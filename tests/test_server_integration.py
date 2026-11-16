"""Local screen against the real engine: real measure() and Chromium, on a host with the browser.

Reference URLs must be public, so the compare test reads PIGGYBACK_TEST_REFERENCE_URL and is
skipped without it. The build-only test needs no network: it serves the loopback fixtures.
"""

import functools
import http.client
import http.server
import importlib.util
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from piggyback import server  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class ServerIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
        cls.site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.screen = server.Screen(0, cls.tmp.name, cls.tmp.name)
        threading.Thread(target=cls.screen.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.screen.shutdown()
        cls.screen.server_close()
        cls.site.shutdown()
        cls.site.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.screen.port, timeout=200)
        headers = {"X-Piggyback-Token": self.screen.token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, data

    def test_build_only_measures_the_loopback_fixture_and_serves_its_screenshot(self):
        build = f"http://127.0.0.1:{self.site.server_address[1]}/index.html"
        status, data = self.call("POST", "/api/measure", {"build": build})
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertEqual(result["state"], "defaults")
        self.assertEqual(result["build"]["url"], build)
        status, png = self.call("GET", f"/api/shot/{result['build']['shot']}.png")
        self.assertEqual(status, 200)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_loopback_reference_is_refused_by_the_real_policy(self):
        status, data = self.call(
            "POST",
            "/api/measure",
            {"references": [f"http://127.0.0.1:{self.site.server_address[1]}/index.html"]},
        )
        self.assertEqual(status, 400, data)

    @unittest.skipUnless(os.environ.get("PIGGYBACK_TEST_REFERENCE_URL"), "needs a public URL")
    def test_reference_and_build_compare_with_pins(self):
        build = f"http://127.0.0.1:{self.site.server_address[1]}/index.html"
        status, data = self.call(
            "POST",
            "/api/measure",
            {"references": [os.environ["PIGGYBACK_TEST_REFERENCE_URL"]], "build": build},
        )
        self.assertEqual(status, 200, data)
        result = json.loads(data)
        self.assertEqual(result["state"], "compare")
        self.assertEqual(self.call("POST", "/api/brief", {})[0], 200)


if __name__ == "__main__":
    unittest.main()
