"""Golden checks on real `piggyback measure` output (tests/fixtures/real-*.json). No browser."""

import re
import unittest
from pathlib import Path

from piggyback import brief as brief_mod
from piggyback import fingerprint as fp
from piggyback import gap as gap_mod

FIXTURES = Path(__file__).parent / "fixtures"
HEX = re.compile(r"#[0-9a-f]{6}")


def front_block(text):
    lines = text.split("\n")
    return "\n".join(lines[1 : lines.index("---", 1)])


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

    def test_real_page_columns(self):
        # Measured on the host 2026-11-16; example.com's column is body max-width 26em = 416px.
        expected = {
            "real-example": 416,
            "real-linear": 1344,
            "real-stripe": 1266,
            "real-vercel": 1080,
        }
        for name, width in expected.items():
            with self.subTest(name):
                self.assertEqual(fp.load(FIXTURES / f"{name}.json")["container_width"], width)

    def test_gap_on_real_pair_never_mislabels_a_missing_anchor(self):
        text = gap_mod.render(gap_mod.gap(self.ref, self.build))
        self.assertIn("Heading: not found on the build — can't compare", text)
        self.assertIn("Navigation: not found on the build — can't compare", text)
        self.assertNotIn("Type scale", text)

    def test_fonts_name_only_the_primary_family_on_real_pages(self):
        expected = {
            "real-linear": "Inter Variable, Berkeley Mono",
            "real-vercel": "GeistSans",
            "real-stripe": "sohne-var",
            "real-example": "system-ui",
        }
        for name, fonts in expected.items():
            text = brief_mod.brief([fp.load(FIXTURES / f"{name}.json")])
            line = next(x for x in text.splitlines() if x.startswith("- Fonts"))
            self.assertTrue(line.endswith(f": {fonts}"), line)
            self.assertNotIn("SF Pro", line)
            self.assertNotIn("Segoe", line)

    def test_design_md_on_linear_carries_measured_values_only(self):
        front = front_block(brief_mod.design_md(self.ref))
        self.assertIn('  background-1: "#08090a"', front)
        self.assertIn('  text-1: "#f7f8f8"', front)
        self.assertIn('    fontFamily: "Inter Variable, Berkeley Mono"', front)
        self.assertIn('    "16": "16px"', front)
        self.assertIn('  full: "9999px"', front)
        self.assertIn('  radius-1: "400px"', front)
        self.assertNotIn("rgba(", front)
        self.assertNotIn("opacity", front)
        body = brief_mod.design_md(self.ref).split("---\n", 2)[2]
        self.assertIn("- Container width: 1344px", body)

    def test_linear_accent_is_zero_because_no_background_is_saturated(self):
        self.assertEqual(self.ref["palette"]["accent_share"], 0)


if __name__ == "__main__":
    unittest.main()
