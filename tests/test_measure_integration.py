import contextlib
import functools
import http.server
import importlib.util
import io
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from piggyback.browser import launch  # noqa: E402
from piggyback.cli import main  # noqa: E402
from piggyback.measure import BYPASS_ENV, MeasureError, measure  # noqa: E402
from piggyback.proxy import EgressProxy  # noqa: E402

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


def lan_address():
    # A UDP connect sends nothing; it only asks the OS which address routes out.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            ip = s.getsockname()[0]
    except OSError:
        return None
    return None if ip.startswith("127.") else ip


RTC_PROBE = """async (target) => {
  const pc = new RTCPeerConnection({iceServers: [
    {urls: `stun:${target}`},
    {urls: `turn:${target}?transport=udp`, username: "u", credential: "p"},
  ]});
  pc.createDataChannel("probe");
  await pc.setLocalDescription(await pc.createOffer());
  await new Promise((done) => setTimeout(done, 3000));
  pc.close();
}"""


def page_udp(browser, target):
    page = browser.new_page()
    page.set_content("<!doctype html><title>rtc</title>")
    page.evaluate(RTC_PROBE, target)


def drain(sock):
    count = 0
    sock.setblocking(False)
    with contextlib.suppress(BlockingIOError):
        while True:
            sock.recv(2048)
            count += 1
    sock.setblocking(True)
    return count


class MeasureIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.other_hits = []
        cls.other = serve(cls.other_hits)
        cls.site_hits = []
        cls.site = serve(cls.site_hits)
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
        # Only the proxy records WebSocket traffic (the route guard never sees it), so this proves
        # the socket went through the proxy. Chromium sends it as an absolute URI or as CONNECT.
        self.assertTrue(any("10.0.0.1:8080" in target for target in result["blocked_requests"]))
        # The proxy answers 403 after recording, so the page's error handler runs after the record.
        self.assertIn("/ws-refused", self.site_hits)

    def test_webrtc_sends_no_udp_to_lan(self):
        # Control first: without the policy the same page must reach the LAN listener, so a
        # silent probe cannot pass the real check.
        lan = lan_address()
        if lan is None:
            self.skipTest("no LAN address on this host")
        from playwright.sync_api import sync_playwright

        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(listener.close)
        listener.bind(("0.0.0.0", 0))
        target = f"{lan}:{listener.getsockname()[1]}"
        with sync_playwright() as p:
            control = p.chromium.launch(chromium_sandbox=True)
            try:
                page_udp(control, target)
            finally:
                control.close()
            control_count = drain(listener)
            egress = EgressProxy(allow_loopback=True)
            self.addCleanup(egress.close)
            with mock.patch.dict(os.environ):
                os.environ.pop(
                    BYPASS_ENV, None
                )  # the worker strips it too: loopback must be proxied
                browser, _ = launch(p, proxy={"server": f"http://127.0.0.1:{egress.port}"})
            try:
                page_udp(browser, target)
            finally:
                browser.close()
            flagged_count = drain(listener)
        self.assertGreater(control_count, 0, "control saw no UDP: the probe measures nothing")
        self.assertEqual(flagged_count, 0, "WebRTC sent UDP to a LAN host")

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
