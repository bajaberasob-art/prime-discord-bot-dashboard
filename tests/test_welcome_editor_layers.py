from io import BytesIO
from pathlib import Path
import unittest

from PIL import Image

from cogs.engagement import Engagement


ROOT = Path(__file__).resolve().parents[1]


class WelcomeStudioLayerTests(unittest.TestCase):
    def _avatar_bytes(self):
        avatar = Image.new("RGB", (32, 32), (80, 110, 180))
        stream = BytesIO()
        avatar.save(stream, format="PNG")
        return stream.getvalue()

    def test_custom_shape_layer_is_rendered_to_final_welcome_image(self):
        output = Engagement._render_welcome_image(
            self._avatar_bytes(), "Test Member", "Test Server", 42, "#7c3aed",
            canvas_config={"elements": [{
                "id": "shape-test", "type": "shape", "x": 40, "y": 30,
                "width": 20, "height": 40, "fontSize": 24,
                "color": "#00ff00", "visible": True, "locked": False,
                "opacity": 100, "rotation": 0, "align": "left", "text": "",
            }]},
        )
        result = Image.open(output).convert("RGB")
        self.assertEqual(result.size, (1200, 420))
        self.assertEqual(result.getpixel((600, 210)), (0, 255, 0))

    def test_hidden_layer_does_not_appear_in_final_welcome_image(self):
        output = Engagement._render_welcome_image(
            self._avatar_bytes(), "Test Member", "Test Server", 42, "#7c3aed",
            canvas_config={"elements": [{
                "id": "shape-hidden", "type": "shape", "x": 40, "y": 30,
                "width": 20, "height": 40, "fontSize": 24,
                "color": "#00ff00", "visible": False, "locked": False,
                "opacity": 100, "rotation": 0, "align": "left", "text": "",
            }]},
        )
        result = Image.open(output).convert("RGB")
        self.assertNotEqual(result.getpixel((600, 210)), (0, 255, 0))

    def test_editor_exposes_drag_layer_and_property_controls(self):
        script = (ROOT / "dashboard" / "welcome-editor.js").read_text(encoding="utf-8")
        self.assertIn("pointerdown", script)
        self.assertIn("setPointerCapture", script)
        self.assertIn("startResize", script)
        self.assertIn("welcome-editor-resize-handle", (ROOT / "dashboard" / "welcome-editor.css").read_text(encoding="utf-8"))
        self.assertIn("data-layers", script)
        self.assertIn("el.visible = !el.visible", script)
        self.assertIn("el.locked = !el.locked", script)
        self.assertIn("data-add", script)

    def test_layer_schema_is_validated_and_persisted(self):
        source = (ROOT / "database.py").read_text(encoding="utf-8")
        self.assertIn('or k == "elements"', source)
        self.assertIn('elif field == "elements":', source)
        self.assertIn('"frame"', source)

    def test_welcome_studio_allows_free_positions_and_preserves_background(self):
        script = (ROOT / "dashboard" / "welcome-editor.js").read_text(encoding="utf-8")
        renderer = (ROOT / "cogs" / "engagement.py").read_text(encoding="utf-8")
        database = (ROOT / "database.py").read_text(encoding="utf-8")
        server = (ROOT / "web_server.py").read_text(encoding="utf-8")
        self.assertIn("freeform elements are the source of truth", script)
        self.assertNotIn("current.name_y =", script)
        self.assertNotIn("current.avatar_x =", script)
        self.assertIn('data-background-slot="1"', script)
        self.assertIn('data-background-slot="4"', script)
        self.assertIn('"/onboarding/welcome-background?slot="', script)
        self.assertIn("current.background_slot", script)
        self.assertIn("loadBackgroundSlotPreviews", script)
        self.assertIn("data-slot-thumb=\"4\"", script)
        self.assertIn("if (Number(current.background_slot || 1) === slot) current.use_custom_background = false;", script)
        self.assertIn("slot=slot", server)
        self.assertIn("CHECK (slot BETWEEN 1 AND 4)", database)
        self.assertIn("get_welcome_background_asset(", renderer)
        self.assertIn("slot=background_slot", renderer)
        self.assertIn('"welcome_canvas_config": (', renderer)
        self.assertIn('"welcome_canvas_config": {}', renderer)
        self.assertIn("member = guild.get_member(int(actor_user_id))", renderer)
        self.assertIn('trigger_type == "dashboard_test"', renderer)
        self.assertIn('"error": "welcome_image_generation_failed"', renderer)
        self.assertIn('if "slot" not in welcome_asset_columns', database)
        self.assertIn("SELECT guild_id, 1, image_data, updated_at", database)
        self.assertIn("CREATE TABLE IF NOT EXISTS welcome_background_assets", database)
        self.assertIn("save_welcome_background_asset(guild.id, normalized_image, slot=slot)", server)
        self.assertIn("delete_welcome_background_asset(guild.id, slot=slot)", server)
        self.assertIn("ImageOps.contain(custom_rgba, (width, height)", renderer)
        self.assertIn("ImageFilter.GaussianBlur(radius=22)", renderer)
        self.assertIn("backdrop.paste(foreground, (x_offset, y_offset), foreground.getchannel(\"A\"))", renderer)

    def test_welcome_typography_controls_are_validated_and_persisted(self):
        from database import validate_setting

        config = validate_setting("welcome_canvas_config", {
            "elements": [{
                "id": "arabic-font", "type": "text", "label": "عنوان",
                "x": 5, "y": 5, "width": 80, "height": 25, "fontSize": 34,
                "color": "#ffffff", "visible": True, "locked": False,
                "opacity": 100, "rotation": 0, "align": "right",
                "text": "أهلاً بك في PRIME", "fontPreset": "luxury", "fontFamily": "serif",
                "fontWeight": "bold", "fontStyle": "italic",
                "textTransform": "uppercase",
            }]
        })
        element = config["elements"][0]
        self.assertEqual(element["fontPreset"], "luxury")
        self.assertEqual(element["fontFamily"], "serif")
        self.assertEqual(element["fontWeight"], "bold")
        self.assertEqual(element["fontStyle"], "italic")
        self.assertEqual(element["textTransform"], "uppercase")

        with self.assertRaises(ValueError):
            validate_setting("welcome_canvas_config", {
                "elements": [{
                    "id": "bad-font", "type": "text", "x": 5, "y": 5,
                    "width": 80, "height": 25, "fontSize": 34,
                    "color": "#ffffff", "fontFamily": "unsupported-font",
                }]
            })
        with self.assertRaises(ValueError):
            validate_setting("welcome_canvas_config", {
                "elements": [{
                    "id": "bad-preset", "type": "text", "x": 5, "y": 5,
                    "width": 80, "height": 25, "fontSize": 34,
                    "color": "#ffffff", "fontPreset": "unsupported-preset",
                }]
            })

        editor = (ROOT / "dashboard" / "welcome-editor.js").read_text(encoding="utf-8")
        renderer = (ROOT / "cogs" / "engagement.py").read_text(encoding="utf-8")
        self.assertIn('propertyField("النمط الجاهز (عربي/إنجليزي)","fontPreset"', editor)
        self.assertIn('"modern_bold"', editor)
        self.assertIn('"formal"', editor)
        self.assertIn('"tech"', editor)
        self.assertIn('"gaming"', editor)
        self.assertIn('propertyField("نوع الخط","fontFamily"', editor)
        self.assertIn('propertyField("سماكة الخط","fontWeight"', editor)
        self.assertIn('propertyField("ميل الخط","fontStyle"', editor)
        self.assertIn('propertyField("تنسيق الإنجليزية","textTransform"', editor)
        self.assertIn("node.style.fontWeight", editor)
        self.assertIn("arabic_reshaper.reshape(text)", renderer)
        self.assertIn("def _welcome_font(", renderer)
        self.assertIn("def _draw_welcome_text(", renderer)
        self.assertIn("manager.draw(", renderer)
        self.assertIn("textTransform", renderer)
        self.assertIn('"modern_bold": ("sans", "bold", "normal", "uppercase")', renderer)
        self.assertIn('"luxury": ("serif", "normal", "italic", "capitalize")', renderer)

    def test_welcome_background_upload_handles_disconnect_and_retries(self):
        server = (ROOT / "web_server.py").read_text(encoding="utf-8")
        script = (ROOT / "dashboard" / "welcome-editor.js").read_text(encoding="utf-8")
        self.assertIn("except (ConnectionResetError, asyncio.IncompleteReadError, aiohttp.ClientPayloadError)", server)
        self.assertIn('response.status != 499', server)
        self.assertIn("attempt < 2 && !uploaded", script)
        self.assertIn("انقطع الاتصال أثناء الرفع؛ جارٍ إعادة المحاولة", script)
        # The background file is only replaced after the complete image has been verified.
        self.assertLess(server.index("image.verify()"), server.index("temporary.replace(path)"))

    def test_editor_treats_unchanged_design_save_as_success(self):
        editor = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const saved = onboardingDirty() ? await saveOnboarding() : true;", editor)
        app = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
        self.assertIn("state.draft.welcome_generated_image_enabled = true;", app)
        self.assertIn("same generated PNG delivered to members", app)

    def test_welcome_background_slot_validation_accepts_only_slots_one_to_four(self):
        from database import validate_setting

        accepted = validate_setting("welcome_canvas_config", {"background_slot": 4})
        self.assertEqual(accepted["background_slot"], 4)
        with self.assertRaises(ValueError):
            validate_setting("welcome_canvas_config", {"background_slot": 5})
        with self.assertRaises(ValueError):
            validate_setting("welcome_canvas_config", {"background_slot": 0})

    def test_welcome_canvas_formats_render_with_distinct_aspect_ratios(self):
        avatar = self._avatar_bytes()
        cases = [
            ("banner", (1200, 420)),
            ("landscape", (1200, 675)),
            ("square", (1000, 1000)),
            ("portrait", (800, 1200)),
        ]
        for canvas_format, expected_size in cases:
            with self.subTest(canvas_format=canvas_format):
                output = Engagement._render_welcome_image(
                    avatar, "Test Member", "Test Server", 42, "#7c3aed",
                    canvas_config={"canvas_format": canvas_format},
                )
                self.assertEqual(Image.open(output).size, expected_size)

    def test_auto_canvas_uses_uploaded_background_aspect_ratio(self):
        avatar = self._avatar_bytes()
        background = Image.new("RGB", (400, 800), (25, 40, 60))
        stream = BytesIO()
        background.save(stream, format="PNG")
        output = Engagement._render_welcome_image(
            avatar, "Test Member", "Test Server", 42, "#7c3aed",
            canvas_config={
                "canvas_format": "auto",
                "use_custom_background": True,
                "_background_image_data": stream.getvalue(),
            },
            guild_id=123,
        )
        self.assertEqual(Image.open(output).size, (600, 1200))

    def test_service_worker_cache_matches_welcome_editor_assets(self):
        server = (ROOT / "web_server.py").read_text(encoding="utf-8")
        index = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
        self.assertIn("prime-dashboard-shell-v67", server)
        self.assertIn("welcome-editor.css?v=welcome-editor-10", server)
        self.assertIn("welcome-editor.js?v=welcome-editor-13", server)
        self.assertIn("welcome-editor.css?v=welcome-editor-10", index)
        self.assertIn("welcome-editor.js?v=welcome-editor-13", index)


    def test_arabic_welcome_uses_single_shaping_pass_and_bundled_font(self):
        from PIL import ImageFont

        logical_text = "يا هلاااااا والله"
        shaped = Engagement._welcome_image_text(logical_text)
        self.assertNotEqual(shaped, logical_text)
        font = Engagement._welcome_font(34, weight="bold")
        basic = getattr(ImageFont, "Layout", None)
        expected_engine = basic.BASIC if basic is not None else ImageFont.LAYOUT_BASIC
        self.assertEqual(font.layout_engine, expected_engine)
        self.assertTrue(Path(ROOT / "assets" / "fonts" / "DejaVuSans-Bold.ttf").is_file())

    def test_arabic_and_mixed_text_are_actually_rasterized_into_welcome_png(self):
        avatar = self._avatar_bytes()
        elements = [
            {
                "id": "arabic-welcome", "type": "text", "x": 5, "y": 12,
                "width": 90, "height": 25, "fontSize": 42,
                "color": "#ffffff", "visible": True, "opacity": 100,
                "rotation": 0, "align": "right", "text": "يا هلاااااا والله",
                "fontFamily": "sans", "fontWeight": "bold", "fontStyle": "normal",
            },
            {
                "id": "mixed-welcome", "type": "text", "x": 5, "y": 42,
                "width": 90, "height": 25, "fontSize": 30,
                "color": "#ff3030", "visible": True, "opacity": 100,
                "rotation": 0, "align": "right", "text": "أهلاً بك في PRIME 515",
                "fontFamily": "sans", "fontWeight": "normal", "fontStyle": "normal",
            },
        ]
        output = Engagement._render_welcome_image(
            avatar, "عضو جديد", "PRIME", 515, "#ff3030",
            canvas_config={"elements": elements},
        )
        rendered = Image.open(output).convert("RGB")
        # Both designated text regions must contain actual glyph pixels, not empty
        # output or missing-glyph-only rendering.
        for region in ((40, 45, 1140, 150), (40, 170, 1140, 285)):
            with self.subTest(region=region):
                crop = rendered.crop(region)
                colors = crop.getcolors(maxcolors=crop.width * crop.height)
                self.assertIsNotNone(colors)
                non_background = sum(
                    count for count, rgb in colors if rgb != rendered.getpixel((0, 0))
                )
                self.assertGreater(non_background, 100)


if __name__ == "__main__":
    unittest.main()
