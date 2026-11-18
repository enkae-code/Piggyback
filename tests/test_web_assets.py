"""Static check of the screen's assets: no specimen kind may be a box class in the stylesheet.

A specimen's <ul> carries its kind as a class, so a kind that is also a standalone rule
(`.meter { height: 12px; border }`) draws the whole list as that box and overflows it.
"""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "piggyback" / "web"


class SpecimenKindTests(unittest.TestCase):
    def test_no_specimen_kind_is_styled_as_a_standalone_class(self):
        js = (WEB / "app.js").read_text(encoding="utf-8")
        css = (WEB / "app.css").read_text(encoding="utf-8")
        kinds = {word for group in re.findall(r'samples\("([^"]+)"', js) for word in group.split()}
        self.assertTrue(kinds)
        for kind in sorted(kinds):
            # `.specimen.<kind>` is the intended form; any other `.<kind>` rule is the collision.
            standalone = re.compile(rf"(?<!specimen)(?<![\w-])\.{re.escape(kind)}(?![\w-])")
            self.assertIsNone(standalone.search(css), f"specimen kind {kind!r} is styled as a box")


def function_body(js, name):
    match = re.search(rf"^(?:async )?function {name}\(.*?^\}}", js, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in app.js"
    return match.group(0)


class StaticChecks(unittest.TestCase):
    """Static text checks, not behaviour: they catch a removed guard, not a wrong one."""

    def setUp(self):
        self.js = (WEB / "app.js").read_text(encoding="utf-8")
        self.css = (WEB / "app.css").read_text(encoding="utf-8")

    def test_hidden_attribute_wins_over_a_class_display_rule(self):
        self.assertRegex(self.css, r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important")

    def test_push_ref_refuses_blank_and_duplicate_references(self):
        body = function_body(self.js, "pushRef")
        self.assertRegex(body, r"if \(!url\.trim\(\)\) return false;")
        self.assertRegex(body, r"state\.refs\.includes\(url\)\)\s*\{[^}]*return false;")

    def test_measure_clears_both_error_lines_on_entry(self):
        body = function_body(self.js, "measure")
        self.assertIn('showError("error", "");', body)
        self.assertIn('showError("ref-error", "");', body)


if __name__ == "__main__":
    unittest.main()
