import unittest
from pathlib import Path

from piggyback import defaults
from piggyback import fingerprint as fp

FIXTURES = Path(__file__).parent / "fixtures" / "fingerprints"
BOX = {"x": 80, "y": 120, "width": 520, "height": 44}


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
        build = {**self.build, "radius": [{"value": "12px", "share": 1}], "rounded_boxes": 3}
        self.assertIn("uniform_radius", concepts(build))

    def test_uniform_radius_needs_three_rounded_boxes(self):
        radius = [{"value": "12px", "share": 1}]
        for boxes in (2, None):
            with self.subTest(boxes=boxes):
                build = {**self.build, "radius": radius, "rounded_boxes": boxes}
                self.assertNotIn("uniform_radius", concepts(build))

    def test_flat_scale_fires_on_one_font_and_small_heading_ratio(self):
        heading = {**self.build["anchors"]["heading"], "font_size": "18px"}
        build = {**self.build, "anchors": {**self.build["anchors"], "heading": heading}}
        self.assertIn("one_font_flat_scale", concepts(build))

    def test_flat_scale_fires_at_exactly_one_and_a_half_times(self):
        heading = {**self.build["anchors"]["heading"], "font_size": "24px"}
        body = {**self.build["anchors"]["body"], "font_size": "16px"}
        build = {
            **self.build,
            "anchors": {**self.build["anchors"], "heading": heading, "body": body},
        }
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

    def test_malformed_motion_props_do_not_crash(self):
        motion = [
            {"props": ["opacity", 3], "count": 5, "duration": 300},
            {"props": None, "count": 5, "duration": 300},
            {"props": "opacity transform", "count": 5, "duration": 300},
            {"props": [[1]], "count": 5, "duration": 300},
            {"props": [{"a": 1}], "count": 5, "duration": 300},
            {"props": ["opacity", "transform"], "count": 5, "duration": 300},
        ]
        self.assertEqual(concepts({"motion": motion}), ["repeated_motion"])

    def with_boxes(self):
        square = {"x": 300, "y": 40, "width": 48, "height": 48}
        effects = {
            **self.build["effects"],
            "gradient_text_box": BOX,
            "tinted_icon_square_box": square,
        }
        motion = [{**self.build["motion"][0], "box": BOX}]
        return {**self.build, "effects": effects, "motion": motion}

    def test_boxes_for_the_three_rules_that_point_at_an_element(self):
        self.assertEqual(
            defaults.boxes(self.with_boxes()),
            {
                "gradient_text": BOX,
                "icon_tile": {"x": 300, "y": 40, "width": 48, "height": 48},
                "repeated_motion": BOX,
            },
        )

    def test_repeated_motion_box_comes_from_the_row_that_fired(self):
        quiet = {
            **self.build["motion"][0],
            "count": 2,
            "box": {"x": 1, "y": 1, "width": 9, "height": 9},
        }
        build = {**self.with_boxes(), "motion": [quiet, {**self.build["motion"][0], "box": BOX}]}
        self.assertEqual(defaults.boxes(build)["repeated_motion"], BOX)

    def test_no_box_without_the_fired_rule(self):
        build = self.with_boxes()
        build["effects"] = {**build["effects"], "gradient_text": 0, "tinted_icon_squares": 0}
        build["motion"] = []
        self.assertEqual(defaults.boxes(build), {})

    def test_missing_malformed_or_empty_box_gives_no_entry(self):
        bad = [
            None,
            {"x": 1, "y": 2, "width": 3},
            {"x": "1", "y": 2, "width": 3, "height": 4},
            {"x": True, "y": 2, "width": 3, "height": 4},
            {"x": float("nan"), "y": 2, "width": 3, "height": 4},
            {"x": 1, "y": 2, "width": 0, "height": 4},
            {"x": 1, "y": 2, "width": 3, "height": -4},
        ]
        for value in bad:
            with self.subTest(value=value):
                build = self.with_boxes()
                build["effects"] = {**build["effects"], "gradient_text_box": value}
                build["motion"] = [{**build["motion"][0], "box": value}]
                self.assertNotIn("gradient_text", defaults.boxes(build))
                self.assertNotIn("repeated_motion", defaults.boxes(build))

    def test_old_fingerprint_has_no_boxes(self):
        self.assertEqual(defaults.boxes(self.build), {})

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
