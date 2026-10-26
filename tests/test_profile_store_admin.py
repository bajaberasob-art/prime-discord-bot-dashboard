import io
import struct
import unittest
import zlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from profile_store_admin import (
    MAX_PROFILE_UPLOAD_BYTES,
    MAX_PROFILE_UPLOAD_PIXELS,
    is_profile_store_owner,
    profile_asset_root,
    validate_catalog_item_payload,
    validate_category_payload,
    validate_static_image,
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




    def test_category_payload_accepts_sections_and_subsections_and_rejects_bad_input(self):
        section = validate_category_payload({
            "name": "بانرات موسمية", "parent_id": None,
            "sort_order": 12, "is_active": True,
        })
        child = validate_category_payload({
            "name": "نيون", "parent_id": 7,
            "sort_order": 3, "is_active": False,
        })
        self.assertEqual(section["name"], "بانرات موسمية")
        self.assertIsNone(section["parent_id"])
        self.assertEqual(section["sort_order"], 12)
        self.assertEqual(child["parent_id"], 7)
        self.assertEqual(child["is_active"], 0)
        for payload, expected in (
            ({"name": "all"}, "invalid_category_name"),
            ({"name": "  "}, "required"),
            ({"name": "valid", "sort_order": -1}, "invalid_category_order"),
            ({"name": "valid", "is_active": 1}, "category_active_must_be_boolean"),
            ({"name": "valid", "parent_id": True}, "invalid_parent_category"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, expected):
                    validate_category_payload(payload)

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


if __name__ == "__main__":
    unittest.main()
