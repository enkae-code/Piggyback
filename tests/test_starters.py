"""Packaged starters: read from a fixture package, served by the screen and the MCP server.

Only `measure` is stubbed, and it must never be called for a starter. No browser is needed.
"""

import copy
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from piggyback import fingerprint as fp
from piggyback import mcp, server, starters

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"
PNG = b"\x89PNG\r\n\x1a\n" + b"starter"


def fake_measure(url, **_):
    raise AssertionError(f"measure must not run for {url}")


class Packaged(unittest.TestCase):
    """A fixture package with one starter; PACKAGED points at it for the test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "starters"
        folder = root / "northline-ops"
        folder.mkdir(parents=True)
        data = copy.deepcopy(fp.load(FIXTURES / "ref.json"))
        data["url"] = "starter:northline-ops"
        (folder / "fingerprint.json").write_text(json.dumps(data), encoding="utf-8")
        (folder / "screenshot.png").write_bytes(PNG)
        meta = {"id": "northline-ops", "name": "Northline Field Ops", "license": "CC0-1.0"}
        (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        patch = mock.patch.object(starters, "PACKAGED", root)
        patch.start()
        self.addCleanup(patch.stop)


class StarterData(Packaged):
    def test_catalog_lists_packaged_starters_with_name_and_license(self):
        self.assertEqual(
            starters.catalog(),
            [{"id": "northline-ops", "name": "Northline Field Ops", "license": "CC0-1.0"}],
        )

    def test_catalog_is_empty_without_packaged_data(self):
        with mock.patch.object(starters, "PACKAGED", Path(self.tmp.name) / "absent"):
            self.assertEqual(starters.catalog(), [])
            self.assertEqual(starters.listing(), "No starters are packaged.")

    def test_catalog_skips_an_incomplete_folder(self):
        (Path(self.tmp.name) / "starters" / "northline-ops" / "screenshot.png").unlink()
        self.assertEqual(starters.catalog(), [])

    def test_fingerprint_comes_from_the_package(self):
        self.assertEqual(
            starters.fingerprint("starter:northline-ops")["url"], "starter:northline-ops"
        )

    def test_unknown_or_traversing_id_is_refused_with_the_choices(self):
        for ref in ["starter:nope", "starter:../northline-ops", "starter:"]:
            with self.subTest(ref=ref), self.assertRaises(ValueError) as caught:
                starters.check(ref)
            self.assertIn("use one of: northline-ops", str(caught.exception))


class ScreenStarters(Packaged):
    def setUp(self):
        super().setUp()
        self.screen_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.screen_tmp.cleanup)
        self.screen = server.Screen(0, self.screen_tmp.name, self.screen_tmp.name)
        threading.Thread(target=self.screen.serve_forever, daemon=True).start()
        self.addCleanup(self.close)
        measure = mock.patch("piggyback.server.measure", side_effect=fake_measure)
        self.measure = measure.start()
        self.addCleanup(measure.stop)

    def close(self):
        self.screen.shutdown()
        self.screen.server_close()

    def call(self, method, path, body=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.screen.port, timeout=30)
        headers = {"Host": f"127.0.0.1:{self.screen.port}"}
        if token:
            headers["X-Piggyback-Token"] = self.screen.token
        payload = None
        if body is not None:
            payload = json.dumps(body)
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        res = conn.getresponse()
        data = json.loads(res.read())
        conn.close()
        return res.status, data

    def test_starters_api_lists_packaged_starters_with_the_token(self):
        status, data = self.call("GET", "/api/starters")
        self.assertEqual(status, 200)
        self.assertEqual(data[0]["id"], "northline-ops")
        self.assertEqual(self.call("GET", "/api/starters", token=False)[0], 403)

    def test_a_starter_reference_is_measured_from_the_package_only(self):
        status, data = self.call(
            "POST", "/api/measure", {"references": ["starter:northline-ops"], "build": None}
        )
        self.assertEqual(status, 200, data)
        self.assertEqual(data["references"][0]["url"], "starter:northline-ops")
        self.measure.assert_not_called()

    def test_a_starter_screenshot_is_copied_so_the_package_is_never_deleted(self):
        for _ in range(2):  # the second run drops the first run's captures
            status, _ = self.call(
                "POST", "/api/measure", {"references": ["starter:northline-ops"], "build": None}
            )
            self.assertEqual(status, 200)
        self.assertTrue((starters.PACKAGED / "northline-ops" / "screenshot.png").is_file())

    def test_unknown_starter_is_a_400_that_names_the_choices(self):
        status, data = self.call(
            "POST", "/api/measure", {"references": ["starter:nope"], "build": None}
        )
        self.assertEqual(status, 400)
        self.assertIn("use one of: northline-ops", data["error"])

    def test_a_loopback_url_is_still_refused_as_a_reference(self):
        status, data = self.call(
            "POST", "/api/measure", {"references": ["http://127.0.0.1:9/"], "build": None}
        )
        self.assertEqual(status, 400)
        self.assertIn("not a public http(s) address", data["error"])
        self.measure.assert_not_called()


class McpStarters(Packaged):
    def setUp(self):
        super().setUp()
        self.page = mock.Mock(side_effect=fake_measure)

    def session(self, refs):
        return mcp.Session(refs, None, measure_page=self.page)

    def test_list_starters_is_a_read_only_tool_without_arguments(self):
        tools = {tool["name"]: tool for tool in mcp.TOOLS}
        self.assertEqual(tools["list_starters"]["inputSchema"]["properties"], {})
        self.assertIs(tools["list_starters"]["inputSchema"]["additionalProperties"], False)
        text = mcp._call(self.session([]), {"name": "list_starters", "arguments": {}})
        self.assertIn(
            "starter:northline-ops: Northline Field Ops (CC0-1.0)", text["content"][0]["text"]
        )

    def test_list_starters_refuses_a_url_argument(self):
        with self.assertRaises(mcp.RpcError) as caught:
            mcp._call(
                self.session([]), {"name": "list_starters", "arguments": {"url": "http://x/"}}
            )
        self.assertEqual(caught.exception.code, mcp.BAD_PARAMS)

    def test_a_starter_reference_is_read_from_the_package_not_fetched(self):
        session = self.session(["starter:northline-ops"])
        text = mcp._call(session, {"name": "get_brief", "arguments": {}})
        self.assertFalse(text["isError"])
        self.page.assert_not_called()

    def test_an_unknown_starter_is_a_tool_error_with_the_choices(self):
        session = self.session(["starter:nope"])
        text = mcp._call(session, {"name": "get_brief", "arguments": {}})
        self.assertTrue(text["isError"])
        self.assertIn("use one of: northline-ops", text["content"][0]["text"])
        self.page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
