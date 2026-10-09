import os
import unittest
from io import BytesIO
from types import SimpleNamespace

import database
from cogs import engagement as engagement_module
from PIL import Image


class FakeInvite:
    def __init__(self, code, uses, inviter):
        self.code = code
        self.uses = uses
        self.inviter = inviter


class FakeChannel:
    id = 300000000000000001

    def __init__(self):
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append((content, kwargs))
        return SimpleNamespace(id=800000000000000001)


class FakeGuild:
    id = 1550042204091715634
    name = "Test Guild"
    member_count = 42

    def __init__(self):
        self.channel = FakeChannel()
        self.system_channel = self.channel
        self._invite_reads = [
            [FakeInvite("abc", 4, SimpleNamespace(id=77, name="Inviter"))],
        ]

    async def invites(self):
        return self._invite_reads.pop(0)

    async def vanity_invite(self):
        return None

    def get_member(self, user_id):
        return SimpleNamespace(id=user_id, display_name="Inviter")

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None


class FakeBot:
    user = SimpleNamespace(id=999)

    def __init__(self, guild):
        self.guilds = [guild]

    def get_guild(self, guild_id):
        return self.guilds[0] if guild_id == self.guilds[0].id else None


class EngagementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        database.DB_NAME = "/tmp/test_engagement.db"
        if os.path.exists(database.DB_NAME):
            os.remove(database.DB_NAME)
        await database.init_db()
        database.invalidate_guild_settings()
        self.guild = FakeGuild()
        self.cog = engagement_module.Engagement(FakeBot(self.guild))
        self.stats = []
        self.config = {
            "welcome_channel_id": None,
            "leave_channel_id": None,
            "welcome_enabled": True,
            "leave_enabled": True,
            "welcome_message": "أهلاً {user} في {server} — {username} — {count} — {inviter}",
            "welcome_dm_message": "",
            "leave_message": "",
            "welcome_dm_enabled": False,
            "welcome_embed_enabled": False,
            "welcome_generated_image_enabled": False,
            "leave_embed_enabled": False,
            "welcome_dm_embed_enabled": False,
            "auto_role_id": None,
            "member_auto_role_id": None,
            "bot_auto_role_id": None,
            "verified_role_id": None,
            "unverified_role_id": None,
            "rules_channel_id": None,
        }

        async def config(_guild_id):
            return dict(self.config)

        self.cog.engagement_settings = config

        async def record(guild_id, inviter_id):
            self.stats.append((guild_id, inviter_id))
            return 1

        engagement_module.record_invite_use = record

    async def test_invite_delta_identifies_inviter_and_renders_welcome(self):
        self.cog.invite_cache[self.guild.id] = {
            "abc": {"uses": 3, "inviter_id": 77, "inviter_name": "Inviter"}
        }
        member = SimpleNamespace(
            id=88,
            bot=False,
            mention="<@88>",
            display_name="New Member",
            guild=self.guild,
            add_roles=self._noop,
            send=self._noop,
        )
        await self.cog.on_member_join(member)
        self.assertEqual(self.stats, [(self.guild.id, 77)])
        self.assertIn("<@88>", self.guild.channel.sent[0][0])
        self.assertIn("42nd", self.guild.channel.sent[0][0])
        self.assertIn("<@77>", self.guild.channel.sent[0][0])

    async def test_invite_stats_and_rules_agreement_are_persistent(self):
        self.assertEqual(await database.record_invite_use(1, 77), 1)
        self.assertEqual(await database.record_invite_use(1, 77), 2)
        agreed_at = await database.record_rules_agreement(1, 88, 123)
        self.assertTrue(agreed_at)
        async with database.connect() as db:
            async with db.execute(
                "SELECT verified_role_id FROM rules_agreements WHERE guild_id = 1 AND user_id = 88"
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], 123)

    async def test_test_welcome_helper_uses_safe_preview_mentions(self):
        result = await self.cog.send_test_welcome(
            self.guild.id,
            self.guild.channel.id,
            {"username": "Preview"},
        )
        self.assertTrue(result["ok"])
        content, kwargs = self.guild.channel.sent[-1]
        self.assertIn("Preview", content)
        self.assertIsNotNone(kwargs["allowed_mentions"])
        self.assertNotIn("embed", kwargs)

    async def test_welcome_embed_sends_one_embed_without_separate_text(self):
        self.config["welcome_embed_enabled"] = True
        member = SimpleNamespace(
            id=88,
            bot=False,
            mention="<@88>",
            display_name="New Member",
            guild=self.guild,
            add_roles=self._noop,
            send=self._noop,
        )

        await self.cog.on_member_join(member)

        self.assertEqual(len(self.guild.channel.sent), 1)
        content, kwargs = self.guild.channel.sent[0]
        self.assertIsNone(content)
        self.assertIn("embed", kwargs)
        self.assertIn("أهلاً", kwargs["embed"].description)

    async def test_untrusted_template_variables_are_removed(self):
        rendered = self.cog.render_template(
            "{inviter}|{invite_code}|{unknown}|{username}",
            member=SimpleNamespace(display_name="Member"),
            guild=self.guild,
        )
        self.assertEqual(rendered, "|||Member")
        attributed = self.cog.render_template(
            "{inviter}|{invite_code}",
            guild=self.guild,
            inviter="<@77>",
            invite_code="abc",
        )
        self.assertEqual(attributed, "<@77>|abc")

    async def test_dm_failure_does_not_stop_channel_welcome_and_is_logged(self):
        self.config["welcome_dm_enabled"] = True

        async def dm_failure(*args, **kwargs):
            raise RuntimeError("private send blocked")

        member = SimpleNamespace(
            id=88,
            bot=False,
            mention="<@88>",
            display_name="New Member",
            guild=self.guild,
            add_roles=self._noop,
            send=dm_failure,
        )
        await self.cog.on_member_join(member)
        logs = await database.get_onboarding_delivery_logs(self.guild.id)
        self.assertEqual(self.guild.channel.sent[0][0].split(" — ")[0], "أهلاً <@88> في Test Guild")
        self.assertEqual({entry["delivery_type"]: entry["status"] for entry in logs},
                         {"dm": "failed", "welcome": "sent"})
        self.assertEqual(next(row for row in logs if row["delivery_type"] == "dm")["reason"],
                         "delivery_error")
        self.assertNotIn("content", logs[0])

    async def test_leave_message_is_delivered_and_recorded(self):
        member = SimpleNamespace(
            id=89,
            bot=False,
            mention="<@89>",
            display_name="Leaving Member",
            guild=self.guild,
        )
        await self.cog.on_member_remove(member)
        logs = await database.get_onboarding_delivery_logs(self.guild.id)
        self.assertEqual(logs[0]["delivery_type"], "leave")
        self.assertEqual(logs[0]["status"], "sent")
        self.assertIn("غادر", self.guild.channel.sent[-1][0])

    async def test_leave_embed_sends_one_embed_without_separate_text(self):
        self.config["leave_embed_enabled"] = True
        member = SimpleNamespace(
            id=89,
            bot=False,
            mention="<@89>",
            display_name="Leaving Member",
            guild=self.guild,
        )

        await self.cog.on_member_remove(member)

        self.assertEqual(len(self.guild.channel.sent), 1)
        content, kwargs = self.guild.channel.sent[0]
        self.assertIsNone(content)
        self.assertIn("embed", kwargs)
        self.assertIn("غادر", kwargs["embed"].description)

    async def test_personal_welcome_image_uses_member_avatar_and_server_data(self):
        avatar = Image.new("RGB", (48, 48), color=(220, 120, 60))
        source = BytesIO()
        avatar.save(source, format="PNG")
        output = self.cog._render_welcome_image(
            source.getvalue(), "عضو جديد", self.guild.name, 42, "#7c3aed"
        )
        result = Image.open(output)
        self.assertEqual(result.size, (1200, 420))
        self.assertEqual(result.format, "PNG")

    async def test_new_onboarding_settings_survive_database_reinitialization(self):
        updated = await database.update_guild_settings(
            self.guild.id,
            expected_revision=0,
            welcome_dm_message="رسالة خاصة مخصصة",
            leave_embed_enabled=True,
            welcome_generated_image_enabled=True,
        )
        self.assertEqual(updated["settings"]["welcome_dm_message"], "رسالة خاصة مخصصة")
        await database.init_db()
        database.invalidate_guild_settings(self.guild.id)
        reloaded = await database.get_guild_settings(self.guild.id)
        self.assertEqual(reloaded["settings"]["welcome_dm_message"], "رسالة خاصة مخصصة")
        self.assertTrue(reloaded["settings"]["leave_embed_enabled"])
        self.assertTrue(reloaded["settings"]["welcome_generated_image_enabled"])

    async def _noop(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()