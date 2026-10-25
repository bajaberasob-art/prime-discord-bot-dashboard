"""Regression tests for the PRIME smart profile-template renderer."""

import unittest

from PIL import Image

import profile_card_generator as generator
import profile_store_db


class ProfileCardSmartLayoutTests(unittest.TestCase):
    def test_all_approved_templates_place_avatar_in_lower_art_slot(self):
        self.assertGreaterEqual(len(profile_store_db.DEFAULT_PROFILE_TEMPLATES), 7)
        for item in profile_store_db.DEFAULT_PROFILE_TEMPLATES:
            with self.subTest(template=item["slug"]):
                meta = item["metadata"]
                width, height = int(meta["width"]), int(meta["height"])
                avatar = meta["avatar"]
                self.assertEqual(meta["layout_engine"], "prime-smart-v2")
                self.assertGreater(avatar["y"], int(height * 0.50))
                self.assertGreater(avatar["size"], 0)
                self.assertLessEqual(avatar["x"] + avatar["size"], width)
                self.assertLessEqual(avatar["y"] + avatar["size"], height)

    def test_progress_bar_follows_level_and_keeps_clear_of_bottom_stats(self):
        for item in profile_store_db.DEFAULT_PROFILE_TEMPLATES:
            with self.subTest(template=item["slug"]):
                meta = item["metadata"]
                self.assertGreater(meta["xp_bar"]["y"], meta["level"]["y"])
                self.assertLess(meta["xp_bar"]["x"] + meta["xp_bar"]["width"], meta["width"])
                self.assertGreater(min(stat["y"] for stat in meta["stats"]), int(meta["height"] * 0.75))
                self.assertTrue(all(stat["h"] > 0 and stat["w"] > 0 for stat in meta["stats"]))

    def test_template_palette_has_valid_font_presets(self):
        expected = {"modern", "tech", "elegant", "soft"}
        for item in profile_store_db.DEFAULT_PROFILE_TEMPLATES:
            with self.subTest(template=item["slug"]):
                family = item["style"]["font_family"]
                self.assertIn(family, expected)
                font = generator._load_font(18, bold=True, font_family=family)
                self.assertIsNotNone(font)
                self.assertGreater(font.getbbox("PRIME")[2], 0)

    def test_text_color_automatically_contrasts_with_template_region(self):
        box = {"x": 160, "y": 60, "max_width": 140, "anchor": "ra"}
        dark_art = Image.new("RGB", (220, 100), (12, 18, 34))
        light_art = Image.new("RGB", (220, 100), (240, 240, 245))
        self.assertEqual(
            generator._contrast_text_color(dark_art, box, "#F9F7FF", "#101522", line_height=24),
            "#F9F7FF",
        )
        self.assertEqual(
            generator._contrast_text_color(light_art, box, "#F9F7FF", "#101522", line_height=24),
            "#101522",
        )

    def test_arabic_mixed_text_remains_renderable_with_template_fonts(self):
        text = generator._display_text("المستوى 117")
        self.assertTrue(text.strip())
        font = generator._fit_font(text, 260, 28, font_family="modern")
        self.assertGreater(font.getbbox(text)[2], 0)


if __name__ == "__main__":
    unittest.main()
