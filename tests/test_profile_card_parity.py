"""Regression checks that /profile (P) and store previews share data and rendering settings."""
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiosqlite

import cogs.profile as profile_cog
import database
import profile_store_db
import profile_store_public


class ProfileRenderParityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_name = database.DB_NAME
        self.original_public_registered = profile_store_public._registered
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prime_profile_parity_")
        database.DB_NAME = os.path.join(self.temp_dir.name, "parity.db")
        await database.init_db()
        await profile_store_db.ensure_profile_store_schema()
        profile_store_public._registered = False

        self.member = SimpleNamespace(
            id=1234, name="abood", display_name="Abood", bot=False,
            display_avatar=None,
        )
        self.guild = SimpleNamespace(
            id=99, name="PRIME TEST", members=[self.member],
            get_member=lambda user_id: self.member if int(user_id) == self.member.id else None,
        )
        self.bot = SimpleNamespace(
            get_guild=lambda guild_id: self.guild if int(guild_id) == self.guild.id else None,
        )
        self.profile = {
            "display_name": "Abood", "username": "abood", "level": 117,
            "xp": 1762, "required_xp": 74395, "balance": 325,
            "messages": 469, "rank": 1, "voice_seconds": 0,
        }
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT * FROM profile_store_items WHERE slug = 'profile-neon-wide'"
            ) as cursor:
                row = await cursor.fetchone()
        self.template = dict(row)
        self.template["style"] = profile_store_public._json_object(self.template.pop("style_json", "{}"))
        self.template["metadata"] = profile_store_public._json_object(self.template.pop("metadata_json", "{}"))
        self.configuration = {
            "template": self.template, "background": None, "flag": None, "badges": [],
            "color_style": {}, "accent_color": "#F58FB2", "divider_color": "#F58FB2",
            "xp_color": "#F58FB2", "font_color": "#FFFFFF",
        }

    async def asyncTearDown(self):
        profile_store_public._registered = self.original_public_registered
        database.DB_NAME = self.original_db_name
        self.temp_dir.cleanup()

    async def test_preview_and_p_use_identical_live_profile_payload_and_template(self):
        preview_bytes = io.BytesIO(b"preview-png")
        command_bytes = io.BytesIO(b"command-png")
        request = SimpleNamespace(app={"bot": self.bot})
        session = {"guild_id": self.guild.id, "user_id": self.member.id}

        with patch(
            "profile_store_public.build_profile_card_data",
            new=AsyncMock(return_value=(dict(self.profile), dict(self.configuration))),
        ) as preview_data, patch(
            "profile_card_generator.generate_profile_card",
            new=AsyncMock(return_value=preview_bytes),
        ) as preview_renderer:
            preview_item_id = int(self.template["item_id"])
            actual_preview = await profile_store_public._render_profile_preview(
                request, session, preview_item_id,
            )

        self.assertEqual(actual_preview, b"preview-png")
        preview_data.assert_awaited_once_with(self.guild, self.member)
        preview_profile = preview_renderer.await_args.args[1]
        preview_config = preview_renderer.await_args.args[2]
        self.assertEqual(preview_profile, self.profile)
        self.assertEqual(preview_config["template"]["item_id"], preview_item_id)
        self.assertEqual(preview_config["preview_item_id"], preview_item_id)

        command = object.__new__(profile_cog.ProfileCommands)
        with patch(
            "cogs.profile.build_profile_card_data",
            new=AsyncMock(return_value=(dict(self.profile), dict(self.configuration))),
        ) as command_data, patch(
            "cogs.profile.generate_profile_card",
            new=AsyncMock(return_value=command_bytes),
        ) as command_renderer:
            actual_command = await command._build_card(self.guild, self.member)

        self.assertIs(actual_command, command_bytes)
        command_data.assert_awaited_once_with(self.guild, self.member)
        command_profile = command_renderer.await_args.args[1]
        command_config = command_renderer.await_args.args[2]
        self.assertEqual(preview_profile, command_profile)
        self.assertEqual(
            {key: value for key, value in preview_config.items() if key != "preview_item_id"},
            command_config,
            "Preview and /profile must render with the same effective template/configuration.",
        )
        self.assertEqual(
            {key: preview_profile[key] for key in ("level", "xp", "required_xp", "balance", "messages", "rank", "voice_seconds")},
            {key: command_profile[key] for key in ("level", "xp", "required_xp", "balance", "messages", "rank", "voice_seconds")},
        )


if __name__ == "__main__":
    unittest.main()
