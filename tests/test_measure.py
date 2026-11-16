import unittest

from piggyback.measure import MeasureError, measure


class MeasureRefusalTests(unittest.TestCase):
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
