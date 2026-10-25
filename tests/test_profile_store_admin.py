import io
import unittest
import warnings

from PIL import Image

from profile_store_admin import (
    MAX_PROFILE_UPLOAD_BYTES,
    MAX_PROFILE_UPLOAD_PIXELS,
    validate_catalog_item_payload,
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
        huge = image_bytes("PNG", size=(MAX_PROFILE_UPLOAD_PIXELS + 1, 1))
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


if __name__ == "__main__":
    unittest.main()
