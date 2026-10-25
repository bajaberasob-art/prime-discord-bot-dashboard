import io
import os
import struct
import tempfile
import unittest
import unittest
import zlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiosqlite
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

import database
import profile_store_admin
import profile_store_db
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from profile_store_admin import (
    MAX_PROFILE_UPLOAD_BYTES,
    MAX_PROFILE_UPLOAD_PIXELS,
    is_profile_store_owner,
    profile_asset_root,
    validate_catalog_item_payload,
    validate_static_image,
    _ASSET_ITEM_TYPES,
)


def image_bytes(fmt="PNG", *, color="#E34A99", size=(64, 48), animated=False):
    frames = [
        Image.new("RGBA" if fmt in {"PNG", "WEBP"} else "RGB", size, color),
        Image.new("RGBA" if fmt in {"PNG", "WEBP"} else "RGB", size, "#1A8BDB"),
    ]
    out = io.BytesIO()
    if animated:
        frames[0].save(
            out,
            format=fmt,
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
    else:
        frames[0].save(out, format=fmt)
    return out.getvalue()


class StaticImageValidationTests(unittest.TestCase):
    def test_accepts_static_png_jpeg_and_webp_and_returns_sanitized_data(self):
        cases = (
            ("PNG", "image/png", "avatar.png"),
            ("JPEG", "image/jpeg", "background.jpeg"),
            ("WEBP", "image/webp", "badge.webp"),
        )
        for image_format, mime, filename in cases:
            with self.subTest(image_format=image_format):
                raw = image_bytes(image_format)
                result = validate_static_image(
                    raw, declared_mime=mime, filename=filename,
                )
                self.assertEqual(result["format"], image_format)
                self.assertEqual(result["mime"], mime)
                self.assertEqual((result["width"], result["height"]), (64, 48))
                self.assertLessEqual(result["size"], MAX_PROFILE_UPLOAD_BYTES)
                with Image.open(io.BytesIO(result["data"])) as normalized:
                    self.assertEqual(normalized.format, image_format)
                    self.assertEqual(normalized.size, (64, 48))
                    self.assertFalse(getattr(normalized, "is_animated", False))

    def test_rejects_animated_gif_apng_and_animated_webp(self):
        cases = [
            ("GIF", "image/gif", "animated.gif"),
            ("PNG", "image/png", "animated.png"),
        ]
        for fmt, mime, filename in cases:
            with self.subTest(format=fmt):
                raw = image_bytes(fmt, animated=True)
                with self.assertRaises(ValueError):
                    validate_static_image(raw, declared_mime=mime, filename=filename)

        try:
            animated_webp = image_bytes("WEBP", animated=True)
        except (OSError, ValueError) as error:
            self.skipTest(f"Pillow WebP animation encoder unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "animated_images_not_allowed"):
            validate_static_image(
                animated_webp, declared_mime="image/webp", filename="motion.webp",
            )

    def test_rejects_mime_extension_and_real_format_mismatches(self):
        png = image_bytes("PNG")
        with self.assertRaisesRegex(ValueError, "image_mime_mismatch"):
            validate_static_image(png, declared_mime="image/jpeg", filename="file.png")
        with self.assertRaisesRegex(ValueError, "image_extension_mismatch"):
            validate_static_image(png, declared_mime="image/png", filename="file.webp")
        with self.assertRaisesRegex(ValueError, "unsupported_image_extension"):
            validate_static_image(png, declared_mime="image/png", filename="../../bad.svg")

    def test_rejects_empty_oversized_and_pixel_bomb_inputs(self):
        with self.assertRaisesRegex(ValueError, "empty_image"):
            validate_static_image(b"", declared_mime="image/png", filename="empty.png")
        png = image_bytes("PNG")
        with self.assertRaisesRegex(ValueError, "image_too_large"):
            validate_static_image(
                png, declared_mime="image/png", filename="large.png", max_bytes=1,
            )
        # Increase dimensions in a small PNG header to avoid allocating a 64MB image.
        huge_buffer = bytearray(image_bytes("PNG", size=(1, 1)))
        struct.pack_into(">II", huge_buffer, 16, MAX_PROFILE_UPLOAD_PIXELS + 1, 1)
        struct.pack_into(">I", huge_buffer, 29, zlib.crc32(huge_buffer[12:29]) & 0xFFFFFFFF)
        huge = bytes(huge_buffer)
        with self.assertRaisesRegex(ValueError, "image_pixel_limit"):
            validate_static_image(
                huge, declared_mime="image/png", filename="huge.png",
            )


class ProfileCatalogPayloadTests(unittest.TestCase):
    def test_admin_upload_supports_templates_banners_stickers_and_flags(self):
        self.assertTrue({"card_template", "background", "badge", "flag"}.issubset(_ASSET_ITEM_TYPES))

    def test_custom_categories_are_preserved_for_storefront_filters(self):
        result = validate_catalog_item_payload({
            "slug": "anime-banner", "name": "بانر أنمي", "item_type": "background",
            "category": "أنمي", "subcategory": "أزرق", "price": 0, "is_active": True,
        })
        self.assertEqual(result["category"], "أنمي")
        self.assertEqual(result["subcategory"], "أزرق")
        import json
        metadata = json.loads(result["metadata_json"])
        self.assertEqual(metadata, {})

    def test_accepts_free_and_paid_items(self):
        free = validate_catalog_item_payload({
            "slug": "rose-default",
            "name": "وردي افتراضي",
            "item_type": "card_color",
            "price": 0,
            "currency": "moon",
            "style": {"accent": "#FF78B7"},
        })
        paid = validate_catalog_item_payload({
            "slug": "gold-banner",
            "name": "ذهبي فاخر",
            "item_type": "background",
            "price": 1500,
            "currency": "gem",
            "rarity": "legendary",
            "metadata": {"category": "anime"},
        })
        self.assertEqual(free["price"], 0)
        self.assertEqual(free["item_type"], "card_color")
        self.assertEqual(paid["price"], 1500)
        self.assertEqual(paid["currency"], "gem")
        self.assertEqual(paid["rarity"], "legendary")
        self.assertEqual(paid["category"], "all")
        self.assertEqual(paid["subcategory"], "")
        self.assertNotIn("asset_path", free)

    def test_rejects_bad_slug_type_rarity_negative_price_and_untrusted_style(self):
        base = {"slug": "valid-item", "name": "Valid", "item_type": "background"}
        cases = [
            ({"slug": "../escape"}, "invalid_slug"),
            ({"item_type": "javascript"}, "invalid_item_type"),
            ({"rarity": "mythic"}, "invalid_rarity"),
            ({"price": -1}, "invalid_price"),
            ({"price": True}, "invalid_price"),
            ({"currency": "USD"}, "invalid_currency"),
            ({"style": {"accent": "url(http://evil)" }}, "invalid_style_accent"),
            ({"is_active": 1}, "boolean_flags_required"),
        ]
        for overrides, expected in cases:
            payload = {**base, **overrides}
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, expected):
                    validate_catalog_item_payload(payload)




    def test_asset_root_rejects_public_dashboard_directory(self):
        public_root = Path(__file__).resolve().parents[1] / "dashboard"
        with patch.dict(os.environ, {"PROFILE_STORE_ASSET_DIR": str(public_root)}):
            with self.assertRaisesRegex(ValueError, "outside the public dashboard root"):
                profile_asset_root()


class ProfileStoreOwnerAccessTests(unittest.TestCase):
    def test_only_configured_global_admins_or_detected_bot_owners_pass(self):
        request = SimpleNamespace(app={"bot": SimpleNamespace(owner_id=None, owner_ids=set(), application=None)})
        with patch.dict(os.environ, {
            "PROFILE_STORE_ADMIN_IDS": "",
            "BOT_OWNER_IDS": "",
            "BOT_OWNER_ID": "",
        }):
            self.assertFalse(is_profile_store_owner(request, {"id": "123"}))
            with patch.dict(os.environ, {"PROFILE_STORE_ADMIN_IDS": "123, 456"}):
                self.assertTrue(is_profile_store_owner(request, {"id": "123"}))
                self.assertFalse(is_profile_store_owner(request, {"id": "789"}))

    def test_runtime_bot_owner_and_local_development_admin_are_allowed(self):
        request = SimpleNamespace(app={
            "bot": SimpleNamespace(
                owner_id=9001,
                owner_ids={9002},
                application=SimpleNamespace(owner=SimpleNamespace(id=9003)),
            )
        })
        with patch.dict(os.environ, {
            "PROFILE_STORE_ADMIN_IDS": "",
            "BOT_OWNER_IDS": "",
            "BOT_OWNER_ID": "",
        }):
            self.assertTrue(is_profile_store_owner(request, {"id": "9001"}))
            self.assertTrue(is_profile_store_owner(request, {"id": "9002"}))
            self.assertTrue(is_profile_store_owner(request, {"id": "9003"}))
            self.assertTrue(is_profile_store_owner(request, {"id": "0", "_local_dev": True}))


class ProfileStoreAdminUploadRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_name = database.DB_NAME
        self.original_registered = profile_store_admin._registered
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prime_store_admin_")
        database.DB_NAME = os.path.join(self.temp_dir.name, "store.db")
        await database.init_db()
        await profile_store_db.ensure_profile_store_schema()
        profile_store_admin._registered = False
        self.routes = web.RouteTableDef()

        async def authorize(_request, *, write=False, management_tier=None):
            return {"id": 9001}, SimpleNamespace(id=99)

        profile_store_admin.register_profile_store_admin_routes(self.routes, authorize)
        app = web.Application(client_max_size=10 * 1024 * 1024)
        app["bot"] = SimpleNamespace(owner_id=None, owner_ids=set(), application=None)
        app.add_routes(self.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        profile_store_admin._registered = self.original_registered
        database.DB_NAME = self.original_db_name
        self.temp_dir.cleanup()

    async def test_template_upload_and_protected_admin_asset_preview(self):
        with patch.dict(os.environ, {
            "PROFILE_STORE_ADMIN_IDS": "9001",
            "BOT_OWNER_IDS": "",
            "BOT_OWNER_ID": "",
            "PROFILE_STORE_ASSET_DIR": "",
        }):
            async with database.connect(aiosqlite.Row) as db:
                async with db.execute(
                    "SELECT item_id FROM profile_store_items WHERE slug = 'profile-neon-wide'"
                ) as cursor:
                    row = await cursor.fetchone()
            self.assertIsNotNone(row)
            item_id = int(row["item_id"])

            # Existing repository-owned template assets must preview too, not just uploads.
            original_preview = await self.client.get(
                f"/api/guild/99/profile-store/items/{item_id}/asset"
            )
            self.assertEqual(original_preview.status, 200, await original_preview.text())
            with Image.open(io.BytesIO(await original_preview.read())) as existing_image:
                self.assertEqual(existing_image.format, "PNG")

            raw = image_bytes("PNG", size=(96, 64))
            form = FormData()
            form.add_field("file", raw, filename="new-template.png", content_type="image/png")
            uploaded = await self.client.post(
                f"/api/guild/99/profile-store/items/{item_id}/asset",
                data=form,
            )
            self.assertEqual(uploaded.status, 201, await uploaded.text())
            info = await uploaded.json()
            self.assertEqual((info["width"], info["height"]), (96, 64))
            self.assertTrue(info["has_asset"])

            image = await self.client.get(
                f"/api/guild/99/profile-store/items/{item_id}/asset"
            )
            self.assertEqual(image.status, 200, await image.text())
            self.assertEqual(image.headers.get("Content-Type"), "image/png")
            with Image.open(io.BytesIO(await image.read())) as decoded:
                self.assertEqual(decoded.size, (96, 64))

    async def test_admin_ui_assets_are_served_by_the_existing_web_server(self):
        for path, expected in (
            ("/profile-store-admin", "إدارة متجر"),
            ("/profile-store-admin/app.css", "product-grid"),
            ("/profile-store-admin/app.js", "submitProduct"),
        ):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
            body = await response.text()
            self.assertIn(expected, body, path)
            self.assertEqual(response.headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
