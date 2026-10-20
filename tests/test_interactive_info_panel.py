import os
import tempfile
import unittest

from interactive_info_store import validate_config


class InteractiveInfoPanelValidationTests(unittest.TestCase):
    def valid_body(self):
        return {
            "enabled": True,
            "channel_id": "123456789012345678",
            "title": "دليل السيرفر",
            "description": "اختر زرًا",
            "color": "#6366F1",
            "banner_url": "https://example.com/banner.png",
            "auto_delete_seconds": 300,
            "buttons": [
                {"label": "القوانين", "emoji": "📜", "style": "primary",
                 "response": "اقرأ <#123456789012345678>"},
            ],
        }

    def test_config_normalizes_button_ids_and_style(self):
        config = validate_config(42, self.valid_body())
        self.assertEqual(config["guild_id"], "42")
        self.assertEqual(config["channel_id"], "123456789012345678")
        self.assertEqual(len(config["buttons"][0]["id"]), 12)
        self.assertEqual(config["buttons"][0]["style"], "primary")
        self.assertEqual(config["auto_delete_seconds"], 300)

    def test_rejects_more_than_14_minutes(self):
        body = self.valid_body()
        body["auto_delete_seconds"] = 900
        with self.assertRaisesRegex(ValueError, "840"):
            validate_config(42, body)

    def test_rejects_empty_or_too_many_buttons(self):
        body = self.valid_body()
        body["buttons"] = []
        with self.assertRaisesRegex(ValueError, "1 إلى 25"):
            validate_config(42, body)
        body["buttons"] = [{"label": "x", "response": ""}] * 26
        with self.assertRaisesRegex(ValueError, "1 إلى 25"):
            validate_config(42, body)

    def test_rejects_invalid_style_and_oversized_response(self):
        body = self.valid_body()
        body["buttons"][0]["style"] = "blurple"
        with self.assertRaisesRegex(ValueError, "نمط زر"):
            validate_config(42, body)
        body = self.valid_body()
        body["buttons"][0]["response"] = "x" * 2001
        with self.assertRaisesRegex(ValueError, "2000"):
            validate_config(42, body)

    def test_uploaded_banner_path_is_scoped_to_guild_and_upload_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            os.environ["PRIME_PANEL_UPLOAD_DIR"] = directory
            try:
                body = self.valid_body()
                body["banner_file"] = os.path.join(directory, "42-banner.png")
                config = validate_config(42, body)
                self.assertEqual(config["banner_file"], os.path.join(directory, "42-banner.png"))
                body["banner_file"] = os.path.join(directory, "43-banner.png")
                with self.assertRaisesRegex(ValueError, "غير صالح"):
                    validate_config(42, body)
                body["banner_file"] = os.path.join(directory, "..", "42-banner.png")
                with self.assertRaisesRegex(ValueError, "غير صالح"):
                    validate_config(42, body)
            finally:
                os.environ.pop("PRIME_PANEL_UPLOAD_DIR", None)

    def test_preserves_published_message_reference_on_draft_save(self):
        old = {"message_id": "987654321098765432", "last_published_at": "2026-10-21T00:00:00"}
        config = validate_config(42, self.valid_body(), old)
        self.assertEqual(config["message_id"], old["message_id"])
        self.assertEqual(config["last_published_at"], old["last_published_at"])


if __name__ == "__main__":
    unittest.main()
