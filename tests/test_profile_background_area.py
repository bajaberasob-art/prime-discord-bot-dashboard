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


    def test_admin_sections_are_split_into_tabs_without_renaming_existing_editor_fields(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "dashboard" / "profile-store-admin.html").read_text("utf-8")
        client = (root / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        for tab in ("overview", "products", "design", "categories", "settings"):
            self.assertIn(f'data-admin-tab="{tab}"', html)
            self.assertIn(f'data-admin-pane="{tab}"', html)
        self.assertIn('id="designTemplateSelect"', html)
        self.assertIn('id="designLoadTemplate"', html)
        self.assertIn("function switchAdminTab(name)", client)
        self.assertIn("function updateDesignTemplateOptions()", client)
        self.assertIn("edit(Number(selected.item_id))", client)
        # Existing control IDs remain stable for the current API/form implementation.
        for field_id in ("itemForm", "itemId", "saveItem", "layoutField", "saveLayout", "backgroundFit"):
            self.assertIn(f'id="{field_id}"', html)


    def test_renderer_applies_per_element_text_style(self):
        frame = png_bytes(Image.new("RGBA", (640, 320), (0, 0, 0, 0)))
        metadata = {
            "width": 640,
            "height": 320,
            "layout": "wide",
            "background_area": {"x": 20, "y": 20, "width": 600, "height": 280, "fit": "cover"},
            "avatar": {"x": 520, "y": 220, "size": 72},
            "name": {
                "x": 50, "y": 65, "max_width": 260, "anchor": "la",
                "font_size": 34, "font_family": "elegant", "color": "#00FF00",
                "bold": False, "stroke_width": 0,
            },
            "handle": {"x": 50, "y": 110, "max_width": 240, "anchor": "la"},
            "level": {"x": 50, "y": 155, "max_width": 220, "anchor": "la"},
            "xp_bar": {"x": 30, "y": 280, "width": 120, "height": 8, "enabled": False},
            "badges": {"x": 10, "y": 10, "size": 20, "limit": 0},
            "flag": {"x": 10, "y": 10, "width": 24, "height": 18},
            "stats": [],
        }
        result = render_profile_card(
            {"display_name": "StyledUser", "username": "member", "level": 2},
            template_bytes=frame,
            template={"metadata": metadata},
        )
        with Image.open(result) as card:
            pixels = card.convert("RGBA").getdata()
            green_pixels = sum(
                1 for red, green, blue, alpha in pixels
                if alpha > 0 and green > 180 and red < 80 and blue < 80
            )
        self.assertGreater(green_pixels, 10, "The configured element color must reach the rendered PNG")

    def test_renderer_applies_custom_style_to_stat_value(self):
        frame = png_bytes(Image.new("RGBA", (640, 320), (0, 0, 0, 0)))
        metadata = {
            "width": 640,
            "height": 320,
            "layout": "wide",
            "background_area": {"x": 20, "y": 20, "width": 600, "height": 280, "fit": "cover"},
            "avatar": {"x": 520, "y": 220, "size": 72},
            "name": {"x": 590, "y": 35, "max_width": 220, "anchor": "ra"},
            "handle": {"x": 590, "y": 75, "max_width": 220, "anchor": "ra"},
            "level": {"x": 30, "y": 35, "max_width": 220, "anchor": "la"},
            "xp_bar": {"x": 30, "y": 280, "width": 120, "height": 8, "enabled": False},
            "badges": {"x": 10, "y": 10, "size": 20, "limit": 0},
            "flag": {"x": 10, "y": 10, "width": 24, "height": 18},
            "stats": [{
                "key": "messages", "label": "Messages", "x": 190, "y": 150,
                "w": 220, "h": 100, "font_size": 36, "font_family": "elegant",
                "color": "#00FF00", "bold": False, "stroke_width": 0,
            }],
        }
        result = render_profile_card(
            {"display_name": "Member", "username": "member", "level": 2, "messages": 12345},
            template_bytes=frame,
            template={"metadata": metadata},
        )
        with Image.open(result) as card:
            pixels = card.convert("RGBA").getdata()
            green_pixels = sum(
                1 for red, green, blue, alpha in pixels
                if alpha > 0 and green > 180 and red < 80 and blue < 80
            )
        self.assertGreater(green_pixels, 10, "The stat-value style must be rendered")

    def test_level_text_template_is_rendered_with_live_level_value(self):
        frame = png_bytes(Image.new("RGBA", (640, 320), (0, 0, 0, 0)))
        base_metadata = {
            "width": 640,
            "height": 320,
            "layout": "wide",
            "background_area": {"x": 20, "y": 20, "width": 600, "height": 280, "fit": "cover"},
            "avatar": {"x": 520, "y": 220, "size": 72},
            "name": {"x": 590, "y": 35, "max_width": 220, "anchor": "ra"},
            "handle": {"x": 590, "y": 75, "max_width": 220, "anchor": "ra"},
            "level": {"x": 50, "y": 145, "max_width": 220, "anchor": "la"},
            "xp_bar": {"x": 30, "y": 280, "width": 120, "height": 8, "enabled": False},
            "badges": {"x": 10, "y": 10, "size": 20, "limit": 0},
            "flag": {"x": 10, "y": 10, "width": 24, "height": 18},
            "stats": [],
        }
        profile = {"display_name": "Member", "username": "member", "level": 42}
        default_result = render_profile_card(
            profile, template_bytes=frame, template={"metadata": base_metadata}
        )
        custom_metadata = dict(base_metadata)
        custom_metadata["level"] = {**base_metadata["level"], "text_template": "LVL {level}"}
        custom_result = render_profile_card(
            profile, template_bytes=frame, template={"metadata": custom_metadata}
        )
        with Image.open(default_result) as default_card, Image.open(custom_result) as custom_card:
            self.assertNotEqual(
                default_card.convert("RGBA").tobytes(),
                custom_card.convert("RGBA").tobytes(),
                "Changing the level text template should change the rendered image",
            )

    def test_invalid_element_style_values_fall_back_safely(self):
        frame = png_bytes(Image.new("RGBA", (640, 320), (0, 0, 0, 0)))
        metadata = {
            "width": 640,
            "height": 320,
            "layout": "wide",
            "background_area": {"x": 20, "y": 20, "width": 600, "height": 280, "fit": "cover"},
            "avatar": {"x": 520, "y": 220, "size": 72},
            "name": {
                "x": 50, "y": 65, "max_width": 260, "anchor": "la",
                "font_size": "invalid", "font_family": "not-a-font",
                "color": "not-a-color", "bold": "false", "stroke_width": "999",
            },
            "handle": {"x": 50, "y": 110, "max_width": 240, "anchor": "la"},
            "level": {"x": 50, "y": 155, "max_width": 220, "anchor": "la"},
            "xp_bar": {"x": 30, "y": 280, "width": 120, "height": 8, "enabled": False},
            "badges": {"x": 10, "y": 10, "size": 20, "limit": 0},
            "flag": {"x": 10, "y": 10, "width": 24, "height": 18},
            "stats": [],
        }
        result = render_profile_card(
            {"display_name": "SafeFallback", "username": "member", "level": 2},
            template_bytes=frame,
            template={"metadata": metadata},
        )
        with Image.open(result) as card:
            self.assertEqual(card.format, "PNG")
            self.assertEqual(card.size, (640, 320))

    def test_design_studio_exposes_per_element_style_and_persists_metadata(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / "dashboard" / "profile-store-admin.html").read_text("utf-8")
        client = (root / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        renderer = (root / "profile_card_generator.py").read_text("utf-8")
        for field_id in (
            "layoutFontSize", "layoutFontFamily", "layoutTextColor",
            "layoutTextContent", "layoutAutoColor", "layoutBold",
            "layoutStrokeWidth", "applyElementStyle",
        ):
            self.assertIn(f'id="{field_id}"', html)
        self.assertIn("function applyElementStyle()", client)
        self.assertIn("box.font_size=Math.round(fontSize)", client)
        self.assertIn("delete box.color", client)
        self.assertIn("box.font_family", client)
        self.assertIn("box.text_template=textContent", client)
        self.assertIn("box.label=textContent", client)
        self.assertIn('level_template = str(level_box.get("text_template") or "المستوى {level}")[:100]', renderer)
        self.assertIn("_clamp(name_box.get(\"font_size\")", renderer)
        self.assertIn("_safe_color(name_box.get(\"color\"), light_text)", renderer)
        self.assertIn('stat.get("font_size")', renderer)

if __name__ == "__main__":
    unittest.main()
