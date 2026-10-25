"""Regression tests for the PRIME smart profile-template renderer."""

import io
import unittest

from PIL import Image, ImageDraw

import profile_card_generator as generator
import profile_store_admin
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
                avatar_center_y = avatar["y"] + avatar["size"] / 2
                self.assertGreater(avatar_center_y, height * 0.50)
                self.assertGreater(avatar["size"], 0)
                self.assertLessEqual(avatar["x"] + avatar["size"], width)
                self.assertLessEqual(avatar["y"] + avatar["size"], height)

    def test_progress_bar_follows_level_and_keeps_clear_of_bottom_stats(self):
        for item in profile_store_db.DEFAULT_PROFILE_TEMPLATES:
            with self.subTest(template=item["slug"]):
                meta = item["metadata"]
                self.assertGreater(meta["xp_bar"]["y"], meta["level"]["y"])
                self.assertLess(meta["xp_bar"]["x"] + meta["xp_bar"]["width"], meta["width"])
                first_stat_y = min(stat["y"] for stat in meta["stats"])
                self.assertGreater(first_stat_y, int(meta["height"] * 0.55))
                self.assertGreater(first_stat_y, meta["xp_bar"]["y"] + meta["xp_bar"]["height"])
                self.assertTrue(all(stat["h"] > 0 and stat["w"] > 0 for stat in meta["stats"]))
                self.assertTrue(all(stat["y"] + stat["h"] <= meta["height"] for stat in meta["stats"]))

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


    def test_unknown_template_detects_lower_right_avatar_ring(self):
        width, height = 280, 360
        image = Image.new("RGBA", (width, height), (10, 13, 22, 255))
        draw = ImageDraw.Draw(image)
        center = (220, 245)
        radius = 38
        draw.ellipse(
            (center[0] - radius, center[1] - radius,
             center[0] + radius, center[1] + radius),
            fill=(4, 8, 18, 255),
            outline=(230, 240, 255, 255),
            width=5,
        )
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")

        detected = generator._detect_template_avatar_slot(encoded.getvalue())
        self.assertIsNotNone(detected)
        inferred_center_x = detected["x"] + detected["size"] // 2
        inferred_center_y = detected["y"] + detected["size"] // 2
        self.assertLess(abs(inferred_center_x - center[0]), 25)
        self.assertLess(abs(inferred_center_y - center[1]), 25)

        metadata = {
            "width": width, "height": height, "layout": "portrait",
            "avatar": {}, "name": {}, "handle": {}, "level": {}, "xp_bar": {},
            "stats": [],
        }
        resolved = generator._apply_smart_template_layout(metadata, encoded.getvalue())
        self.assertEqual(resolved["layout_engine"], "prime-smart-v2")
        self.assertEqual(resolved["avatar_detection"]["method"], "circular-contour")
        self.assertEqual(len(resolved["stats"]), 4)
        self.assertGreater(resolved["xp_bar"]["width"], 0)

    def test_detected_avatar_ring_overrides_stale_saved_coordinates(self):
        width, height = 280, 360
        image = Image.new("RGBA", (width, height), (10, 13, 22, 255))
        draw = ImageDraw.Draw(image)
        center = (220, 245)
        radius = 38
        draw.ellipse(
            (center[0] - radius, center[1] - radius,
             center[0] + radius, center[1] + radius),
            fill=(4, 8, 18, 255),
            outline=(230, 240, 255, 255),
            width=5,
        )
        encoded = io.BytesIO()
        image.save(encoded, format="PNG")

        # Simulate stale avatar coordinates in a template that already has hand-authored
        # text, XP-track, and stats anchors. Only the avatar should move.
        metadata = {
            "width": width, "height": height, "layout": "portrait",
            "avatar": {"x": 12, "y": 16, "size": 48},
            "name": {"x": 150, "y": 40, "max_width": 100, "anchor": "ra"},
            "handle": {"x": 150, "y": 60, "max_width": 100, "anchor": "ra"},
            "level": {"x": 20, "y": 200, "max_width": 120, "anchor": "la"},
            "xp_bar": {"x": 20, "y": 235, "width": 190, "height": 8},
            "stats": [{"key": "balance", "label": "الرصيد", "x": 20, "y": 290, "w": 100, "h": 35}],
        }
        resolved = generator._apply_smart_template_layout(metadata, encoded.getvalue())
        avatar = resolved["avatar"]
        actual_center_x = avatar["x"] + avatar["size"] / 2
        actual_center_y = avatar["y"] + avatar["size"] / 2

        self.assertEqual(
            resolved["avatar_detection"]["method"], "circular-contour-override"
        )
        # The coarse local contour scan is approximate but must beat the stale saved slot.
        self.assertLess(abs(actual_center_x - center[0]), 36)
        self.assertLess(abs(actual_center_y - center[1]), 36)
        for key in ("name", "handle", "level", "xp_bar", "stats"):
            self.assertEqual(resolved[key], metadata[key], f"{key} anchor changed with avatar")

    def test_arabic_draw_uses_directional_text_without_double_reordering(self):
        image = Image.new("RGBA", (420, 100), (8, 12, 22, 255))
        draw = ImageDraw.Draw(image)
        font = generator._load_font(32, bold=True, font_family="modern")
        self.assertTrue(generator._contains_arabic("المستوى 117"))
        generator._draw_display_text(
            draw, (400, 12), "المستوى 117",
            font=font, fill="#FFFFFF", anchor="ra",
        )
        self.assertGreater(len(set(image.convert("RGB").getdata())), 1)

    def test_admin_allows_profile_template_artwork_upload(self):
        self.assertIn("card_template", profile_store_admin._ASSET_ITEM_TYPES)

    def test_arabic_mixed_text_remains_renderable_with_template_fonts(self):
        text = generator._display_text("المستوى 117")
        self.assertTrue(text.strip())
        font = generator._fit_font(text, 260, 28, font_family="modern")
        self.assertGreater(font.getbbox(text)[2], 0)


if __name__ == "__main__":
    unittest.main()
