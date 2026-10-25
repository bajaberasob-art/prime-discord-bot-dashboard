"""Regression tests for the image-aware PRIME profile layout engine."""
from __future__ import annotations

import io
import unittest

from PIL import Image, ImageDraw

import profile_card_generator as cards
import profile_store_db


def _template_png() -> bytes:
    image = Image.new("RGBA", (640, 360), (13, 18, 32, 255))
    draw = ImageDraw.Draw(image)
    # Decorative rails and a prominent avatar ring create realistic occupied regions.
    draw.rounded_rectangle((8, 8, 632, 352), radius=24, outline=(80, 180, 240, 255), width=5)
    draw.ellipse((475, 205, 595, 325), outline=(100, 210, 255, 255), width=9)
    image.paste((8, 10, 20, 255), (500, 230, 570, 300))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


class PrimeSmartProfileLayoutTests(unittest.TestCase):
    def test_text_box_avoids_reserved_avatar_and_stat_regions(self):
        frame = _template_png()
        reserved = [(455, 185, 615, 345), (0, 285, 640, 360)]
        box = cards._choose_template_text_box(
            frame,
            width=640,
            height=360,
            box_width=230,
            box_height=34,
            reserved=reserved,
            y_range=(0.12, 0.68),
        )
        self.assertIsNotNone(box)
        rect = box["_rect"]
        self.assertFalse(any(cards._rect_overlaps(rect, item) for item in reserved))

    def test_layout_marks_image_analysis_and_keeps_stats_anchors(self):
        frame = _template_png()
        metadata = {
            "width": 640,
            "height": 360,
            "layout": "wide",
            "avatar": {"x": 480, "y": 210, "size": 100},
            "name": {"x": 440, "y": 160, "max_width": 260, "anchor": "ra"},
            "handle": {"x": 440, "y": 185, "max_width": 260, "anchor": "ra"},
            "level": {"x": 35, "y": 230, "max_width": 240, "anchor": "la"},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 20, "y": 290, "w": 130, "h": 45},
                {"key": "messages", "label": "الرسائل", "x": 165, "y": 290, "w": 130, "h": 45},
            ],
        }
        result = cards._apply_smart_template_layout(metadata, frame)
        self.assertEqual(result["layout_engine"], "prime-vision-layout-v4")
        self.assertEqual(result["stats"], metadata["stats"])
        self.assertIn("name_region_optimized", result["layout_analysis"])
        self.assertIn("level_region_optimized", result["layout_analysis"])
        self.assertTrue(0 <= result["avatar"]["x"] < result["width"])
        self.assertTrue(0 <= result["avatar"]["y"] < result["height"])

    def test_all_approved_raster_templates_use_actual_profile_text_and_avoid_overlap(self):
        sample_profile = {
            "display_name": "aboody_515",
            "username": "aboody_515",
            "level": 117,
            "balance": 325,
            "messages": 483,
            "rank": 1,
            "voice_seconds": 3600,
        }
        checked = 0
        for item in profile_store_db.DEFAULT_PROFILE_TEMPLATES:
            with self.subTest(template=item["slug"]):
                raw = cards._read_local_asset(item["asset_path"], template_asset=True)
                self.assertIsNotNone(raw, f"approved asset missing: {item['asset_path']}")
                metadata = cards._metadata_for_native_frame(
                    cards._safe_metadata({"metadata": item["metadata"]}), raw
                )
                result = cards._apply_smart_template_layout(
                    metadata, raw, profile=sample_profile, style=item["style"]
                )
                self.assertEqual(result["layout_engine"], "prime-vision-layout-v4")
                self.assertTrue(result["layout_analysis"]["uses_profile_text_lengths"])
                self.assertEqual(result["stats"], metadata["stats"])
                # The solver must either produce a collision-free pair or keep the curated
                # metadata intact; it may not half-apply one position and leave another stale.
                self.assertEqual(
                    result["layout_analysis"]["layout_applied"],
                    result["layout_analysis"]["name_region_optimized"]
                    and result["layout_analysis"]["level_region_optimized"],
                )
                if result["layout_analysis"]["layout_applied"]:
                    identity_rect = tuple(result["layout_analysis"]["identity_rect"])
                    level_rect = tuple(result["layout_analysis"]["level_rect"])
                    self.assertFalse(cards._rect_overlaps(identity_rect, level_rect))
                    avatar = result["avatar"]
                    avatar_rect = (
                        avatar["x"], avatar["y"],
                        avatar["x"] + avatar["size"], avatar["y"] + avatar["size"],
                    )
                    self.assertFalse(cards._rect_overlaps(identity_rect, avatar_rect))
                    self.assertFalse(cards._rect_overlaps(level_rect, avatar_rect))
                    for stat in result["stats"]:
                        stat_rect = (stat["x"], stat["y"], stat["x"] + stat["w"], stat["y"] + stat["h"])
                        self.assertFalse(cards._rect_overlaps(identity_rect, stat_rect))
                        self.assertFalse(cards._rect_overlaps(level_rect, stat_rect))
                checked += 1
        self.assertGreaterEqual(checked, 7)

    def test_missing_template_image_keeps_curated_text_positions(self):
        metadata = {
            "width": 640,
            "height": 360,
            "avatar": {"x": 480, "y": 210, "size": 100},
            "name": {"x": 420, "y": 150, "max_width": 260, "anchor": "ra"},
            "handle": {"x": 420, "y": 175, "max_width": 260, "anchor": "ra"},
            "level": {"x": 40, "y": 225, "max_width": 240, "anchor": "la"},
            "stats": [],
        }
        result = cards._apply_smart_template_layout(metadata, None)
        self.assertEqual(result["name"], metadata["name"])
        self.assertEqual(result["handle"], metadata["handle"])
        self.assertEqual(result["level"], metadata["level"])


if __name__ == "__main__":
    unittest.main()
