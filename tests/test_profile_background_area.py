"""Regression coverage for per-template profile-card background geometry."""

import io
import unittest
from pathlib import Path

from PIL import Image

from profile_card_generator import _fit_background_to_area, _safe_metadata, render_profile_card
from profile_store_admin import default_uploaded_template_metadata


def png_bytes(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class ProfileBackgroundAreaTests(unittest.TestCase):
    def test_background_area_is_clamped_and_fit_mode_is_normalized(self):
        metadata = _safe_metadata({
            "metadata": {
                "width": 640,
                "height": 320,
                "background_area": {
                    "x": 600, "y": 300, "width": 100, "height": 80, "fit": "stretch"
                },
            }
        })
        self.assertEqual(
            metadata["background_area"],
            {"x": 600, "y": 300, "width": 40, "height": 20, "fit": "cover"},
        )

    def test_existing_template_without_area_gets_safe_inset_default(self):
        metadata = _safe_metadata({"metadata": {"width": 640, "height": 320}})
        area = metadata["background_area"]
        self.assertGreater(area["x"], 0)
        self.assertGreater(area["y"], 0)
        self.assertLess(area["x"] + area["width"], metadata["width"])
        self.assertLess(area["y"] + area["height"], metadata["height"])
        self.assertEqual(area["fit"], "cover")

    def test_background_image_is_visible_only_inside_configured_area(self):
        frame = png_bytes(Image.new("RGBA", (640, 320), (0, 0, 0, 0)))
        background = png_bytes(Image.new("RGB", (300, 180), "#ff0000"))
        metadata = {
            "width": 640,
            "height": 320,
            "layout": "wide",
            "background_area": {"x": 80, "y": 40, "width": 400, "height": 200, "fit": "cover"},
            "avatar": {"x": 530, "y": 230, "size": 72},
            "name": {"x": 620, "y": 10, "max_width": 100, "anchor": "ra"},
            "handle": {"x": 620, "y": 32, "max_width": 100, "anchor": "ra"},
            "level": {"x": 10, "y": 300, "max_width": 120, "anchor": "la"},
            "xp_bar": {"x": 10, "y": 285, "width": 120, "height": 8, "enabled": False},
            "badges": {"x": 10, "y": 250, "size": 20, "limit": 1},
            "flag": {"x": 10, "y": 10, "width": 24, "height": 18},
            "stats": [],
        }
        result = render_profile_card(
            {"display_name": "Member", "username": "member", "level": 2},
            background_bytes=background,
            template_bytes=frame,
            template={"metadata": metadata},
        )
        with Image.open(result) as card:
            card.load()
            inside = card.getpixel((120, 100))
            outside = card.getpixel((35, 100))
        self.assertGreater(inside[0], 130, f"Configured area should show background red: {inside}")
        self.assertLess(inside[1], 45, f"Configured area should retain source color: {inside}")
        self.assertGreater(
            inside[0], outside[0] + 45,
            f"Background must not spill outside configured area; inside={inside}, outside={outside}",
        )

    def test_contain_preserves_full_image_and_leaves_unused_area_transparent(self):
        source = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
        fitted = _fit_background_to_area(source, (100, 100), "contain")
        self.assertEqual(fitted.size, (100, 100))
        self.assertEqual(fitted.getpixel((50, 50)), (255, 0, 0, 255))
        self.assertEqual(fitted.getpixel((50, 5))[3], 0)
        self.assertEqual(fitted.getpixel((50, 94))[3], 0)

    def test_new_upload_metadata_has_editable_inset_background_area(self):
        for width, height in ((1024, 1536), (1860, 845)):
            with self.subTest(size=(width, height)):
                metadata = default_uploaded_template_metadata(width, height)
                area = metadata["background_area"]
                self.assertIn(area["fit"], {"cover", "contain"})
                self.assertGreater(area["x"], 0)
                self.assertGreater(area["y"], 0)
                self.assertGreater(area["width"], 0)
                self.assertGreater(area["height"], 0)
                self.assertLessEqual(area["x"] + area["width"], metadata["width"])
                self.assertLessEqual(area["y"] + area["height"], metadata["height"])

    def test_admin_editor_has_real_background_preview_and_verified_layout_save(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "dashboard" / "profile-store-admin.html").read_text("utf-8")
        client = (root / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        self.assertIn('id="layoutBackgroundPreview"', html)
        self.assertIn('id="layoutBackgroundSource"', html)
        self.assertIn("function updateBackgroundPreview()", client)
        self.assertIn("preview.style.left=(area.x/meta.width*100)+'%'", client)
        self.assertIn("preview.style.objectFit=area.fit==='contain'?'contain':'cover'", client)
        self.assertIn("إعادة تحميل بيانات المتجر", client)
        self.assertIn("JSON.stringify(saved.metadata||{})!==JSON.stringify(desired.metadata)", client)
        self.assertIn('data-corner="nw"', client)
        self.assertIn('data-corner="ne"', client)
        self.assertIn('data-corner="sw"', client)
        self.assertIn('data-corner="se"', client)
        self.assertIn("if(layoutDrag.key==='background_area')", client)

    def test_admin_editor_exposes_drag_resize_and_fit_controls(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "dashboard" / "profile-store-admin.html").read_text("utf-8")
        client = (root / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        self.assertIn('option value="background_area"', html)
        self.assertIn('id="backgroundFit"', html)
        self.assertIn("layoutMetadata.background_area", client)
        self.assertIn("key==='background_area'?'background'", client)
        self.assertIn("box.width=width;box.height=height", client)
        self.assertIn("background_area:'مساحة الخلفية'", client)
        self.assertIn("b.fit=$('backgroundFit').value", client)


    def test_startup_seed_does_not_overwrite_saved_template_layout(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "profile_store_db.py").read_text("utf-8")
        self.assertIn("INSERT OR IGNORE INTO profile_store_items", source)
        self.assertIn("Do not rewrite", source)
        self.assertNotIn("style_json = :style_json, metadata_json = :metadata_json,", source)


if __name__ == "__main__":
    unittest.main()
