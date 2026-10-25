import io
import unittest
from pathlib import Path

from PIL import Image

from profile_store_db import DEFAULT_PROFILE_TEMPLATES
from profile_card_generator import render_profile_card, _safe_metadata


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


if __name__ == "__main__":
    unittest.main()
