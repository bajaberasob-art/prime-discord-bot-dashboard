"""Integration tests for direct level-card media uploads."""
import io
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import leveling_api
from cogs import card_images


GUILD_ID = 1516185800146944000


def png_bytes():
    output = io.BytesIO()
    Image.new("RGB", (32, 24), "#26cbab").save(output, format="PNG")
    return output.getvalue()


def gif_bytes():
    output = io.BytesIO()
    frames = [
        Image.new("RGB", (32, 24), color)
        for color in ("#ff0000", "#00ff00", "#0000ff")
    ]
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=[100, 120, 140], loop=0, disposal=2,
    )
    return output.getvalue()


class LevelingMediaUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_upload_dir = os.environ.get("PRIME_LEVEL_CARD_UPLOAD_DIR")
        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["PRIME_LEVEL_CARD_UPLOAD_DIR"] = self.temp_dir.name
        self.guild = SimpleNamespace(id=GUILD_ID)

        async def authorize(_request, **_kwargs):
            # Mirrors the tuple contract used by the dashboard's real auth helper.
            return {"id": "test-admin"}, self.guild

        async def level_admin_authorize(_request, **_kwargs):
            return {"id": "test-admin"}, self.guild

        async def read_json_body(request):
            return await request.json()

        def json_error(status, error, **fields):
            return web.json_response({"error": error, **fields}, status=status)

        routes = web.RouteTableDef()
        leveling_api.register_leveling_routes(
            routes,
            authorize=authorize,
            level_admin_authorize=level_admin_authorize,
            json_error=json_error,
            read_json_body=read_json_body,
            logger=logging.getLogger("TestLevelingMediaUpload"),
        )
        app = web.Application(client_max_size=21 * 1024 * 1024)
        app.add_routes(routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        with card_images._cache_lock:
            card_images._cache.clear()
        card_images._pending.clear()

    async def asyncTearDown(self):
        await self.client.close()
        with card_images._cache_lock:
            card_images._cache.clear()
        card_images._pending.clear()
        if self.previous_upload_dir is None:
            os.environ.pop("PRIME_LEVEL_CARD_UPLOAD_DIR", None)
        else:
            os.environ["PRIME_LEVEL_CARD_UPLOAD_DIR"] = self.previous_upload_dir
        self.temp_dir.cleanup()

    async def upload(self, data, filename, content_type):
        form = FormData()
        form.add_field("file", data, filename=filename, content_type=content_type)
        return await self.client.post(
            f"/api/guild/{GUILD_ID}/leveling/card-background",
            data=form,
        )

    async def test_png_upload_is_guild_scoped_and_survives_cold_cache_reload(self):
        response = await self.upload(png_bytes(), "level-background.png", "image/png")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        token = payload["backgroundAsset"]
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["format"], "png")
        self.assertFalse(payload["animated"])
        self.assertTrue(card_images.is_uploaded_background(token, GUILD_ID))
        self.assertFalse(card_images.is_uploaded_background(token, GUILD_ID + 1))

        path = card_images.uploaded_background_path(token, GUILD_ID)
        self.assertTrue(path.is_file())
        with card_images._cache_lock:
            card_images._cache.clear()
        card_images._pending.clear()
        loaded = await card_images.fetch_image(token)
        self.assertTrue(loaded and loaded.startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_saved_card_presets_retain_uploaded_background_tokens(self):
        token = f"upload:{GUILD_ID}:{'a' * 32}.png"
        preset = {
            **leveling_api.CARD_DESIGN_DEFAULTS,
            "id": "uploaded-background",
            "name": "Uploaded background",
            "layout": "vertical",
            "particles": "none",
            "color": "#26cbab",
            "bg": "",
            "backgroundAsset": token,
            "animated": True,
            "showStats": True,
        }
        cleaned = leveling_api._clean_card_design({
            **leveling_api.CARD_DESIGN_DEFAULTS,
            "presets": [preset],
        })
        saved = cleaned["presets"][0]
        self.assertEqual(saved["backgroundAsset"], token)
        self.assertEqual(saved["bg"], "")

    async def test_animated_gif_upload_preserves_motion(self):
        response = await self.upload(gif_bytes(), "level-background.gif", "image/gif")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["format"], "gif")
        self.assertTrue(payload["animated"])
        loaded = await card_images.fetch_image(payload["backgroundAsset"])
        with Image.open(io.BytesIO(loaded)) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.n_frames, 3)

    async def test_mp4_upload_is_transcoded_to_a_guild_owned_gif(self):
        def fake_ffmpeg(command, **_kwargs):
            Path(command[-1]).write_bytes(gif_bytes())
            return SimpleNamespace(returncode=0)

        # Valid MP4 container signature is enough to exercise the upload route;
        # FFmpeg is mocked so this test never needs a real video fixture or codec.
        mp4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32
        with patch("leveling_api.subprocess.run", side_effect=fake_ffmpeg):
            response = await self.upload(mp4, "level-background.mp4", "video/mp4")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertTrue(payload["transcodedVideo"])
        self.assertTrue(payload["animated"])
        self.assertEqual(payload["format"], "gif")
        self.assertTrue(
            card_images.is_uploaded_background(payload["backgroundAsset"], GUILD_ID)
        )


if __name__ == "__main__":
    unittest.main()
