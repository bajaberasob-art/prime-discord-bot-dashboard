import io
import unittest
from pathlib import Path
from unittest.mock import patch

import cairosvg
from PIL import Image

from profile_store_db import DEFAULT_PROFILE_TEMPLATES
from profile_card_generator import render_profile_card, _safe_metadata


class ProfileCardTemplateTests(unittest.TestCase):
    def test_templates_are_separate_transparent_assets_with_varied_aspect_ratios(self):
        root = Path(__file__).resolve().parents[1]
        dimensions = set()
        filenames = set()

        for template in DEFAULT_PROFILE_TEMPLATES:
            path = root / template["asset_path"]
            self.assertTrue(path.is_file(), template["slug"])
            filenames.add(path.name)
            metadata = template["metadata"]
            dimensions.add((metadata["width"], metadata["height"]))
            rendered = cairosvg.svg2png(bytestring=path.read_bytes())
            with Image.open(io.BytesIO(rendered)) as image:
                self.assertEqual(image.size, (metadata["width"], metadata["height"]))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 0, template["slug"])

        self.assertEqual(len(filenames), len(DEFAULT_PROFILE_TEMPLATES))
        self.assertGreaterEqual(len(dimensions), 4)
        self.assertTrue(any(width > height for width, height in dimensions))
        self.assertTrue(any(height > width for width, height in dimensions))
        self.assertTrue(any(width == height for width, height in dimensions))

    def test_rendered_card_preserves_template_dimensions_and_has_arabic_content(self):
        avatar_buffer = io.BytesIO()
        Image.new("RGB", (128, 128), "#FF78B7").save(avatar_buffer, format="PNG")
        avatar = avatar_buffer.getvalue()

        for slug in ("profile-neon-wide", "profile-rose-portrait", "profile-emerald-square"):
            template = next(item for item in DEFAULT_PROFILE_TEMPLATES if item["slug"] == slug)
            frame = cairosvg.svg2png(
                bytestring=(Path(__file__).resolve().parents[1] / template["asset_path"]).read_bytes()
            )
            result = render_profile_card(
                {
                    "display_name": "لونا • Lona",
                    "username": "lona",
                    "level": 25,
                    "xp": 4894,
                    "required_xp": 9120,
                    "balance": 30300,
                    "messages": 11400,
                    "rank": 724,
                    "voice_seconds": 82800,
                },
                avatar_bytes=avatar,
                template_bytes=frame,
                template=template,
            )
            self.assertEqual(result.tell(), 0)
            with Image.open(result) as card:
                self.assertEqual(
                    card.size,
                    (template["metadata"]["width"], template["metadata"]["height"]),
                )
                self.assertEqual(card.mode, "RGBA")
                self.assertEqual(card.format, "PNG")

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
