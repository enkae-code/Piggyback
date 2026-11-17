"""Local screen server: real ephemeral server and HTTP client. Only `measure` is stubbed."""

import contextlib
import copy
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from piggyback import defaults as defaults_mod
from piggyback import fingerprint as fp
from piggyback import server
from piggyback.measure import MeasureError

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"
PNG = b"\x89PNG\r\n\x1a\n" + b"stub"


def fake_measure(url, *, allow_loopback=False, screenshot=None, **_):
    data = fp.load(FIXTURES / ("build.json" if allow_loopback else "ref.json"))
    Path(str(screenshot)).write_bytes(PNG)
    return {**data, "url": url, "screenshot": str(screenshot)}


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.screen = server.Screen(0, self.tmp.name, self.tmp.name)
        threading.Thread(target=self.screen.serve_forever, daemon=True).start()
        self.addCleanup(self.close)
        self.measure = mock.patch("piggyback.server.measure", side_effect=fake_measure).start()
        self.addCleanup(mock.patch.stopall)

    def close(self):
        self.screen.shutdown()
        self.screen.server_close()
        self.tmp.cleanup()

    def call(
        self,
        method,
        path,
        body=None,
        *,
        raw=None,
        token=True,
        host=None,
        origin=None,
        ctype="application/json",
    ):
        conn = http.client.HTTPConnection("127.0.0.1", self.screen.port, timeout=30)
        headers = {"Host": host or f"127.0.0.1:{self.screen.port}"}
        if token:
            headers["X-Piggyback-Token"] = self.screen.token
        if origin:
            headers["Origin"] = origin
        payload = raw if raw is not None else (None if body is None else json.dumps(body))
        if payload is not None:
            headers["Content-Type"] = ctype
        conn.request(method, path, body=payload, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, dict(res.getheaders()), data

    def post_measure(self, body, **kwargs):
        status, _, data = self.call("POST", "/api/measure", body, **kwargs)
        return status, json.loads(data)

    def test_page_is_served_without_token_with_strict_headers(self):
        status, headers, data = self.call("GET", "/", token=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Piggyback</title>", data)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("object-src 'none'", headers["Content-Security-Policy"])

    def test_unknown_static_path_is_not_found(self):
        self.assertEqual(self.call("GET", "/../server.py", token=False)[0], 404)

    def test_api_without_token_is_refused(self):
        status, _, _ = self.call("POST", "/api/measure", {"references": []}, token=False)
        self.assertEqual(status, 403)

    def test_api_with_wrong_token_is_refused(self):
        self.screen.token = "right-token"
        conn = http.client.HTTPConnection("127.0.0.1", self.screen.port, timeout=10)
        conn.request(
            "POST",
            "/api/measure",
            body="{}",
            headers={
                "Host": f"127.0.0.1:{self.screen.port}",
                "X-Piggyback-Token": "wrong",
                "Content-Type": "application/json",
            },
        )
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()

    def test_bad_host_is_refused_dns_rebinding(self):
        status, _, _ = self.call(
            "GET", "/", token=False, host="evil.example:" + str(self.screen.port)
        )
        self.assertEqual(status, 403)

    def test_localhost_host_is_accepted(self):
        host = f"localhost:{self.screen.port}"
        self.assertEqual(self.call("GET", "/", token=False, host=host)[0], 200)

    def test_cross_origin_is_refused(self):
        status, _, _ = self.call("GET", "/", token=False, origin="http://evil.example")
        self.assertEqual(status, 403)

    def test_same_origin_is_accepted(self):
        origin = f"http://127.0.0.1:{self.screen.port}"
        self.assertEqual(self.call("GET", "/", token=False, origin=origin)[0], 200)

    def test_body_must_be_json(self):
        status, _, _ = self.call("POST", "/api/measure", raw="{}", ctype="text/plain")
        self.assertEqual(status, 415)

    def test_oversized_body_is_refused(self):
        raw = "{" + " " * server.MAX_BODY + "}"
        self.assertEqual(self.call("POST", "/api/measure", raw=raw)[0], 413)

    def test_invalid_json_and_non_object_are_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/measure", raw="{nope")[0], 400)
        self.assertEqual(self.call("POST", "/api/measure", raw="[1]")[0], 400)

    def test_reference_loopback_is_refused_before_any_work(self):
        status, data = self.post_measure({"references": ["http://127.0.0.1:9/"]})
        self.assertEqual(status, 400)
        self.assertIn("public", data["error"])
        self.measure.assert_not_called()

    def test_build_loopback_is_allowed_and_reference_is_not(self):
        status, data = self.post_measure(
            {"references": ["https://example.com"], "build": "http://localhost:3000"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "compare")
        flags = [call.kwargs["allow_loopback"] for call in self.measure.call_args_list]
        self.assertEqual(flags, [False, True])

    def test_public_build_gets_no_loopback_reach(self):
        self.assertEqual(self.post_measure({"build": "https://example.com/"})[0], 200)
        self.assertEqual(self.measure.call_args.kwargs["allow_loopback"], False)

    def test_private_build_is_refused_before_any_work(self):
        status, _ = self.post_measure(
            {"references": ["https://example.com"], "build": "http://10.0.0.1/"}
        )
        self.assertEqual(status, 400)
        self.measure.assert_not_called()

    def test_build_name_resolving_to_loopback_is_refused_with_a_screen_hint(self):
        # Only the policy's resolver is faked; the test client still resolves normally.
        with mock.patch("piggyback.netpolicy.socket") as fake:
            fake.getaddrinfo.return_value = [(2, 1, 6, "", ("127.0.0.1", 0))]
            status, data = self.post_measure({"build": "http://lvh.me:3000/"})
        self.assertEqual(status, 400)
        self.assertIn("localhost or 127.0.0.1", data["error"])
        self.measure.assert_not_called()

    def test_overlong_url_is_refused(self):
        url = "https://example.com/" + "a" * server.MAX_URL
        self.assertEqual(self.post_measure({"references": [url]})[0], 400)
        self.measure.assert_not_called()

    def test_null_origin_is_refused(self):
        self.assertEqual(self.call("GET", "/", token=False, origin="null")[0], 403)

    def test_unexpected_error_is_a_500_not_a_dropped_connection(self):
        self.measure.side_effect = KeyError("boom")
        status, data = self.post_measure({"references": ["https://example.com"]})
        self.assertEqual(status, 500)
        self.assertNotIn("boom", data["error"])
        self.assertTrue(self.screen.busy.acquire(blocking=False))
        self.screen.busy.release()

    def test_brief_refuses_to_write_through_a_symlink(self):
        self.post_measure({"references": ["https://example.com"]})
        target = Path(self.tmp.name, "elsewhere.txt")
        Path(self.tmp.name, "BRIEF.md").symlink_to(target)
        self.assertEqual(self.call("POST", "/api/brief", {})[0], 409)
        self.assertFalse(target.exists())

    def test_reference_only_is_a_brief_with_no_paths_in_the_payload(self):
        status, data = self.post_measure({"references": ["https://example.com"]})
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "brief")
        shot = data["references"][0]["shot"]
        self.assertRegex(shot, r"^[0-9a-f]{32}$")
        self.assertNotIn(self.tmp.name, json.dumps(data))

    def test_build_only_is_defaults(self):
        status, data = self.post_measure({"build": "http://localhost:3000"})
        self.assertEqual(status, 200)
        self.assertEqual(data["state"], "defaults")
        self.assertTrue(data["items"])

    def test_build_localhost_is_measured_as_loopback_ip(self):
        self.post_measure({"build": "http://localhost:3000/app?x=1"})
        self.assertEqual(self.measure.call_args.args[0], "http://127.0.0.1:3000/app?x=1")

    def test_no_slots_is_refused(self):
        self.assertEqual(self.post_measure({"references": []})[0], 400)

    def test_more_than_three_references_is_refused(self):
        self.assertEqual(self.post_measure({"references": ["https://a.example"] * 4})[0], 400)

    def test_screenshot_served_by_opaque_id_only(self):
        _, data = self.post_measure({"references": ["https://example.com"]})
        shot = data["references"][0]["shot"]
        status, headers, body = self.call("GET", f"/api/shot/{shot}.png")
        self.assertEqual((status, headers["Content-Type"], body), (200, "image/png", PNG))
        self.assertEqual(self.call("GET", f"/api/shot/{'0' * 32}.png")[0], 404)
        self.assertEqual(self.call("GET", "/api/shot/..%2F..%2Fetc%2Fpasswd.png")[0], 404)

    def test_shot_without_token_is_refused(self):
        self.assertEqual(self.call("GET", f"/api/shot/{'0' * 32}.png", token=False)[0], 403)

    def test_measure_error_is_reported_not_crashed(self):
        self.measure.side_effect = MeasureError("browser did not start")
        status, data = self.post_measure({"references": ["https://example.com"]})
        self.assertEqual(status, 422)
        self.assertEqual(data["error"], "browser did not start")

    def test_second_measure_while_busy_gets_429(self):
        self.screen.busy.acquire()
        self.addCleanup(self.screen.busy.release)
        self.assertEqual(self.post_measure({"build": "http://localhost:3000"})[0], 429)

    def use_build(self, build):
        """Reference is the fixture; the loopback build is the given fingerprint."""

        def measure_pages(url, *, allow_loopback=False, screenshot=None, **_):
            Path(str(screenshot)).write_bytes(PNG)
            data = build if allow_loopback else fp.load(FIXTURES / "ref.json")
            return {**data, "url": url, "screenshot": str(screenshot)}

        self.measure.side_effect = measure_pages

    def test_a_pin_ticks_only_while_its_element_still_compares(self):
        ref = fp.load(FIXTURES / "ref.json")
        build = fp.load(FIXTURES / "build.json")
        # Run 1: the nav is on both pages and differs. (The build fixture has no nav at all.)
        build["anchors"]["nav"] = {**ref["anchors"]["nav"], "font_size": "20px"}
        self.use_build(build)
        body = {"references": ["https://example.com"], "build": "http://localhost:3000"}
        _, first = self.post_measure(body)
        self.assertIn("nav_type", {pin["concept"] for pin in first["pins"]})
        # Run 2: the heading now matches and the nav has vanished from the build.
        build = copy.deepcopy(build)
        build["anchors"]["heading"] = copy.deepcopy(ref["anchors"]["heading"])
        del build["anchors"]["nav"]
        self.use_build(build)
        status, second = self.post_measure({**body, "rerun": True})
        self.assertEqual(status, 200)
        self.assertEqual([pin["concept"] for pin in second["ticked"]], ["heading_type"])

    def test_a_tick_is_dropped_once_its_element_vanishes(self):
        ref = fp.load(FIXTURES / "ref.json")
        build = fp.load(FIXTURES / "build.json")
        body = {"references": ["https://example.com"], "build": "http://localhost:3000"}
        self.use_build(build)
        self.post_measure(body)  # run 1: the heading differs
        build = copy.deepcopy(build)
        build["anchors"]["heading"] = copy.deepcopy(ref["anchors"]["heading"])
        self.use_build(build)
        _, second = self.post_measure({**body, "rerun": True})
        self.assertEqual([pin["concept"] for pin in second["ticked"]], ["heading_type"])
        del build["anchors"]["heading"]  # run 3: the heading vanishes; its tick must go too
        self.use_build(build)
        _, third = self.post_measure({**body, "rerun": True})
        self.assertEqual(third["ticked"], [])

    def test_a_measured_reference_drops_the_last_compare(self):
        self.screen.last_compare = {"state": "compare", "pins": [], "ticked": [{"concept": "x"}]}
        self.screen._measure_references(["https://example.com"])
        self.assertIsNone(self.screen.last_compare)

    def test_a_rerun_reuses_the_reference_capture_until_the_list_changes(self):
        body = {"references": ["https://example.com"], "build": "http://localhost:3000"}

        def ref_measures():
            return sum(not call.kwargs["allow_loopback"] for call in self.measure.call_args_list)

        _, first = self.post_measure(body)
        ref_shot = f"/api/shot/{first['reference']['shot']}.png"
        self.assertEqual(self.call("GET", ref_shot)[0], 200)
        self.post_measure({**body, "rerun": True})
        self.assertEqual(ref_measures(), 1)  # the rerun measured only the build
        self.assertEqual(self.call("GET", ref_shot)[0], 200)  # the kept capture still serves
        self.post_measure(body)  # a fresh measure measures the reference again
        self.assertEqual(ref_measures(), 2)
        other = {"references": ["https://example.org"], "build": body["build"], "rerun": True}
        self.post_measure(other)  # a changed list is measured again
        self.assertEqual(ref_measures(), 3)

    def test_brief_is_never_overwritten(self):
        self.post_measure({"references": ["https://example.com"]})
        self.assertEqual(self.call("POST", "/api/brief", {})[0], 200)
        status, _, data = self.call("POST", "/api/brief", {})
        body = json.loads(data)
        self.assertEqual(status, 409)
        self.assertIn("BRIEF.md already exists", body["error"])
        self.assertEqual(body["text"], self.screen.last_text)  # the page can still copy it

    def test_brief_needs_a_measure_first_then_writes_the_file(self):
        self.assertEqual(self.call("POST", "/api/brief", {})[0], 409)
        self.post_measure({"references": ["https://example.com"]})
        status, _, data = self.call("POST", "/api/brief", {})
        self.assertEqual(status, 200)
        body = json.loads(data)
        written = Path(self.tmp.name, "BRIEF.md").read_text(encoding="utf-8")
        self.assertEqual(written, body["text"])
        self.assertTrue(written.startswith("# Design brief"))

    def test_build_is_shown_as_typed_and_visuals_come_from_measured_values(self):
        _, data = self.post_measure(
            {"references": ["https://example.com"], "build": "http://localhost:3000"}
        )
        self.assertEqual(data["build"]["url"], "http://localhost:3000")
        _, brief = self.post_measure({"references": ["https://example.com"]})
        visuals = [line["visual"] for line in brief["references"][0]["lines"]]
        kinds = {visual["kind"] for visual in visuals if visual}
        self.assertTrue({"swatches", "sizes", "radii"} <= kinds)

    def test_compare_result_names_pins_and_not_found_once_per_concept(self):
        _, data = self.post_measure(
            {"references": ["https://example.com"], "build": "http://localhost:3000"}
        )
        numbers = [pin["number"] for pin in data["pins"]]
        self.assertEqual(numbers, sorted(numbers))
        whys = [pin["why"] for pin in data["pins"] if pin["why"]]
        self.assertEqual(len(whys), len({pin["concept"] for pin in data["pins"]}))
        self.assertTrue(all(isinstance(text, str) for text in data["not_found"]))

    def clear_build(self):
        build = fp.load(FIXTURES / "build.json")
        build["effects"] = {"gradient_text": 0, "tinted_icon_squares": 0}
        build["motion"] = []
        return build

    def test_compare_carries_defaults_shaped_like_the_build_only_items(self):
        body = {"references": ["https://example.com"], "build": "http://localhost:3000"}
        self.use_build(fp.load(FIXTURES / "build.json"))
        _, data = self.post_measure(body)
        _, build_only = self.post_measure({"build": body["build"]})
        self.assertEqual(data["defaults"], build_only["items"])
        self.assertEqual([item["number"] for item in data["defaults"]], [1, 2, 3])
        self.use_build(self.clear_build())
        _, clear = self.post_measure(body)
        self.assertEqual(clear["defaults"], [])

    def test_compare_brief_names_defaults_only_when_the_build_has_hits(self):
        body = {"references": ["https://example.com"], "build": "http://localhost:3000"}
        self.use_build(fp.load(FIXTURES / "build.json"))
        self.post_measure(body)
        self.assertIn("## Defaults detected", self.screen.last_text or "")
        self.use_build(self.clear_build())
        self.post_measure(body)
        self.assertNotIn(
            "efaults detected", self.screen.last_text or ""
        )  # also catches "No generic defaults detected."

    def test_build_only_payload_and_text_are_unchanged(self):
        build = {**fp.load(FIXTURES / "build.json"), "url": "http://127.0.0.1:9/", "shot": "a" * 32}
        payload, text = server._render([], build)
        self.assertEqual(set(payload), {"state", "build", "items"})
        self.assertEqual(
            [item["concept"] for item in payload["items"]],
            ["gradient_text", "repeated_motion", "icon_tile"],
        )
        self.assertEqual(text, defaults_mod.render(defaults_mod.find(build)))


class OpenCommandTests(unittest.TestCase):
    def test_no_command_and_open_both_start_the_screen(self):
        from piggyback.cli import main

        for argv in ([], ["open", "--port", "8123", "--out", "/tmp/x"]):
            with self.subTest(argv=argv), mock.patch("piggyback.server.serve") as serve:
                self.assertEqual(main(argv), 0)
                serve.assert_called_once()

    def test_port_in_use_is_a_clean_error(self):
        from piggyback.cli import main

        err = io.StringIO()
        with mock.patch("piggyback.server.serve", side_effect=OSError(98, "Address in use")):
            with contextlib.redirect_stderr(err):
                self.assertEqual(main(["open"]), 1)
        self.assertIn("Address in use", err.getvalue())


if __name__ == "__main__":
    unittest.main()
