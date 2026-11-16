import unittest
from pathlib import Path

from piggyback import defaults
from piggyback import fingerprint as fp

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"


def concepts(build):
    return [concept for concept, _ in defaults.find(build)]


class DefaultsTests(unittest.TestCase):
    def setUp(self):
        self.build = fp.load(FIXTURES / "build.json")

    def test_rules_fire_only_where_the_evidence_is(self):
        # Build has gradient text, a tinted icon square and repeated fade-and-move motion.
        # Two radii and a 2.25x heading ratio mean the radius and flat-scale rules stay quiet.
        self.assertEqual(concepts(self.build), ["gradient_text", "repeated_motion", "icon_tile"])

    def test_gradient_and_icon_silent_without_evidence(self):
        build = {**self.build, "effects": {"gradient_text": 0, "tinted_icon_squares": 0}}
        self.assertNotIn("gradient_text", concepts(build))
        self.assertNotIn("icon_tile", concepts(build))

    def test_repeated_motion_needs_three_elements(self):
        motion = [{**self.build["motion"][0], "count": 2}]
        self.assertNotIn("repeated_motion", concepts({**self.build, "motion": motion}))

    def test_uniform_radius_fires_on_one_radius(self):
        build = {**self.build, "radius": [{"value": "12px", "share": 1}]}
        self.assertIn("uniform_radius", concepts(build))

    def test_flat_scale_fires_on_one_font_and_small_heading_ratio(self):
        heading = {**self.build["anchors"]["heading"], "font_size": "18px"}
        build = {**self.build, "anchors": {**self.build["anchors"], "heading": heading}}
        self.assertIn("one_font_flat_scale", concepts(build))

    def test_flat_scale_ignores_generic_fallback_fonts(self):
        heading = {**self.build["anchors"]["heading"], "font_size": "18px"}
        families = [{"value": "Inter, sans-serif", "share": 1}]
        build = {
            **self.build,
            "anchors": {**self.build["anchors"], "heading": heading},
            "type": {**self.build["type"], "families": families},
        }
        self.assertIn("one_font_flat_scale", concepts(build))

    def test_no_evidence_no_defaults(self):
        self.assertEqual(defaults.find({}), [])
        self.assertEqual(defaults.render([]), "No generic defaults detected.\n")

    def test_render_numbers_each_default_with_why(self):
        text = defaults.render(defaults.find(self.build))
        self.assertIn("- ① Gradient text", text)
        self.assertIn("- ② Repeated motion", text)
        self.assertEqual(text.count("  - Why it matters:"), 3)


if __name__ == "__main__":
    unittest.main()
