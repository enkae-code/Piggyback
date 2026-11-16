import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from piggyback import format as ref_format
from piggyback.cli import check_refs

GOOD = {"piggyback": 1, "by": "Ada", "by_kind": "agent", "license": "CC0-1.0"}


class CheckRefsTests(unittest.TestCase):
    def run_check(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            refs = Path(tmp) / "refs"
            refs.mkdir()
            for name, meta in files.items():
                (refs / name).write_text(ref_format.render_ref(meta, "", "", ""), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check_refs(tmp)
            return code, out.getvalue()

    def test_clean_refs_exit_zero(self):
        code, out = self.run_check({"a.md": GOOD})
        self.assertEqual(code, 0)
        self.assertIn("1 refs checked, 0 with problems", out)

    def test_bad_ref_exit_one_and_reported(self):
        code, out = self.run_check({"a.md": GOOD, "b.md": {**GOOD, "by_kind": "brodey"}})
        self.assertEqual(code, 1)
        self.assertIn("by_kind must be one of", out)

    def test_unparseable_file_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x.md").write_text("no fences here", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = check_refs(tmp)
        self.assertEqual(code, 1)
        self.assertIn("missing frontmatter fences", out.getvalue())


if __name__ == "__main__":
    unittest.main()
