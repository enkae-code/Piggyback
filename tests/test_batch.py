"""`piggyback measure-list`: list parsing, slugs, resume, links, failures and exit codes, with
`measure` mocked. The fake result carries the requested URL as the real measure does."""

import contextlib
import io
import json
import os
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
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def run_cli(list_path, out):
    with contextlib.redirect_stdout(io.StringIO()) as printed:
        code = main(["measure-list", list_path, "--out", out])
    return code, printed.getvalue()


def fake_ok(url, **_):
    return {"url": url}


class ReadList(unittest.TestCase):
    def test_comments_and_blank_lines_are_ignored(self):
        text = "# head\n\nhttps://93.184.216.34/\n  https://93.184.216.35/  \n# tail\n"
        self.assertEqual(
            batch.read_list(write_list(self.tmp(), text)),
            [(3, "https://93.184.216.34/"), (4, "https://93.184.216.35/")],
        )

    def test_a_byte_order_mark_is_not_part_of_the_first_url(self):
        text = "\ufeffhttps://93.184.216.34/\n"
        self.assertEqual(
            batch.read_list(write_list(self.tmp(), text)), [(1, "https://93.184.216.34/")]
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

    def test_the_port_is_part_of_the_folder_name(self):
        self.assertEqual(batch.slug("http://127.0.0.1:8797/x"), "127-0-0-1-8797-x")
        self.assertNotEqual(
            batch.slug("http://127.0.0.1:8797/x"), batch.slug("http://127.0.0.1:8798/x")
        )

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
            return {"url": url}

        code, printed, _, records = self.run_list(urls, fake)
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in records], ["measured", "failed", "measured"])
        self.assertTrue((Path(self.out) / "93-184-216-36" / "fingerprint.json").exists())
        self.assertIn("1 failed", printed)

    def test_records_keep_list_order(self):
        urls = [OK_URL, "https://93.184.216.35/", "https://93.184.216.36/"]
        _, _, _, records = self.run_list(urls, fake_ok)
        self.assertEqual([r["url"] for r in records], urls)

    def test_exit_0_and_batch_json_shape_when_nothing_failed(self):
        code, printed, _, records = self.run_list([OK_URL], fake_ok)
        self.assertEqual(code, 0)
        self.assertEqual(
            records,
            [{"url": OK_URL, "dir": "93-184-216-34", "status": "measured", "error": None}],
        )
        self.assertIn(f"measured: {OK_URL}", printed)

    def test_two_ports_on_one_host_get_two_folders(self):
        urls = ["https://93.184.216.34:8797/x", "https://93.184.216.34:8798/x"]
        code, _, _, records = self.run_list(urls, fake_ok)
        self.assertEqual(code, 0)
        self.assertEqual(
            [r["dir"] for r in records], ["93-184-216-34-8797-x", "93-184-216-34-8798-x"]
        )

    def test_an_existing_fingerprint_for_the_same_url_is_kept_and_never_rewritten(self):
        folder = Path(self.out) / "93-184-216-34"
        folder.mkdir(parents=True)
        old = json.dumps({"url": OK_URL, "old": True})
        (folder / "fingerprint.json").write_text(old, encoding="utf-8")
        code, _, m, records = self.run_list([OK_URL], fake_ok)
        self.assertEqual(code, 0)
        self.assertEqual(records[0]["status"], "kept")
        m.assert_not_called()
        self.assertEqual((folder / "fingerprint.json").read_text(encoding="utf-8"), old)

    def test_a_fingerprint_for_another_query_is_not_kept(self):
        # The query is not in the folder name, so both runs share one folder.
        folder = Path(self.out) / "93-184-216-34"
        folder.mkdir(parents=True)
        old = json.dumps({"url": OK_URL + "?a=1"})
        (folder / "fingerprint.json").write_text(old, encoding="utf-8")
        code, _, m, records = self.run_list([OK_URL + "?a=2"], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertIn("another URL", records[0]["error"])
        m.assert_not_called()
        self.assertEqual((folder / "fingerprint.json").read_text(encoding="utf-8"), old)

    def test_a_fingerprint_for_another_port_is_not_kept(self):
        folder = Path(self.out) / "93-184-216-34-8797-x"
        folder.mkdir(parents=True)
        (folder / "fingerprint.json").write_text(
            json.dumps({"url": "https://93.184.216.34:8798/x"}), encoding="utf-8"
        )
        _, _, m, records = self.run_list(["https://93.184.216.34:8797/x"], fake_ok)
        self.assertEqual(records[0]["status"], "failed")
        m.assert_not_called()

    def test_a_truncated_fingerprint_fails_and_is_not_rewritten(self):
        folder = Path(self.out) / "93-184-216-34"
        folder.mkdir(parents=True)
        (folder / "fingerprint.json").write_text('{"url": "https://93', encoding="utf-8")
        code, _, m, records = self.run_list([OK_URL], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertIn("not valid JSON", records[0]["error"])
        m.assert_not_called()
        self.assertEqual(
            (folder / "fingerprint.json").read_text(encoding="utf-8"), '{"url": "https://93'
        )

    def test_a_failure_error_is_cleaned_in_batch_json_and_on_screen(self):
        def refuse(url, **_):
            raise MeasureError("page says *bold* <script>")

        code, printed, _, records = self.run_list([OK_URL], refuse)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["error"], r"page says \*bold\* \<script\>")
        self.assertNotIn("<script>", printed)
        self.assertNotIn("*bold*", printed)

    def test_a_regular_file_at_the_folder_path_fails_only_that_url(self):
        Path(self.out).mkdir(parents=True)
        (Path(self.out) / "93-184-216-34").write_text("not a folder", encoding="utf-8")
        code, _, m, records = self.run_list([OK_URL, "https://93.184.216.35/"], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in records], ["failed", "measured"])
        self.assertEqual(m.call_count, 1)

    def test_a_malformed_url_fails_alone_and_the_rest_still_run(self):
        code, _, _, records = self.run_list(["http://[broken/", OK_URL], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in records], ["failed", "measured"])
        self.assertIsNone(records[0]["dir"])
        self.assertEqual(records[1]["dir"], "93-184-216-34")

    def test_a_line_over_2048_characters_fails_alone(self):
        long_line = "https://93.184.216.35/" + "a" * 2100
        _, _, _, records = self.run_list([long_line, OK_URL], fake_ok)
        self.assertEqual([r["status"] for r in records], ["failed", "measured"])
        self.assertIn("longer than 2048", records[0]["error"])

    def test_escape_and_bell_in_a_list_line_never_reach_the_terminal(self):
        hostile = "https://93.184.216.35/\x1b]0;evil\x07\x1b[2J"
        code, printed, _, records = self.run_list([hostile, OK_URL], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual([r["status"] for r in records], ["failed", "measured"])
        self.assertNotIn("\x1b", printed)
        self.assertNotIn("\x07", printed)
        self.assertIn("\\x1b", printed)
        self.assertEqual(records[0]["url"], hostile)

    def test_a_symlinked_slug_folder_fails_and_writes_nothing_outside(self):
        external = Path(self.tmp.name) / "external"
        external.mkdir()
        Path(self.out).mkdir(parents=True)
        os.symlink(external, Path(self.out) / "93-184-216-34", target_is_directory=True)
        code, _, m, records = self.run_list([OK_URL], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(list(external.iterdir()), [])
        m.assert_not_called()

    def test_a_symlinked_fingerprint_fails_and_the_target_is_untouched(self):
        folder = Path(self.out) / "93-184-216-34"
        folder.mkdir(parents=True)
        target = Path(self.tmp.name) / "external.json"
        target.write_text("EXTERNAL", encoding="utf-8")
        os.symlink(target, folder / "fingerprint.json")
        code, _, m, records = self.run_list([OK_URL], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["status"], "failed")
        self.assertIn("symbolic link", records[0]["error"])
        m.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "EXTERNAL")

    def test_a_symlinked_screenshot_fails_and_the_target_is_untouched(self):
        folder = Path(self.out) / "93-184-216-34"
        folder.mkdir(parents=True)
        target = Path(self.tmp.name) / "external.png"
        target.write_bytes(b"EXTERNAL")
        os.symlink(target, folder / "screenshot.png")
        code, _, m, records = self.run_list([OK_URL], fake_ok)
        self.assertEqual(code, 1)
        self.assertEqual(records[0]["status"], "failed")
        m.assert_not_called()
        self.assertEqual(target.read_bytes(), b"EXTERNAL")

    def test_a_symlinked_batch_json_refuses_the_run_before_any_url(self):
        Path(self.out).mkdir(parents=True)
        target = Path(self.tmp.name) / "external.json"
        target.write_text("EXTERNAL", encoding="utf-8")
        os.symlink(target, Path(self.out) / "batch.json")
        path = write_list(self.tmp.name, OK_URL + "\n")
        with mock.patch("piggyback.measure.measure", side_effect=fake_ok) as m:
            code, _ = run_cli(path, self.out)
        self.assertEqual(code, 1)
        m.assert_not_called()
        self.assertEqual(target.read_text(encoding="utf-8"), "EXTERNAL")

    def test_a_batch_json_write_failure_is_a_message_and_exit_1(self):
        Path(self.out, "batch.json").mkdir(parents=True)
        path = write_list(self.tmp.name, OK_URL + "\n")
        err = io.StringIO()
        with (
            mock.patch("piggyback.measure.measure", side_effect=fake_ok),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = main(["measure-list", path, "--out", self.out])
        self.assertEqual(code, 1)
        self.assertIn("measure-list failed", err.getvalue())

    def test_a_failed_batch_json_publish_keeps_the_old_file_and_leaves_no_temp(self):
        Path(self.out).mkdir(parents=True)
        old = b'[{"old": true}]'
        (Path(self.out) / "batch.json").write_bytes(old)
        path = write_list(self.tmp.name, OK_URL + "\n")
        real = os.replace

        def replace(src, dst):
            if Path(dst).name == "batch.json":
                raise OSError("disk full")
            real(src, dst)

        with (
            mock.patch("piggyback.measure.measure", side_effect=fake_ok),
            mock.patch("piggyback.measure.os.replace", side_effect=replace),
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = main(["measure-list", path, "--out", self.out])
        self.assertEqual(code, 1)
        self.assertEqual((Path(self.out) / "batch.json").read_bytes(), old)
        self.assertEqual([p for p in Path(self.out).iterdir() if p.name.startswith(".")], [])


if __name__ == "__main__":
    unittest.main()
