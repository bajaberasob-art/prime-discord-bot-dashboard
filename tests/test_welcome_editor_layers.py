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
        self.assertIn("freeform elements are the source of truth", script)
        self.assertNotIn("current.name_y =", script)
        self.assertNotIn("current.avatar_x =", script)
        self.assertIn('data-background-slot="1"', script)
        self.assertIn('data-background-slot="4"', script)
        self.assertIn('"/onboarding/welcome-background?slot="', script)
        self.assertIn("current.background_slot", script)
        self.assertIn("slot=slot", server)
        self.assertIn("CHECK (slot BETWEEN 1 AND 4)", database)
        self.assertIn("get_welcome_background_asset(\\n                        guild.id, slot=background_slot", renderer)
        self.assertIn('"welcome_canvas_config": (', renderer)
        self.assertIn('"welcome_canvas_config": {}', renderer)
        database = (ROOT / "database.py").read_text(encoding="utf-8")
        server = (ROOT / "web_server.py").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS welcome_background_assets", database)
        self.assertIn("save_welcome_background_asset(guild.id, normalized_image, slot=slot)", server)
        self.assertIn("delete_welcome_background_asset(guild.id, slot=slot)", server)
        self.assertIn("ImageOps.contain(custom_rgba, (width, height)", renderer)
        self.assertIn("ImageFilter.GaussianBlur(radius=22)", renderer)
        self.assertIn("backdrop.paste(foreground, (x_offset, y_offset), foreground.getchannel(\"A\"))", renderer)

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
        self.assertIn("prime-dashboard-shell-v64", server)
        self.assertIn("welcome-editor.css?v=welcome-editor-9", server)
        self.assertIn("welcome-editor.js?v=welcome-editor-10", server)
        self.assertIn("welcome-editor.css?v=welcome-editor-9", index)
        self.assertIn("welcome-editor.js?v=welcome-editor-10", index)


if __name__ == "__main__":
    unittest.main()
