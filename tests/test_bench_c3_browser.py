"""C3 checklist in real Chromium on fixture pages. Skips without Playwright; run on the host.

Predicate and message tests that need no browser live in test_bench_c3.py.
"""

import functools
import http.server
import importlib.util
import threading
import unittest
from pathlib import Path

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from bench.c3 import checklist  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "c3"


def page(name):
    return str(FIXTURES / name)


class ChecklistBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
        cls.site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.site.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.site.shutdown()
        cls.site.server_close()

    def run_on(self, target, task="T01"):
        return checklist.run_checklist(target, task)

    def test_passing_page_passes_every_item(self):
        report = self.run_on(page("t01-pass.html"))
        self.assertTrue(report["pass"], report["failed"])
        self.assertIsNone(report["correction_message"])
        self.assertEqual([c["pass"] for c in report["checks"]["overflow"]], [True, True])

    def test_loopback_http_url_gives_the_same_result(self):
        url = f"http://127.0.0.1:{self.site.server_address[1]}/t01-pass.html"
        self.assertTrue(self.run_on(url)["pass"])

    def test_missing_table_row_fails_only_the_content_item(self):
        report = self.run_on(page("t01-missing-table.html"))
        self.assertFalse(report["pass"])
        self.assertEqual(report["failed"], ["content item missing: vessel/asset table"])

    def test_wide_element_fails_overflow(self):
        report = self.run_on(page("t01-overflow.html"))
        self.assertIn("horizontal overflow at 1440 px", report["failed"])
        self.assertIn("horizontal overflow at 390 px", report["failed"])

    def test_script_error_with_no_content_fails_render(self):
        report = self.run_on(page("blocked.html"))
        self.assertFalse(report["checks"]["render"]["pass"])
        self.assertTrue(report["checks"]["render"]["page_errors"])
        self.assertIn(checklist.RENDER_ITEM, report["failed"])

    def test_axe_violation_fails_and_is_reported(self):
        report = self.run_on(page("axe-image.html"))
        axe = report["checks"]["axe"]
        self.assertEqual(axe["version"], checklist.AXE_VERSION)
        self.assertIn("image-alt", [v["id"] for v in axe["violations"]])
        self.assertFalse(axe["pass"])


if __name__ == "__main__":
    unittest.main()
