"""Browser tests for the C1 evidence hook. Skipped without Playwright; Hermes runs them."""

import contextlib
import http.server
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from bench.c1.snapshot import Snapshot  # noqa: E402
from piggyback.measure import measure  # noqa: E402

PAGE = b"""<!doctype html><html><head><title>C1 fixture</title></head><body>
<h1 style="font-size:48px;margin:0">Fixture heading</h1>
<p>This paragraph has more than forty characters of plain body text for the snapshot.</p>
<button style="background:#0a5;padding:12px">Go now</button>
</body></html>"""


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)


class EvidenceHook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_evidence_written_and_fingerprint_unchanged_when_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "evidence.json"
            with_ev = measure(self.url, allow_loopback=True, evidence=evidence)
            without = measure(self.url, allow_loopback=True)
            self.assertEqual(
                {k: v for k, v in with_ev.items() if k not in ("captured_at", "capture")},
                {k: v for k, v in without.items() if k not in ("captured_at", "capture")},
            )
            data = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(data["page"]["title"], "C1 fixture")
            snap = Snapshot(data)
            heading = next(e for e in snap.elements() if e.tag == "h1")
            self.assertEqual(heading.style["font-size"], "48px")
            self.assertEqual(snap.inner_text(heading.idx), "Fixture heading")


if __name__ == "__main__":
    with contextlib.suppress(SystemExit):
        unittest.main()
