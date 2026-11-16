import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from piggyback import brief as brief_mod
from piggyback import fingerprint as fp
from piggyback.cli import main

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"
REF = str(FIXTURES / "ref.json")
BUILD = str(FIXTURES / "build.json")


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class BriefCliTests(unittest.TestCase):
    def test_brief_prints_to_stdout(self):
        code, out, _ = run(["brief", REF])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# Design brief"))

    def test_brief_with_build_appends_numbered_differences(self):
        code, out, _ = run(["brief", REF, "--build", BUILD])
        self.assertEqual(code, 0)
        self.assertIn("## Differences (build → reference)", out)
        self.assertIn("- ① Heading type", out)

    def test_build_needs_exactly_one_reference(self):
        code, out, err = run(["brief", REF, BUILD, "--build", BUILD])
        self.assertEqual(code, 1)
        self.assertIn("pass exactly one", err)

    def test_more_than_three_references_refused(self):
        code, _, err = run(["brief", REF, REF, REF, REF])
        self.assertEqual(code, 1)
        self.assertIn("not 4", err)

    def test_out_writes_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "BRIEF.md"
            code, out, _ = run(["brief", REF, "--out", str(target)])
            self.assertEqual(code, 0)
            self.assertTrue(target.read_text(encoding="utf-8").startswith("# Design brief"))
            self.assertIn("wrote", out)

    def test_missing_file_fails_cleanly(self):
        code, _, err = run(["brief", "/no/such/fingerprint.json"])
        self.assertEqual(code, 1)
        self.assertIn("brief failed", err)

    def test_invalid_json_and_non_objects_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, body in (("bad.json", "{not json"), ("list.json", "[1, 2]")):
                path = Path(tmp) / name
                path.write_text(body, encoding="utf-8")
                code, _, err = run(["brief", str(path)])
                self.assertEqual(code, 1, name)
                self.assertIn("brief failed", err)

    def test_fingerprint_over_size_limit_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "big.json"
            path.write_text(json.dumps({"x": "a" * (fp.MAX_BYTES + 1)}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "larger than 5 MB"):
                fp.load(path)


class DesignMdCliTests(unittest.TestCase):
    def test_design_md_is_written_for_the_first_reference_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "DESIGN.md"
            code, _, _ = run(["brief", REF, BUILD, "--design-md", str(target)])
            self.assertEqual(code, 0)
            self.assertTrue(target.read_text(encoding="utf-8").startswith("---\nname: "))
            self.assertEqual(target.read_text(encoding="utf-8"), brief_design(REF))

    def test_design_md_refuses_to_write_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            victim = Path(tmp) / "victim.txt"
            victim.write_text("keep", encoding="utf-8")
            link = Path(tmp) / "DESIGN.md"
            link.symlink_to(victim)
            code, _, err = run(["brief", REF, "--design-md", str(link)])
            self.assertEqual(code, 1)
            self.assertIn("already exists", err)
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep")

    def test_out_and_design_md_must_be_different_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "D.md"
            code, _, err = run(["brief", REF, "--out", str(target), "--design-md", str(target)])
            self.assertEqual(code, 1)
            self.assertIn("different files", err)
            self.assertFalse(target.exists())

    def test_bad_out_path_fails_cleanly_and_writes_no_design_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp) / "DESIGN.md"
            out = Path(tmp) / "missing" / "BRIEF.md"
            code, _, err = run(["brief", REF, "--out", str(out), "--design-md", str(design)])
            self.assertEqual(code, 1)
            self.assertIn("brief failed:", err)
            self.assertFalse(design.exists())

    def test_design_md_never_replaces_an_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "DESIGN.md"
            target.write_text("hand-written\n", encoding="utf-8")
            code, _, err = run(["brief", REF, "--design-md", str(target)])
            self.assertEqual(code, 1)
            self.assertIn("already exists", err)
            self.assertEqual(target.read_text(encoding="utf-8"), "hand-written\n")


def brief_design(path):
    return brief_mod.design_md(fp.load(path))


class DefaultsCliTests(unittest.TestCase):
    def test_defaults_lists_marks(self):
        code, out, _ = run(["defaults", BUILD])
        self.assertEqual(code, 0)
        self.assertIn("## Defaults detected", out)

    def test_defaults_missing_file_fails_cleanly(self):
        code, _, err = run(["defaults", "/no/such/build.json"])
        self.assertEqual(code, 1)
        self.assertIn("defaults failed", err)


if __name__ == "__main__":
    unittest.main()
