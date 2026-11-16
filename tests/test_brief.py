import re
import unittest
from pathlib import Path

from piggyback import brief as brief_mod
from piggyback import fingerprint as fp
from piggyback import gap as gap_mod

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"
BRACKETS = re.compile(r"(?<!\\)[\[\]`<>]")  # an unescaped character a renderer could act on


def load(name):
    return fp.load(FIXTURES / name)


class BriefTests(unittest.TestCase):
    def test_one_reference_has_sections_and_a_why_line_each(self):
        text = brief_mod.brief([load("ref.json")])
        self.assertIn("## Reference 1: https://ref.example/", text)
        self.assertIn("- Type scale (the set of text sizes a page uses): 56px, 16px, 14px", text)
        self.assertIn(
            "- Accent (the saturated colour that draws the eye): 2% of the viewport", text
        )
        self.assertIn("- Container width (how wide the main content column is): 1200px", text)
        self.assertEqual(text.count("  - Why it matters:"), 7)

    def test_three_references_are_listed_separately(self):
        refs = [load("ref.json"), load("build.json"), load("hostile.json")]
        text = brief_mod.brief(refs)
        for index in (1, 2, 3):
            self.assertIn(f"## Reference {index}:", text)
        self.assertIn("none are averaged", text)

    def test_more_than_three_and_none_are_refused(self):
        ref = load("ref.json")
        with self.assertRaisesRegex(ValueError, "not 4"):
            brief_mod.brief([ref] * 4)
        with self.assertRaises(ValueError):
            brief_mod.brief([])

    def test_hostile_strings_cannot_reach_the_agent_as_markup_or_lines(self):
        text = brief_mod.brief([load("hostile.json")])
        self.assertIsNone(BRACKETS.search(text), "unescaped markdown or HTML")
        self.assertNotIn("\n## Borrow", text)
        self.assertNotIn("\n- run", text)
        self.assertNotIn("javascript:", text)
        self.assertNotIn("Ignore previous", text)
        self.assertNotIn("npm i", text)
        self.assertNotIn("install pip", text)
        self.assertNotIn("Install Inter", text)  # short, but reads as a command: dropped
        self.assertIn("Inter", text)  # a plain font name still gets through

    def test_notes_are_cleaned_and_blank_ones_dropped(self):
        notes = {"borrow": ["Tight tracking\n## Avoid <b>x</b>"], "avoid": ["   "]}
        text = brief_mod.brief([load("ref.json")], notes)
        self.assertIn("## Borrow", text)
        self.assertNotIn("## Avoid", text)
        self.assertNotIn("\n## Avoid", text)
        self.assertIsNone(BRACKETS.search(text))

    def test_missing_or_junk_fields_do_not_crash(self):
        text = brief_mod.brief([{}, {"type": "junk", "palette": 5, "radius": [None, 3]}])
        self.assertIn("not measured", text)
        self.assertNotIn("score", text.lower())


class GapTests(unittest.TestCase):
    def setUp(self):
        self.ref = load("ref.json")
        self.build = load("build.json")

    def test_pins_only_on_anchors_both_pages_have(self):
        pins = gap_mod.gap(self.ref, self.build)
        self.assertEqual([pin.number for pin in pins], [1, 2, 3, None])
        self.assertEqual(pins[0].detail, "Heading 36/44 → 56/58, -0.02em")
        self.assertEqual(pins[1].detail, "accent on 19% of the page → 2%")
        self.assertEqual(pins[2].detail, "1280px → 1200px")
        self.assertEqual(pins[3].detail, "Navigation: not found on the build")

    def test_boxes_travel_with_the_pin(self):
        heading = gap_mod.gap(self.ref, self.build)[0]
        self.assertEqual(heading.ref_box, {"x": 80, "y": 120, "width": 640, "height": 58})
        self.assertEqual(heading.build_box, {"x": 80, "y": 120, "width": 520, "height": 44})

    def test_missing_anchor_is_never_guessed(self):
        build = {**self.build, "anchors": {**self.build["anchors"], "cta": None, "heading": None}}
        details = [pin.detail for pin in gap_mod.gap(self.ref, build) if pin.number is None]
        self.assertIn("Primary button: not found on the build", details)
        self.assertIn("Heading: not found on the build", details)

    def test_anchor_missing_on_both_sides_names_both(self):
        ref = {**self.ref, "anchors": {**self.ref["anchors"], "heading": None}}
        build = {**self.build, "anchors": {**self.build["anchors"], "heading": None}}
        details = [pin.detail for pin in gap_mod.gap(ref, build) if pin.number is None]
        self.assertIn("Heading: not found on the reference and the build", details)

    def heading_gap(self, size):
        heading = {"font_size": f"{size}px", "line_height": "58px", "letter_spacing": "-1.12px"}
        build = {**self.build, "anchors": {**self.build["anchors"], "heading": heading}}
        return [pin for pin in gap_mod.gap(self.ref, build) if pin.detail.startswith("Heading")]

    def test_type_threshold_edges(self):
        self.assertEqual(self.heading_gap(55), [])  # exactly 1px: within tolerance
        self.assertEqual(len(self.heading_gap(54.99)), 1)  # just over 1px: a pin

    def test_accent_threshold_edges(self):
        ref = {**self.ref, "palette": {**self.ref["palette"], "accent_share": 0.02}}

        def accent_pins(share):
            build = {**self.build, "palette": {"accent_share": share}}
            return [pin for pin in gap_mod.gap(ref, build) if pin.detail.startswith("accent")]

        self.assertEqual(accent_pins(0.04), [])  # exactly 2%: within tolerance
        self.assertEqual(len(accent_pins(0.0401)), 1)  # just over 2%: a pin

    def test_numbering_is_stable_across_runs(self):
        first = [(pin.number, pin.detail) for pin in gap_mod.gap(self.ref, self.build)]
        second = [(pin.number, pin.detail) for pin in gap_mod.gap(self.ref, self.build)]
        self.assertEqual(first, second)

    def test_render_numbers_and_explains_each_pin(self):
        text = gap_mod.render(gap_mod.gap(self.ref, self.build))
        self.assertIn("## Differences (build → reference)", text)
        self.assertIn("- ① Type scale (the set", text)
        self.assertIn("- ② Accent (the saturated", text)
        self.assertEqual(text.count("  - Why it matters:"), 4)

    def test_garbage_fingerprints_give_not_found_not_a_crash(self):
        pins = gap_mod.gap({}, {})
        self.assertTrue(pins)
        self.assertTrue(all(pin.number is None for pin in pins))


if __name__ == "__main__":
    unittest.main()
