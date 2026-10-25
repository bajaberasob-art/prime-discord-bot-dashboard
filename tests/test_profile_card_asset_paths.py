"""Regression tests for repository-relative assets used by PRIME profile cards."""

from pathlib import Path
import unittest

import profile_card_generator as generator


class ProfileCardAssetPathTests(unittest.TestCase):
    def test_generator_root_is_the_repository_root(self):
        expected_root = Path(generator.__file__).resolve().parent
        self.assertEqual(generator.ROOT, expected_root)
        self.assertTrue((expected_root / "assets").is_dir())

    def test_approved_png_template_resolves_and_is_readable(self):
        template_path = "assets/profile_templates/02_wide_icy_blue.png"
        resolved = generator._resolve_local_asset(template_path, template_asset=True)
        self.assertIsNotNone(
            resolved,
            "The live renderer must find the repository's approved PNG template.",
        )
        self.assertTrue(resolved.is_file())

        raw = generator._read_local_asset(template_path, template_asset=True)
        self.assertIsNotNone(raw, "The approved frame must load instead of using fallback art.")
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_arabic_capable_repository_fonts_are_loaded(self):
        regular = generator._FONT_ROOT / "DejaVuSans.ttf"
        bold = generator._FONT_ROOT / "DejaVuSans-Bold.ttf"
        self.assertTrue(regular.is_file(), "Regular font path must resolve inside the repository.")
        self.assertTrue(bold.is_file(), "Bold font path must resolve inside the repository.")
        self.assertEqual(generator._load_font(18).getname()[0], "DejaVu Sans")
        self.assertEqual(generator._load_font(18, bold=True).getname()[0], "DejaVu Sans")


if __name__ == "__main__":
    unittest.main()
