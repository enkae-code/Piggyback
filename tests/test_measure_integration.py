import contextlib
import functools
import http.server
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from piggyback.cli import main  # noqa: E402
from piggyback.measure import MeasureError, measure  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def serve(hits=None):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if hits is not None:
                hits.append(self.path)
            if self.path == "/bounce":
                self.send_response(302)
                self.send_header("Location", "http://10.0.0.1/")
                self.end_headers()
                return
            super().do_GET()

    handler = functools.partial(Handler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class MeasureIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.other_hits = []
        cls.other = serve(cls.other_hits)
        cls.site = serve()
        cls.base = f"http://127.0.0.1:{cls.site.server_address[1]}"
        cls.other_port = cls.other.server_address[1]

    @classmethod
    def tearDownClass(cls):
        for server in (cls.site, cls.other):
            server.shutdown()
            server.server_close()

    def test_index_fingerprint_and_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            shot = Path(tmp) / "shot.png"
            url = f"{self.base}/index.html?other={self.other_port}"
            result = measure(url, allow_loopback=True, screenshot=shot)
            self.assertEqual(result["anchors"]["heading"]["tag"], "h1")
            self.assertEqual(result["anchors"]["heading"]["font_size"], "40px")
            self.assertEqual(result["anchors"]["cta"]["tag"], "button")
            self.assertEqual(result["anchors"]["cta"]["radius"], "8px")
            self.assertIn("8px", [r["value"] for r in result["radius"]])
            self.assertEqual(result["anchors"]["card"]["count"], 3)
            self.assertTrue(any(m["duration"] == 1000 for m in result["motion"]))
            self.assertTrue(shot.read_bytes().startswith(b"\x89PNG"))
            # Loopback sibling reached (flag on); 10.0.0.1 aborted before it left the browser.
            self.assertTrue(any(p == "/" for p in self.other_hits))
            self.assertIn("http://10.0.0.1/", result["blocked_requests"])
            self.assertFalse(any(f":{self.other_port}" in u for u in result["blocked_requests"]))

    def test_missing_anchor_is_null(self):
        result = measure(f"{self.base}/plain.html", allow_loopback=True)
        self.assertIsNone(result["anchors"]["cta"])
        self.assertIsNone(result["anchors"]["card"])
        self.assertEqual(result["anchors"]["heading"]["font_size"], "32px")

    def test_private_address_refused_without_flag(self):
        with self.assertRaises(MeasureError):
            measure(f"http://127.0.0.1:{self.site.server_address[1]}/")

    def test_redirect_into_private_range_is_aborted(self):
        result = measure(f"{self.base}/redirect.html", allow_loopback=True)
        self.assertIn("http://10.0.0.1/", result["blocked_requests"])

    def test_websocket_to_private_range_is_refused(self):
        result = measure(f"{self.base}/socket.html", allow_loopback=True)
        # Chromium may send ws:// as an absolute URI or as CONNECT; both name the target.
        self.assertTrue(any("10.0.0.1:1" in target for target in result["blocked_requests"]))

    def test_cli_measure_writes_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(
                    ["measure", f"{self.base}/plain.html", "--out", tmp, "--allow-loopback"]
                )
            self.assertEqual(code, 0)
            data = json.loads((Path(tmp) / "fingerprint.json").read_text(encoding="utf-8"))
            self.assertTrue(data["captured_with"].startswith("piggyback "))
            self.assertTrue((Path(tmp) / "screenshot.png").exists())

    def test_doctor_reports_sandboxed_browser(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["doctor"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("starts with sandbox", out.getvalue())


if __name__ == "__main__":
    unittest.main()
