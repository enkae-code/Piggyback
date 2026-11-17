"""`piggyback measure-list`: list parsing, slugs, resume and exit codes, with `measure` mocked."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from piggyback import batch
from piggyback.cli import main
from piggyback.measure import MeasureError

OK_URL = "https://93.184.216.34/"  # a public literal: check_url needs no DNS


def write_list(folder, text):
    path = Path(folder) / "list.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run_cli(list_path, out):
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        code = main(["measure-list", list_path, "--out", out])
    return code, printed.getvalue()


class ReadList(unittest.TestCase):
    def test_comments_and_blank_lines_are_ignored(self):
        text = "# head\n\nhttps://93.184.216.34/\n  https://93.184.216.35/  \n# tail\n"
        self.assertEqual(
            batch.read_list(write_list(self.tmp(), text)),
            [(3, "https://93.184.216.34/"), (4, "https://93.184.216.35/")],
        )

    def test_a_duplicate_url_names_both_lines(self):
        text = "https://a.example/\nhttps://b.example/\nhttps://a.example/\n"
        with self.assertRaisesRegex(ValueError, "line 3: same URL as line 1"):
            batch.read_list(write_list(self.tmp(), text))

    def test_more_than_500_urls_names_the_501st_line(self):
        text = "".join(f"https://site{i}.example/\n" for i in range(501))
        with self.assertRaisesRegex(ValueError, "line 501"):
            batch.read_list(write_list(self.tmp(), text))

    def test_exactly_500_urls_are_allowed(self):
        text = "".join(f"https://site{i}.example/\n" for i in range(500))
        self.assertEqual(len(batch.read_list(write_list(self.tmp(), text))), 500)

    def tmp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return folder.name


class Slugs(unittest.TestCase):
    def test_host_and_path_become_lowercase_dashes(self):
        self.assertEqual(
            batch.slug("https://Example.COM/Docs/Page_1.html?x=1"), "example-com-docs-page-1-html"
        )

    def test_traversal_never_survives(self):
        name = batch.slug("https://example.com/../../etc/passwd")
        self.assertEqual(name, "example-com-etc-passwd")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)

    def test_a_long_path_is_capped_at_80_without_a_trailing_dash(self):
        name = batch.slug("https://a.example/" + "x-" * 100)
        self.assertLessEqual(len(name), 80)
        self.assertFalse(name.endswith("-"))

    def test_two_urls_with_one_slug_are_refused_before_anything_is_measured(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("piggyback.measure.measure") as m:
            path = write_list(tmp, "https://a.example/x\nhttps://a.example/x/\n")
            code, _ = run_cli(path, str(Path(tmp) / "out"))
            self.assertEqual(code, 1)
            m.assert_not_called()
            self.assertFalse((Path(tmp) / "out").exists())


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = str(Path(self.tmp.name) / "out")

    def run_list(self, urls, fake):
        path = write_list(self.tmp.name, "\n".join(urls) + "\n")
        with mock.patch("piggyback.measure.measure", side_effect=fake) as m:
            code, printed = run_cli(path, self.out)
        return code, printed, m, json.loads((Path(self.out) / "batch.json").read_text())

    def test_a_failure_does_not_stop_the_rest_and_exits_1(self):
        urls = [OK_URL, "https://93.184.216.35/", "https://93.184.216.36/"]

        def fake(url, **_):
            if url == urls[1]:
                raise MeasureError("page answered HTTP 404 from 93.184.216.35")
            return {"ok": url}

        code, printed, _, records = self.run_list(urls, fake)
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in records], ["measured", "failed", "measured"])
        self.assertTrue((Path(self.out) / "93-184-216-36" / "fingerprint.json").exists())
        self.assertIn("1 failed", printed)

    def test_exit_0_and_batch_json_shape_when_nothing_failed(self):
        code, printed, _, records = self.run_list([OK_URL], lambda url, **_: {"ok": url})
        self.assertEqual(code, 0)
        self.assertEqual(
            records,
            [
                {
                    "url": OK_URL,
                    "dir": str(Path(self.out) / "93-184-216-34"),
                    "status": "measured",
                    "error": None,
                }
            ],
        )
        self.assertIn(f"measured: {OK_URL}", printed)

    def test_an_existing_fingerprint_is_kept_and_never_rewritten(self):
        folder = Path(self.out) / "93-184-216-34"
        folder.mkdir(parents=True)
        (folder / "fingerprint.json").write_text("OLD", encoding="utf-8")
        code, _, m, records = self.run_list([OK_URL], lambda url, **_: {"ok": url})
        self.assertEqual(code, 0)
        self.assertEqual(records[0]["status"], "kept")
        m.assert_not_called()
        self.assertEqual((folder / "fingerprint.json").read_text(encoding="utf-8"), "OLD")

    def test_a_failure_error_is_cleaned_in_batch_json_and_on_screen(self):
        def refuse(url, **_):
            raise MeasureError("page says *bold* <script>")

        code, printed, _, records = self.run_list([OK_URL], refuse)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["error"], r"page says \*bold\* \<script\>")
        self.assertNotIn("<script>", printed)
        self.assertNotIn("*bold*", printed)


if __name__ == "__main__":
    unittest.main()
