import io
import unittest
from types import SimpleNamespace

from PIL import Image, ImageChops

from game_engine import (
    BACKGROUND,
    UniversalLobbyView,
    create_base_canvas,
    draw_arabic_text,
    get_circular_avatar,
)


class VisualGameEngineTests(unittest.IsolatedAsyncioTestCase):
    def test_base_canvas_has_requested_dimensions_and_png_compatibility(self):
        image = create_base_canvas(960, 540, "PRIME ألعاب", "اختبار اللوبي")
        self.assertEqual(image.size, (960, 540))
        self.assertEqual(image.mode, "RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(image.getpixel((15, 15)), tuple(bytes.fromhex(BACKGROUND[1:])))

    def test_draw_arabic_text_accepts_mixed_arabic_english_and_symbols(self):
        image = Image.new("RGB", (480, 120), BACKGROUND)
        from PIL import ImageDraw
        before = image.copy()
        draw = ImageDraw.Draw(image)
        draw_arabic_text(draw, (450, 20), "توقفت الموسيقى! Round 2 🎵")
        self.assertIsNotNone(ImageChops.difference(image, before).getbbox())

    async def test_missing_avatar_url_returns_circular_placeholder(self):
        user = SimpleNamespace(id=5, name="Tester", display_name="Tester", display_avatar=None)
        avatar = await get_circular_avatar(user, 64)
        self.assertEqual(avatar.size, (64, 64))
        self.assertEqual(avatar.mode, "RGBA")
        self.assertEqual(avatar.getpixel((0, 0))[3], 0)
        self.assertEqual(avatar.getpixel((32, 32))[3], 255)

    async def test_lobby_validates_player_limits_and_starts_with_host(self):
        async def start_callback(interaction, players):
            return None

        host = SimpleNamespace(id=10, name="Host", display_name="Host", mention="<@10>")
        view = UniversalLobbyView(
            host,
            game_name="الكراسي الموسيقية",
            rules="لا تضغط أثناء الموسيقى.",
            on_start=start_callback,
            min_players=3,
            max_players=10,
        )
        self.assertEqual(list(view.players), [10])
        self.assertEqual(view.min_players, 3)
        self.assertEqual(view.max_players, 10)
        self.assertTrue(view.start_button.disabled)
        self.assertEqual(len(view.children), 4)
        await view.on_timeout()
        self.assertTrue(view.closed)
        self.assertTrue(view.join_button.disabled)
        self.assertTrue(view.rules_button.disabled)

    async def test_lobby_render_produces_a_discord_png_file(self):
        async def start_callback(interaction, players):
            return None

        host = SimpleNamespace(
            id=10,
            name="Host",
            display_name="Host",
            mention="<@10>",
            display_avatar=None,
        )
        view = UniversalLobbyView(
            host,
            game_name="الكراسي الموسيقية",
            rules="انتظر توقف الموسيقى.",
            on_start=start_callback,
            min_players=2,
            max_players=4,
        )
        output = await view.render_lobby_file()
        try:
            self.assertEqual(output.filename, "prime-lobby.png")
            self.assertTrue(output.fp.read().startswith(b"\\x89PNG\\r\\n\\x1a\\n"))
        finally:
            output.close()

    async def test_invalid_lobby_limits_are_rejected(self):
        async def start_callback(interaction, players):
            return None

        host = SimpleNamespace(id=10, name="Host", display_name="Host", mention="<@10>")
        with self.assertRaisesRegex(ValueError, "invalid_player_limits"):
            UniversalLobbyView(
                host,
                game_name="Invalid",
                rules="",
                on_start=start_callback,
                min_players=4,
                max_players=3,
            )


if __name__ == "__main__":
    unittest.main()
