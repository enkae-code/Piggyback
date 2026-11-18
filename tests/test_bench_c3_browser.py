"""C3 checklist in real Chromium on fixture pages. Skips without Playwright; run on the host.

Predicate and message tests that need no browser live in test_bench_c3.py.
"""

import functools
import http.server
import importlib.util
import socket
import threading
import unittest
from pathlib import Path

if importlib.util.find_spec("playwright") is None:
    raise unittest.SkipTest("playwright not installed; run with the measure extra")

from bench.c3 import checklist  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "c3"
RENDER_ERROR = "boom: script stops the page before any content"


def page(name):
    return str(FIXTURES / name)


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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

    def site_url(self, name, query=""):
        return f"http://127.0.0.1:{self.site.server_address[1]}/{name}{query}"

    def test_passing_page_passes_every_item(self):
        report = self.run_on(page("t01-pass.html"))
        self.assertEqual(report["outcome"], "pass", report["failed"])
        self.assertIsNone(report["correction_message"])
        self.assertEqual([c["pass"] for c in report["checks"]["overflow"]], [True, True])
        self.assertTrue(report["browser_version"])

    def test_loopback_http_url_gives_the_same_result(self):
        self.assertTrue(self.run_on(self.site_url("t01-pass.html"))["pass"])

    def test_missing_table_row_fails_only_the_content_item(self):
        report = self.run_on(page("t01-missing-table.html"))
        self.assertEqual(report["outcome"], "fail")
        self.assertEqual(len(report["failed"]), 1)
        self.assertTrue(
            report["failed"][0].startswith("content item missing: vessel/asset table (")
        )

    def test_wide_element_fails_overflow(self):
        report = self.run_on(page("t01-overflow.html"))
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=1440), report["failed"])
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=390), report["failed"])

    def test_fixed_element_past_the_edge_fails_overflow(self):
        report = self.run_on(page("t01-fixed-overflow.html"))
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=1440), report["failed"])
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=390), report["failed"])

    def test_element_that_grows_after_resize_fails_at_390_once_settled(self):
        report = self.run_on(page("t01-animated-overflow.html"))
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=390), report["failed"])
        self.assertNotIn(checklist.OVERFLOW_ITEM.format(width=1440), report["failed"])

    def test_wide_element_clipped_by_overflow_hidden_passes(self):
        report = self.run_on(page("t01-clipped.html"))
        self.assertTrue(report["pass"], report["failed"])

    def test_console_error_alone_never_fails(self):
        report = self.run_on(page("console-error.html"))
        self.assertTrue(report["pass"], report["failed"])

    def test_page_error_after_render_never_fails_a_rendered_page(self):
        report = self.run_on(page("page-error-with-text.html"))
        self.assertTrue(report["checks"]["render"]["page_errors"])
        self.assertTrue(report["pass"], report["failed"])

    def test_script_error_with_no_content_names_the_first_page_error(self):
        report = self.run_on(page("blocked.html"))
        self.assertFalse(report["checks"]["render"]["pass"])
        self.assertTrue(any(RENDER_ERROR in item for item in report["failed"]), report["failed"])

    def test_axe_violation_fails_and_is_reported(self):
        report = self.run_on(page("axe-image.html"))
        axe = report["checks"]["axe"]
        self.assertEqual(axe["version"], checklist.AXE_VERSION)
        self.assertIn("image-alt", [v["id"] for v in axe["violations"]])
        self.assertFalse(axe["pass"])

    def test_closed_loopback_port_is_no_runnable_output(self):
        report = self.run_on(f"http://127.0.0.1:{closed_port()}/")
        self.assertEqual(report["outcome"], "no_runnable_output")
        self.assertIsNone(report["correction_message"])
        # The proxy answers for the closed port, so the main document has an HTTP error status.
        self.assertGreaterEqual(report["main_document"]["status"], 400, report)

    def test_page_cannot_reach_ws_or_another_loopback_port(self):
        # ws:// goes through the proxy, which must refuse 203.0.113.7 and record it. fetch() to
        # another loopback port is stopped by the route() layer, which records it in the same
        # list. Port 8080 is browser-safe: Chromium refuses port 9 before any connection.
        # <link rel=preconnect> is not asserted: this test does not verify Chromium sends it
        # through the proxy.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(4)
            other = listener.getsockname()[1]
            report = self.run_on(self.site_url("egress-probe.html", f"?port={other}"))
            listener.settimeout(3)
            with self.assertRaises(socket.timeout):
                listener.accept()
        self.assertTrue(report["checks"]["render"]["pass"], report["failed"])
        self.assertTrue(any("203.0.113.7" in b for b in report["blocked_requests"]), report)
        self.assertTrue(any(f"127.0.0.1:{other}" in b for b in report["blocked_requests"]), report)

    def test_fixed_element_escapes_an_overflow_hidden_ancestor(self):
        report = self.run_on(page("t01-fixed-escapes.html"))
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=1440), report["failed"])
        self.assertIn(checklist.OVERFLOW_ITEM.format(width=390), report["failed"])

    def test_absolute_element_clipped_by_its_containing_block_passes(self):
        report = self.run_on(page("t01-absolute-clipped.html"))
        self.assertTrue(report["pass"], report["failed"])

    def test_page_that_blocks_the_browser_is_bounded(self):
        report = checklist.run_bounded(page("t01-hangs.html"), "T01", total_s=20)
        self.assertEqual(report["outcome"], "no_runnable_output")
        self.assertEqual(report["detail"], "checklist timed out")
        self.assertIsNone(report["correction_message"])


if __name__ == "__main__":
    unittest.main()
