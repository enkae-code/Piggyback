import os
import tempfile
import unittest
from pathlib import Path

from piggyback import fingerprint as fp


class CleanTests(unittest.TestCase):
    def test_tag_characters_are_removed(self):
        self.assertEqual(fp.clean("ab\U000e0041\U000e007fcd"), "ab cd")

    def test_zero_width_and_bom_are_removed(self):
        self.assertEqual(fp.clean("a\u200bb\u200fc\ufeffd"), "a b c d")

    def test_invisible_non_control_characters_are_removed(self):
        hostile = "A" + "\ufe0f" * 50 + "\U000e0100" + "\u034f\u115f\u3164\uffa0\u2800" + "B"
        self.assertEqual(fp.clean(hostile), "A B")

    def test_bidi_controls_are_removed(self):
        self.assertEqual(fp.clean("x\u202eevil\u2066y\u061cz"), "x evil y z")

    def test_c1_controls_are_removed(self):
        self.assertEqual(fp.clean("a\x85b\x9fc"), "a b c")

    def test_line_and_paragraph_separators_are_removed(self):
        self.assertEqual(fp.clean("a\u2028b\u2029c"), "a b c")

    def test_private_use_and_unassigned_are_removed(self):
        self.assertEqual(fp.clean("a\ue000b\U000f0000c"), "a b c")

    def test_length_is_capped_before_escaping(self):
        self.assertEqual(fp.clean("x" * 100), "x" * 60)
        self.assertEqual(fp.clean("x" * 100, limit=5), "xxxxx")

    def test_markdown_control_characters_are_escaped(self):
        self.assertEqual(fp.clean("a\\b|c`d*e_f"), "a\\\\b\\|c\\`d\\*e\\_f")

    def test_visible_text_passes_through(self):
        self.assertEqual(fp.clean("Inter Tight 4.5"), "Inter Tight 4.5")


class LoadTests(unittest.TestCase):
    def write(self, data):
        handle, name = tempfile.mkstemp(dir=os.environ.get("TMPDIR"))
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        self.addCleanup(os.unlink, name)
        return Path(name)

    def test_deeply_nested_json_is_a_value_error_not_a_crash(self):
        path = self.write(b"[" * 100_000 + b"]" * 100_000)
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            fp.load(path)

    def test_non_object_json_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not a fingerprint object"):
            fp.load(self.write(b"[1, 2]"))

    def test_bad_utf8_is_refused(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            fp.load(self.write(b'{"a": "\xff"}'))

    def test_file_over_five_megabytes_is_refused(self):
        with self.assertRaisesRegex(ValueError, "larger than 5 MB"):
            fp.load(self.write(b" " * (fp.MAX_BYTES + 1)))

    def test_file_at_the_limit_is_read(self):
        path = self.write(b"{" + b" " * (fp.MAX_BYTES - 2) + b"}")
        self.assertEqual(fp.load(path), {})


class FontTests(unittest.TestCase):
    def test_real_google_font_names_pass(self):
        for name in ("Prompt", "Assistant", "Instrument Sans", "Inter Tight", "Run Sans"):
            with self.subTest(name):
                self.assertEqual(fp.font_names(name), [name])

    def test_next_font_names_with_underscores_pass(self):
        self.assertEqual(
            fp.primary_font("__Inter_d65c78, __Inter_Fallback_d65c78"), "__Inter_d65c78"
        )

    def test_css_generic_families_pass(self):
        for name in ("system-ui", "ui-sans-serif", "ui-monospace", "sans-serif", "emoji", "math"):
            with self.subTest(name):
                self.assertEqual(fp.font_names(name), [name])

    def test_shell_metacharacters_are_dropped(self):
        for name in ("a/b", "a;b", "a|b", "a&&b", "a$(b)", "a`b`"):
            with self.subTest(name):
                self.assertEqual(fp.font_names(name), [])


if __name__ == "__main__":
    unittest.main()
