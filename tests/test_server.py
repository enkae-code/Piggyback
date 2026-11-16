"""Local screen server: real ephemeral server and HTTP client. Only `measure` is stubbed."""

import contextlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

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
