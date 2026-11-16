import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import piggyback.measure as measure_mod
from piggyback.cli import main
from piggyback.measure import MeasureError, measure


class MeasureRefusalTests(unittest.TestCase):
    def test_motion_capture_orders_fingerprint_screenshot_then_reduced_motion(self):
        calls = []

        class FakePage:
            def evaluate(self, script):
                calls.append("count" if "playState" in script else "fingerprint")
                return 2 if "playState" in script else {"motion": []}

            def screenshot(self, **kwargs):
                calls.append("screenshot")

            def emulate_media(self, **kwargs):
                calls.append("emulate")

        result = measure_mod._capture_motion(FakePage(), "capture.png")
        self.assertEqual(calls, ["fingerprint", "screenshot", "emulate", "count"])
        self.assertEqual(result["reduced_motion"], {"still_running": 2})

    def test_hint_only_when_loopback_would_be_allowed(self):
        with self.assertRaises(MeasureError) as ctx:
            measure("http://10.0.0.1/")
        self.assertNotIn("--allow-loopback", str(ctx.exception))

    def test_cli_refuses_before_creating_out_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "new"
            with contextlib.redirect_stderr(io.StringIO()):
                code = main(["measure", "http://10.0.0.1/", "--out", str(out)])
            self.assertEqual(code, 1)
            self.assertFalse(out.exists())

    # These refuse before any browser starts, so they run without Playwright.
    def test_loopback_refused_without_flag(self):
        with self.assertRaisesRegex(MeasureError, "--allow-loopback"):
            measure("http://127.0.0.1:8000/")

    def test_non_http_refused(self):
        for url in ("file:///etc/passwd", "data:text/html,<h1>x</h1>", "http://10.0.0.1/"):
            with self.subTest(url=url), self.assertRaises(MeasureError):
                measure(url)


if __name__ == "__main__":
    unittest.main()
