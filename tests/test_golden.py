"""Golden checks on real `piggyback measure` output (tests/fixtures/real-*.json). No browser."""

import re
import unittest
from pathlib import Path

from piggyback import brief as brief_mod
from piggyback import fingerprint as fp
from piggyback import gap as gap_mod

FIXTURES = Path(__file__).parent / "fixtures"
HEX = re.compile(r"#[0-9a-f]{6}")


class RealSampleGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ref = fp.load(FIXTURES / "real-linear.json")
        cls.build = fp.load(FIXTURES / "real-example.json")

    def test_brief_on_linear_is_readable(self):
        text = brief_mod.brief([self.ref])
        palette = next(x for x in text.splitlines() if x.startswith("- Palette"))
        self.assertLessEqual(len(HEX.findall(palette)), 5)
        self.assertNotIn("rgba(", palette)
        self.assertIn("background #08090a", palette)
        self.assertIn("13.3px", text)
        self.assertNotIn("13.3333", text)
        self.assertEqual(text.count("  - Why it matters:"), 7)

    def test_gap_on_real_pair_never_mislabels_a_missing_anchor(self):
        text = gap_mod.render(gap_mod.gap(self.ref, self.build))
        self.assertIn("Heading: not found on the build — can't compare", text)
        self.assertIn("Navigation: not found on the build — can't compare", text)
        self.assertNotIn("Type scale", text)

    def test_linear_accent_is_zero_because_no_background_is_saturated(self):
        self.assertEqual(self.ref["palette"]["accent_share"], 0)


if __name__ == "__main__":
    unittest.main()
