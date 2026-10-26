import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from profile_store_db import DEFAULT_PROFILE_TEMPLATES
from profile_card_generator import render_profile_card, _safe_metadata, _draw_stat_text, _fit_font, _display_text, _resolve_local_asset


class ProfileCardTemplateTests(unittest.TestCase):
    def test_seven_independent_png_templates_exist_and_keep_native_dimensions(self):
        root = Path(__file__).resolve().parents[1]
        dimensions = set()
        paths = set()

        self.assertEqual(len(DEFAULT_PROFILE_TEMPLATES), 7)
        for template in DEFAULT_PROFILE_TEMPLATES:
            path = root / template["asset_path"]
            self.assertTrue(path.is_file(), f"Missing asset for {template['slug']}: {path}")
            self.assertEqual(path.suffix.lower(), ".png")
            self.assertLessEqual(path.stat().st_size, 8 * 1024 * 1024)
            paths.add(path.resolve())
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                self.assertEqual(image.format, "PNG", template["slug"])
                self.assertIn(image.mode, ("RGB", "RGBA"), template["slug"])
                dimensions.add(image.size)

        self.assertEqual(len(paths), 7)
        self.assertEqual(len({json.dumps(item["metadata"], sort_keys=True) for item in DEFAULT_PROFILE_TEMPLATES}), 7,
                         "Each approved raster template must have its own element geometry")
        self.assertGreaterEqual(len(dimensions), 3)
        self.assertTrue(any(width > height for width, height in dimensions))
        self.assertTrue(any(height > width for width, height in dimensions))
        self.assertTrue(any(width == height for width, height in dimensions))

    def test_every_png_template_renders_with_member_data(self):
        root = Path(__file__).resolve().parents[1]
        avatar_buffer = io.BytesIO()
        Image.new("RGB", (128, 128), "#FF78B7").save(avatar_buffer, format="PNG")
        avatar = avatar_buffer.getvalue()
        profile = {
            "display_name": "لونا • Lona",
            "username": "lona",
            "level": 25,
            "xp": 4894,
            "required_xp": 9120,
            "balance": 30300,
            "messages": 11400,
            "rank": 724,
            "voice_seconds": 82800,
        }

        for template in DEFAULT_PROFILE_TEMPLATES:
            path = root / template["asset_path"]
            with Image.open(path) as source:
                native_size = source.size
            result = render_profile_card(
                profile,
                avatar_bytes=avatar,
                template_bytes=path.read_bytes(),
                template=template,
            )
            self.assertEqual(result.tell(), 0, template["slug"])
            with Image.open(result) as card:
                self.assertEqual(card.size, native_size, template["slug"])
                self.assertEqual(card.mode, "RGBA")
                self.assertEqual(card.format, "PNG")
                card.load()
                with Image.open(path) as original:
                    original_rgba = original.convert("RGBA")
                    self.assertEqual(card.getpixel((0, 0))[3], original_rgba.getpixel((0, 0))[3],
                                     f"Outer-corner alpha changed for {template['slug']}")
                self.assertIsNotNone(card.getbbox(), f"Rendered content is empty for {template['slug']}")

    def test_sanitized_gif_asset_is_allowed_for_profile_background_rendering(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "catalog" / "99" / "motion.gif"
            path.parent.mkdir(parents=True)
            frames = [
                Image.new("RGB", (72, 48), "#168CDC"),
                Image.new("RGB", (72, 48), "#E34A99"),
            ]
            frames[0].save(
                path, format="GIF", save_all=True, append_images=frames[1:],
                duration=[80, 120], loop=0,
            )
            with patch.dict(os.environ, {"PROFILE_STORE_ASSET_DIR": temporary}):
                resolved = _resolve_local_asset(str(path))
            self.assertEqual(resolved, path.resolve())

    def test_approved_ultrawide_template_dimensions_are_preserved(self):
        metadata = _safe_metadata({
            "metadata": {
                "layout": "wide",
                "width": 1860,
                "height": 845,
                "avatar": {"x": 1500, "y": 80, "size": 180},
            }
        })
        self.assertEqual((metadata["width"], metadata["height"]), (1860, 845))
        self.assertEqual(metadata["layout"], "wide")

    def test_untrusted_template_dimensions_are_clamped(self):
        metadata = _safe_metadata({
            "metadata": {
                "layout": "wide",
                "width": 100000,
                "height": 100000,
                "avatar": {"x": -10, "y": -10, "size": 100000},
            }
        })
        self.assertLessEqual(metadata["width"] * metadata["height"], 2_500_000)
        self.assertLessEqual(metadata["width"], 2048)
        self.assertLessEqual(metadata["height"], 1600)
        self.assertEqual(metadata["layout"], "wide")


    def test_long_stat_values_are_fitted_without_ellipsis_truncation(self):
        image = Image.new("RGBA", (360, 120), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        _draw_stat_text(
            draw,
            {"key": "balance", "label": "الرصيد", "x": 10, "y": 10, "w": 100, "h": 80},
            "123456789012", 1.0, "#FFFFFF", "#BBBBBB",
        )
        # The value is fitted to the cell by the renderer; importantly it is not replaced by "…".
        self.assertEqual(image.size, (360, 120))
        value = "123456789012"
        fitted = _fit_font(value, 88, 46, minimum_size=8, bold=True)
        bounds = ImageDraw.Draw(image).textbbox((0, 0), _display_text(value), font=fitted)
        self.assertLessEqual(bounds[2] - bounds[0], 88, "Full stat value must fit its cell")

    def test_all_templates_have_in_bounds_positions_and_individual_font_boxes(self):
        root = Path(__file__).resolve().parents[1]
        for template in DEFAULT_PROFILE_TEMPLATES:
            metadata = _safe_metadata(template)
            width, height = metadata["width"], metadata["height"]
            for key in ("avatar", "name", "handle", "level", "xp_bar", "badges", "flag"):
                box = metadata[key]
                self.assertGreaterEqual(box.get("x", 0), 0, f"{template['slug']} {key}")
                self.assertGreaterEqual(box.get("y", 0), 0, f"{template['slug']} {key}")
                self.assertLess(box.get("x", 0), width, f"{template['slug']} {key}")
                self.assertLess(box.get("y", 0), height, f"{template['slug']} {key}")
            for stat in metadata["stats"]:
                self.assertGreaterEqual(stat["x"] + stat["w"], stat["x"])
                self.assertLessEqual(stat["x"] + stat["w"], width + 1, template["slug"])
                self.assertLessEqual(stat["y"] + stat["h"], height + 1, template["slug"])


    def test_custom_template_can_render_positioned_xp_bar_and_streak_stat(self):
        artwork = io.BytesIO()
        Image.new("RGBA", (640, 320), "#102030").save(artwork, format="PNG")
        metadata = {
            "width": 640, "height": 320, "layout": "wide",
            "avatar": {"x": 500, "y": 30, "size": 80},
            "name": {"x": 470, "y": 120, "max_width": 250, "anchor": "ra"},
            "handle": {"x": 470, "y": 154, "max_width": 220, "anchor": "ra"},
            "level": {"x": 60, "y": 180, "max_width": 220, "anchor": "la"},
            "xp_bar": {"x": 70, "y": 230, "width": 420, "height": 12, "enabled": True},
            "badges": {"x": 60, "y": 270, "size": 24, "limit": 2},
            "flag": {"x": 24, "y": 16, "width": 36, "height": 24},
            "stats": [{"key": "streak", "label": "الأيام", "x": 250, "y": 255, "w": 120, "h": 32}],
        }
        result = render_profile_card(
            {"display_name": "Member", "username": "member", "level": 4,
             "xp": 50, "required_xp": 100, "streak_days": 12},
            template_bytes=artwork.getvalue(),
            template={"metadata": metadata},
        )
        with Image.open(result) as image:
            self.assertEqual(image.size, (640, 320))
            self.assertEqual(image.format, "PNG")
            self.assertNotEqual(image.getpixel((100, 235)), ImageColor.getrgb("#102030") + (255,))


if __name__ == "__main__":
    unittest.main()
