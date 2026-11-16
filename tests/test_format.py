import unittest
from pathlib import Path

from piggyback import format as ref_format

ROOT = Path(__file__).resolve().parent.parent
GOOD = {
    "piggyback": 1,
    "by": "Ada Example",
    "by_kind": "human",
    "license": "MIT",
    "attribution": "Ada Example, 2026",
    "license_url": "https://example.com/license",
    "captured_with": "piggyback 0.1",
    "sha256": "a" * 64,
}


class FormatTests(unittest.TestCase):
    def test_round_trip(self):
        text = ref_format.render_ref(GOOD, "tight tracking", "neon glow", "Notes here.\n")
        meta, borrow, avoid, notes = ref_format.parse_ref(text)
        self.assertEqual(meta, GOOD)
        self.assertEqual((borrow, avoid, notes), ("tight tracking", "neon glow", "Notes here."))
        self.assertEqual(ref_format.render_ref(meta, borrow, avoid, notes), text)

    def test_missing_fences_rejected(self):
        with self.assertRaises(ValueError):
            ref_format.parse_ref("piggyback: 1\n")

    def test_good_meta_has_no_problems(self):
        self.assertEqual(ref_format.validate(GOOD), [])

    def test_minimal_inspiration_only_is_valid(self):
        meta = {"piggyback": 1, "by": "Someone", "by_kind": "agent", "license": "inspiration-only"}
        self.assertEqual(ref_format.validate(meta), [])

    def test_validation_errors(self):
        cases = {
            "missing by": {**GOOD, "by": None},
            "bad version": {**GOOD, "piggyback": 2},
            "bool version": {**GOOD, "piggyback": True},  # True == 1 in Python
            "bad by_kind": {**GOOD, "by_kind": "brodey"},
            "bad licence": {**GOOD, "license": "not a licence!"},
            "bad licence url": {**GOOD, "license_url": "file:///etc/passwd"},
            "bad sha": {**GOOD, "sha256": "xyz"},
            "empty captured_with": {**GOOD, "captured_with": "  "},
        }
        for name, meta in cases.items():
            with self.subTest(name):
                self.assertTrue(ref_format.validate(meta), meta)

    def test_schema_keys_match_validator(self):
        import json

        schema = json.loads((ROOT / "schema.json").read_text(encoding="utf-8"))
        self.assertEqual(set(schema["required"]), set(ref_format.REQUIRED))
        self.assertEqual(schema["properties"]["piggyback"]["const"], ref_format.VERSION)
        self.assertEqual(schema["properties"]["by_kind"]["enum"], list(ref_format.BY_KINDS))

    def test_no_personal_names_in_shipped_files(self):
        for name in ("piggyback/format.py", "schema.json", "README.md"):
            path = ROOT / name
            if path.exists():
                text = path.read_text(encoding="utf-8").lower()
                self.assertNotIn("brodey", text, name)
                self.assertNotIn("dakoda", text, name)


if __name__ == "__main__":
    unittest.main()
