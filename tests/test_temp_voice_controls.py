import unittest
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from PIL import Image

from cogs.temp_voice import TempVoice
from interaction_runtime import _callback_opens_modal
import temp_voice_store as store
from temp_voice_panel import MemberLimitModal, RoomNameModal, RoomPanel
from temp_voice_dashboard import validate_banner_payload


class Role:
    def __init__(self, role_id):
        self.id = role_id


class Overwrite:
    def __init__(self, view_channel=None, connect=None, manage_channels=None):
        self.view_channel = view_channel
        self.connect = connect
        self.manage_channels = manage_channels


class FakeChannel:
    def __init__(self, guild, overwrites=None):
        self.guild = guild
        self.overwrites = dict(overwrites or {})

    def overwrites_for(self, target):
        return self.overwrites.get(target, Overwrite())

    async def set_permissions(self, target, overwrite=None, **permissions):
        if overwrite is None:
            values = {key: value for key, value in permissions.items() if key != "reason"}
            if not values:
                self.overwrites.pop(target, None)
                return
            overwrite = self.overwrites.get(target, Overwrite())
            for key, value in values.items():
                setattr(overwrite, key, value)
            self.overwrites[target] = overwrite
        else:
            self.overwrites[target] = overwrite


class TempVoiceControlsTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self):
        cog = TempVoice(SimpleNamespace())
        cog.persist_preferences = AsyncMock()
        return cog

    async def run_action(self, cog, channel, room, cfg, action, value=None):
        actor = SimpleNamespace(id=44)
        with patch("cogs.temp_voice.store.update_room", new_callable=AsyncMock):
            return await cog.perform(actor, channel, room, cfg, action, value)

    async def test_emergency_unlock_restores_the_previous_default_role_overwrite(self):
        default_role = Role(1)
        guild = SimpleNamespace(
            id=100, default_role=default_role, get_channel=lambda _channel_id: None,
        )
        original = Overwrite(view_channel=None, connect=False)
        channel = FakeChannel(guild, {default_role: original})
        room = {
            "guild_id": guild.id, "channel_id": 200, "owner_id": 300,
            "state": {"privacy": "public"},
        }
        cfg = {"privacy": "public", "button_settings": {}}
        cog = self.make_cog()

        await self.run_action(cog, channel, room, cfg, "emergency")
        locked = channel.overwrites[default_role]
        self.assertIsNone(locked.view_channel)
        self.assertFalse(locked.connect)
        self.assertTrue(room["state"]["emergency"])

        await self.run_action(cog, channel, room, cfg, "emergency")
        restored = channel.overwrites[default_role]
        self.assertIsNone(restored.view_channel)
        self.assertFalse(restored.connect)
        self.assertNotIn("emergency_overwrite", room["state"])
        self.assertFalse(room["state"]["emergency"])

    async def test_regular_lock_controls_cannot_change_permissions_during_emergency(self):
        default_role = Role(1)
        guild = SimpleNamespace(
            id=100, default_role=default_role, get_channel=lambda _channel_id: None,
        )
        channel = FakeChannel(guild, {default_role: Overwrite(connect=False)})
        room = {
            "guild_id": guild.id, "channel_id": 200, "owner_id": 300,
            "state": {"privacy": "public", "emergency": True,
                      "emergency_overwrite": {"view_channel": None, "connect": True}},
        }
        cog = self.make_cog()

        with self.assertRaisesRegex(ValueError, "قفل الطوارئ"):
            await self.run_action(cog, channel, room, {"privacy": "public", "button_settings": {}}, "unlock")
        self.assertFalse(channel.overwrites[default_role].connect)

    async def test_hide_and_lock_controls_change_visibility_and_connection_independently(self):
        default_role = Role(1)
        guild = SimpleNamespace(id=100, default_role=default_role, get_channel=lambda _channel_id: None)
        channel = FakeChannel(guild, {default_role: Overwrite(view_channel=True, connect=True)})
        room = {
            "guild_id": guild.id, "channel_id": 200, "owner_id": 300,
            "state": {"privacy": "public"},
        }
        cfg = {"privacy": "public", "button_settings": {}}
        cog = self.make_cog()

        await self.run_action(cog, channel, room, cfg, "privacy")
        self.assertFalse(channel.overwrites[default_role].view_channel)
        self.assertTrue(channel.overwrites[default_role].connect)
        self.assertTrue(room["state"]["hidden"])

        await self.run_action(cog, channel, room, cfg, "lock")
        self.assertFalse(channel.overwrites[default_role].view_channel)
        self.assertFalse(channel.overwrites[default_role].connect)
        self.assertTrue(room["state"]["locked"])

        await self.run_action(cog, channel, room, cfg, "unlock")
        self.assertFalse(channel.overwrites[default_role].view_channel)
        self.assertTrue(channel.overwrites[default_role].connect)
        self.assertFalse(room["state"]["locked"])

    async def test_room_message_identifies_target_for_an_admin_not_in_voice(self):
        cog = self.make_cog()
        channel = SimpleNamespace(id=200)
        guild = SimpleNamespace(
            id=100, get_channel=lambda channel_id: channel if channel_id == 200 else None,
        )
        room = {"guild_id": 100, "channel_id": 200, "owner_id": 55, "state": {}}
        cog.rooms[channel.id] = room
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(voice=None),
            message=SimpleNamespace(channel=channel),
        )

        with patch("cogs.temp_voice.discord.VoiceChannel", SimpleNamespace):
            resolved_room, resolved_channel = cog.resolve_room(interaction)

        self.assertIs(resolved_room, room)
        self.assertIs(resolved_channel, channel)

    async def test_room_permission_reconciliation_uses_previous_admin_roles(self):
        old_role, new_role, unrelated_role = Role(11), Role(12), Role(13)
        default_role = Role(1)
        roles = {r.id: r for r in (old_role, new_role, unrelated_role)}
        guild = SimpleNamespace(
            id=100, default_role=default_role,
            get_role=lambda role_id: roles.get(role_id),
            get_member=lambda _member_id: None,
        )
        channel = FakeChannel(guild, {
            old_role: Overwrite(view_channel=True, connect=True),
            unrelated_role: Overwrite(view_channel=False, connect=None),
        })
        cog = self.make_cog()
        # The API installs the new config before reconciling each room.
        cog.configs[guild.id] = {"admin_role_ids": [str(new_role.id)]}

        await cog.reconcile_room_permissions(
            channel,
            {"guild_id": guild.id, "owner_id": 99, "state": {}},
            {"admin_role_ids": [str(new_role.id)], "owner_manage_channel": False},
            old_config={"admin_role_ids": [str(old_role.id)]},
        )

        self.assertNotIn(old_role, channel.overwrites)
        self.assertTrue(channel.overwrites[new_role].view_channel)
        self.assertTrue(channel.overwrites[new_role].connect)
        self.assertFalse(channel.overwrites[unrelated_role].view_channel)

    async def test_room_panel_uses_four_icon_only_columns_and_red_delete(self):
        view = RoomPanel(None, store.DEFAULTS)
        buttons = view.children

        self.assertIsNone(view.timeout)
        self.assertEqual(len(buttons), 20)
        self.assertEqual(len(set(store.BUTTON_EMOJIS.values())), len(store.BUTTON_EMOJIS))
        for index, button in enumerate(buttons):
            action = button.custom_id.rsplit(":", 1)[-1]
            self.assertIsNone(button.label)
            self.assertEqual(button.row, index // 4)
            self.assertEqual(str(button.emoji), store.BUTTON_EMOJIS[action])
            self.assertEqual(
                button.style,
                discord.ButtonStyle.danger if action == "delete" else (
                    discord.ButtonStyle.primary if action in {"claim", "invite"}
                    else discord.ButtonStyle.secondary
                ),
            )

    async def test_room_panel_uses_one_usable_custom_emoji_family(self):
        actions = ["rename", "limit", "privacy", "waiting"]
        custom = [
            discord.PartialEmoji(name=name, id=123456789012345678 + index)
            for index, name in enumerate((
                "prime_edit", "prime_members", "prime_eye", "prime_hourglass",
            ))
        ]
        guild = type("Guild", (), {"emojis": custom})()
        view = RoomPanel(None, {"buttons": actions}, guild=guild)

        self.assertEqual(
            [button.emoji.id for button in view.children],
            [emoji.id for emoji in custom],
        )

    async def test_unavailable_custom_emoji_falls_back_without_disabling_button(self):
        actions = ["rename", "limit", "privacy", "waiting"]
        custom = [
            discord.PartialEmoji(name=name, id=123456789012345678 + index)
            for index, name in enumerate((
                "prime_edit", "prime_members", "prime_eye",
            ))
        ]
        custom.append(type("UnavailableEmoji", (), {
            "name": "prime_hourglass", "available": False,
            "id": 123456789012345699, "animated": False,
        })())
        guild = type("Guild", (), {"emojis": custom})()
        view = RoomPanel(None, {"buttons": actions}, guild=guild)

        self.assertEqual(
            [button.emoji.id for button in view.children[:3]],
            [emoji.id for emoji in custom[:3]],
        )
        self.assertEqual(str(view.children[3].emoji), store.BUTTON_EMOJIS["waiting"])

    async def test_room_panel_marks_modal_actions_for_runtime(self):
        view = RoomPanel(None, {"buttons": ["rename", "limit", "color", "lock"]})
        callbacks = {
            button.custom_id.rsplit(":", 1)[-1]: button.callback
            for button in view.children
        }

        for action in ("rename", "limit", "color"):
            self.assertTrue(_callback_opens_modal(callbacks[action]), action)
        self.assertFalse(_callback_opens_modal(callbacks["lock"]))

    async def test_legacy_full_layout_reserves_a_slot_for_explicit_delete(self):
        config = {"buttons": list(store.BUTTONS)[:20]}

        migration = store.ensure_delete_button(config)

        self.assertEqual(migration["displaced"], "transfer")
        self.assertEqual(len(config["buttons"]), 20)
        self.assertIn("delete", config["buttons"])
        self.assertNotIn("transfer", config["buttons"])

        short_config = {"buttons": ["rename", "limit"]}
        self.assertEqual(store.ensure_delete_button(short_config), {"displaced": None})
        self.assertEqual(short_config["buttons"], ["rename", "limit", "delete"])

    async def test_panel_layout_version_is_server_managed(self):
        with self.assertRaisesRegex(ValueError, "حقول غير معروفة"):
            store.validate_patch(
                SimpleNamespace(), {"revision": 0, "panel_layout_version": 1}, store.DEFAULTS,
            )

    async def test_rename_and_limit_buttons_open_their_specific_modals(self):
        cog = self.make_cog()
        actor = SimpleNamespace(
            id=44, roles=[], guild_permissions=SimpleNamespace(administrator=False),
        )
        guild = SimpleNamespace(
            id=100, owner_id=99, get_member=lambda _user_id: actor,
        )
        actor.guild = guild
        channel = SimpleNamespace(id=200, name="Room", user_limit=8)
        room = {"guild_id": 100, "channel_id": 200, "owner_id": actor.id, "state": {}}
        cfg = {
            "buttons": ["rename", "limit"], "admin_role_ids": [],
            "ownership_claim": True, "owner_embed_color": True,
            "waiting_room": True, "meeting_mode": True, "embed_color": "#8b5cf6",
        }
        cog.config = AsyncMock(return_value=cfg)
        cog.resolve_room = lambda *_args: (room, channel)
        response = SimpleNamespace(send_modal=AsyncMock())
        interaction = SimpleNamespace(guild=guild, user=actor, response=response)

        await cog.dispatch(interaction, "rename")
        self.assertIsInstance(response.send_modal.await_args.args[0], RoomNameModal)
        response.send_modal.reset_mock()

        await cog.dispatch(interaction, "limit")
        self.assertIsInstance(response.send_modal.await_args.args[0], MemberLimitModal)

    async def test_rename_and_limit_apply_channel_edits_immediately(self):
        class EditableChannel:
            def __init__(self):
                self.guild = SimpleNamespace(id=100)
                self.id = 200
                self.name = "Old room"
                self.user_limit = 8
                self.edit = AsyncMock()

        channel = EditableChannel()
        room = {"guild_id": 100, "channel_id": 200, "owner_id": 44, "state": {}}
        cfg = {"button_settings": {}, "permanent_memory": False}
        cog = self.make_cog()

        await self.run_action(cog, channel, room, cfg, "rename", "New room")
        channel.edit.assert_awaited_once_with(name="New room")
        channel.edit.reset_mock()

        await self.run_action(cog, channel, room, cfg, "limit", "12")
        channel.edit.assert_awaited_once_with(user_limit=12)
        channel.edit.reset_mock()
        with self.assertRaisesRegex(ValueError, "0 و99"):
            await self.run_action(cog, channel, room, cfg, "limit", "100")
        channel.edit.assert_not_awaited()

    async def test_panel_uses_one_embed_and_attaches_uploaded_banner(self):
        cog = self.make_cog()
        guild = SimpleNamespace(id=100)
        config = dict(store.DEFAULTS)
        config["banner_image_id"] = "a" * 64
        config["guide_image_id"] = "b" * 64

        with patch(
            "temp_voice_dashboard.get_banner",
            new_callable=AsyncMock,
            return_value={"mime": "image/png", "payload": b"png-data"},
        ):
            embeds, files, streams = await cog.panel_message_assets(guild, config)
            try:
                self.assertEqual(len(embeds), 1)
                self.assertIsNone(embeds[0].description)
                self.assertEqual(embeds[0].image.url, "attachment://prime-voice-banner.png")
                self.assertEqual(len(files), 1)
                self.assertEqual(files[0].filename, "prime-voice-banner.png")
            finally:
                cog.close_panel_files(files, streams)

    async def test_admin_can_start_and_confirm_discord_room_delete(self):
        cog = self.make_cog()
        admin = SimpleNamespace(
            id=44, roles=[], guild_permissions=SimpleNamespace(administrator=True),
        )
        guild = SimpleNamespace(
            id=100, owner_id=99, get_member=lambda _user_id: admin,
        )
        admin.guild = guild
        channel = SimpleNamespace(id=200)
        room = {"guild_id": 100, "channel_id": 200, "owner_id": 55, "state": {}}
        cog.config = AsyncMock(return_value={
            "buttons": ["delete"], "admin_role_ids": [],
        })
        cog.resolve_room = lambda *_args: (room, channel)
        response = SimpleNamespace(
            is_done=lambda: True, defer=AsyncMock(), send_message=AsyncMock(),
        )
        followup = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(guild=guild, user=admin, response=response, followup=followup)
        cog.perform = AsyncMock(return_value="تم الحذف")

        await cog.dispatch(interaction, "delete")

        response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        confirmation = followup.send.await_args.kwargs["view"]
        self.assertEqual(len(confirmation.children), 1)
        self.assertEqual(confirmation.children[0].emoji.name, store.BUTTON_EMOJIS["delete"])

        response.defer.reset_mock()
        followup.send.reset_mock()
        await cog.dispatch(interaction, "delete", value="confirmed")
        response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        cog.perform.assert_awaited_once_with(admin, channel, room, {
            "buttons": ["delete"], "admin_role_ids": [],
        }, "delete", "confirmed")

    async def test_nonowners_cannot_use_claim_status_or_report(self):
        cog = self.make_cog()
        member = SimpleNamespace(
            id=44, roles=[], guild_permissions=SimpleNamespace(administrator=False),
        )
        guild = SimpleNamespace(
            id=100, owner_id=99, get_member=lambda _member_id: member,
        )
        member.guild = guild
        channel = SimpleNamespace(id=200)
        room = {"guild_id": 100, "channel_id": 200, "owner_id": 55, "state": {}}
        cfg = {"buttons": ["claim", "status", "report"], "admin_role_ids": []}
        cog.config = AsyncMock(return_value=cfg)
        cog.resolve_room = lambda *_args: (room, channel)

        for action in ("claim", "status", "report"):
            response = SimpleNamespace(
                is_done=lambda: True, defer=AsyncMock(), send_message=AsyncMock(),
            )
            followup = SimpleNamespace(send=AsyncMock())
            interaction = SimpleNamespace(
                guild=guild, user=member, response=response, followup=followup,
            )

            await cog.dispatch(interaction, action)

            response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
            self.assertIn("مالك الروم", followup.send.await_args.args[0])

    async def test_delete_room_service_accepts_a_live_server_admin(self):
        cog = self.make_cog()
        admin = SimpleNamespace(
            id=44, roles=[], guild_permissions=SimpleNamespace(administrator=True),
        )
        guild = SimpleNamespace(
            id=100, owner_id=99, get_member=lambda _member_id: admin,
            get_channel=lambda _channel_id: None,
        )
        admin.guild = guild
        room = {"guild_id": guild.id, "channel_id": 200, "owner_id": 55, "state": {}}
        cog.rooms[200] = room
        cog.config = AsyncMock(return_value={"creation_channel_id": None, "admin_role_ids": []})
        cog.tick = AsyncMock()

        with patch("cogs.temp_voice.store.delete_room", new_callable=AsyncMock) as delete_record:
            await cog.delete_room(guild, 200, actor_id=admin.id)

        delete_record.assert_awaited_once_with(guild.id, 200)
        self.assertNotIn(200, cog.rooms)

    def test_webp_upload_validation_accepts_a_mobile_image(self):
        image = Image.new("RGB", (8, 8), color="purple")
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP")

        self.assertEqual(
            validate_banner_payload(buffer.getvalue(), "image/webp"),
            "image/webp",
        )
        with self.assertRaisesRegex(ValueError, "لا يطابق"):
            validate_banner_payload(buffer.getvalue(), "image/png")

    async def test_room_deletion_requires_owner_or_dashboard_authorization(self):
        cog = self.make_cog()
        guild = SimpleNamespace(id=100)
        cog.rooms[200] = {"guild_id": 100, "channel_id": 200, "owner_id": 55, "state": {}}

        with self.assertRaisesRegex(ValueError, "تأكيد مالكه"):
            await cog.delete_room(guild, 200)
        with self.assertRaisesRegex(ValueError, "لمالك الروم"):
            await cog.delete_room(guild, 200, actor_id=44)

    async def test_empty_room_survives_member_departure(self):
        cog = self.make_cog()
        guild = SimpleNamespace(id=100)
        channel = SimpleNamespace(id=200, members=[])
        room = {
            "guild_id": guild.id, "channel_id": channel.id, "owner_id": 44,
            "state": {"pinned": False},
        }
        cog.ready = True
        cog.rooms[channel.id] = room
        cog.config = AsyncMock(return_value={"enabled": False})
        cog.tick = AsyncMock()
        cog.delete_room = AsyncMock()
        member = SimpleNamespace(guild=guild, bot=False)

        with patch("cogs.temp_voice.store.delete_room", new_callable=AsyncMock) as delete_record:
            await cog.on_voice_state_update(
                member, SimpleNamespace(channel=channel), SimpleNamespace(channel=None),
            )

        cog.delete_room.assert_not_awaited()
        delete_record.assert_not_awaited()
        self.assertIs(cog.rooms[channel.id], room)

    async def test_empty_restored_room_is_kept_on_restart(self):
        cog = self.make_cog()
        channel = SimpleNamespace(id=200, members=[])
        guild = SimpleNamespace(
            id=100, voice_channels=[], get_channel=lambda _channel_id: channel,
        )
        bot = SimpleNamespace(
            guilds=[guild], wait_until_ready=AsyncMock(), get_guild=lambda _guild_id: guild,
        )
        cog.bot = bot
        room = {
            "guild_id": guild.id, "channel_id": channel.id, "owner_id": 44,
            "state": {"pinned": False}, "last_tick": 0,
        }
        config = dict(store.DEFAULTS)

        with (
            patch("cogs.temp_voice.store.get_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 1}),
            patch("cogs.temp_voice.store.rooms", new_callable=AsyncMock, return_value=[room]),
            patch("cogs.temp_voice.store.update_room", new_callable=AsyncMock),
            patch("cogs.temp_voice.store.save_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 2}),
            patch("cogs.temp_voice.store.delete_room", new_callable=AsyncMock) as delete_record,
            patch.object(cog.heartbeat, "start") as start_heartbeat,
        ):
            await cog.restore()

        start_heartbeat.assert_called_once()
        delete_record.assert_not_awaited()
        self.assertIn(channel.id, cog.rooms)
        self.assertEqual(cog.member_counts[channel.id], 0)

    async def test_button_migration_refreshes_saved_panels_and_continues_on_api_failure(self):
        class SavedMessage:
            def __init__(self, author_id):
                self.author = SimpleNamespace(id=author_id)
                self.edit = AsyncMock()

        class SavedChannel:
            def __init__(self, channel_id, guild, message, *, members=None):
                self.id = channel_id
                self.guild = guild
                self.members = members or []
                self.fetch_message = AsyncMock(return_value=message)

        cog = self.make_cog()
        bot_user = SimpleNamespace(id=999)
        config = dict(store.DEFAULTS)
        config.update(
            buttons=list(store.BUTTONS)[:20],
            panel_channel_id="300",
            panel_message_id="301",
            banner_url="https://example.com/voice-banner.png",
        )
        central_message = SavedMessage(bot_user.id)
        room_message = SavedMessage(bot_user.id)
        guild = SimpleNamespace(id=100, voice_channels=[])
        central_channel = SavedChannel(300, guild, central_message)
        room_channel = SavedChannel(200, guild, room_message, members=[])
        channels = {central_channel.id: central_channel, room_channel.id: room_channel}
        guild.get_channel = lambda channel_id: channels.get(channel_id)
        bot = SimpleNamespace(
            user=bot_user, guilds=[guild], wait_until_ready=AsyncMock(),
            get_guild=lambda guild_id: guild if guild_id == guild.id else None,
        )
        cog.bot = bot
        room = {
            "guild_id": guild.id, "channel_id": room_channel.id, "owner_id": 44,
            "state": {"pinned": False, "panel_message_id": "201"}, "last_tick": 0,
        }

        # The central panel is unavailable, but its failure must not prevent
        # the room panel from refreshing or restoration from completing.
        central_channel.fetch_message.side_effect = RuntimeError("central panel unavailable")
        with (
            patch("cogs.temp_voice.store.get_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 1}),
            patch("cogs.temp_voice.store.save_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 2}) as save_config,
            patch("cogs.temp_voice.store.rooms", new_callable=AsyncMock, return_value=[room]),
            patch("cogs.temp_voice.store.update_room", new_callable=AsyncMock),
            patch.object(cog.heartbeat, "start") as start_heartbeat,
        ):
            await cog.restore()

        save_config.assert_awaited_once()
        central_channel.fetch_message.assert_awaited_once_with(301)
        room_channel.fetch_message.assert_awaited_once_with(201)
        room_view = room_message.edit.await_args.kwargs["view"]
        room_actions = [button.custom_id.rsplit(":", 1)[-1] for button in room_view.children]
        self.assertIn("delete", room_actions)
        self.assertNotIn("transfer", room_actions)
        self.assertEqual(len(room_message.edit.await_args.kwargs["embeds"]), 1)
        self.assertEqual(room_message.edit.await_args.kwargs["embeds"][0].image.url,
                         "https://example.com/voice-banner.png")
        self.assertTrue(cog.ready)
        start_heartbeat.assert_called_once()

    async def test_button_migration_refreshes_the_saved_central_panel_with_one_embed(self):
        author_id = 999
        message = SimpleNamespace(author=SimpleNamespace(id=author_id), edit=AsyncMock())
        channel = SimpleNamespace(id=300, fetch_message=AsyncMock(return_value=message))
        config = dict(store.DEFAULTS)
        config.update(
            # Delete was saved by the earlier startup; the version marker must
            # still trigger a one-time refresh of panels that have stale views.
            buttons=list(store.DEFAULTS["buttons"]),
            panel_channel_id=str(channel.id),
            panel_message_id="301",
            banner_url="https://example.com/voice-banner.png",
        )
        guild = SimpleNamespace(id=100, voice_channels=[], get_channel=lambda _id: channel)
        bot = SimpleNamespace(
            user=SimpleNamespace(id=author_id), guilds=[guild],
            wait_until_ready=AsyncMock(), get_guild=lambda _id: guild,
        )
        cog = self.make_cog()
        cog.bot = bot

        with (
            patch("cogs.temp_voice.store.get_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 1}),
            patch("cogs.temp_voice.store.save_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 2}) as save_config,
            patch("cogs.temp_voice.store.rooms", new_callable=AsyncMock, return_value=[]),
            patch.object(cog.heartbeat, "start"),
        ):
            await cog.restore()

        save_config.assert_awaited_once()
        self.assertEqual(config["panel_layout_version"], store.PANEL_LAYOUT_VERSION)
        channel.fetch_message.assert_awaited_once_with(301)
        message.edit.assert_awaited_once()
        kwargs = message.edit.await_args.kwargs
        actions = [button.custom_id.rsplit(":", 1)[-1] for button in kwargs["view"].children]
        self.assertIn("delete", actions)
        self.assertNotIn("transfer", actions)
        self.assertEqual(len(kwargs["embeds"]), 1)
        self.assertEqual(kwargs["embeds"][0].image.url, "https://example.com/voice-banner.png")

    async def test_completed_panel_layout_migration_does_not_repeat_on_next_restart(self):
        message = SimpleNamespace(
            author=SimpleNamespace(id=999), edit=AsyncMock(),
        )
        channel = SimpleNamespace(id=300, fetch_message=AsyncMock(return_value=message))
        config = dict(store.DEFAULTS)
        config.update(
            panel_layout_version=store.PANEL_LAYOUT_VERSION,
            panel_channel_id=str(channel.id),
            panel_message_id="301",
        )
        guild = SimpleNamespace(id=100, voice_channels=[], get_channel=lambda _id: channel)
        bot = SimpleNamespace(
            user=SimpleNamespace(id=999), guilds=[guild], wait_until_ready=AsyncMock(),
            get_guild=lambda _id: guild,
        )
        cog = self.make_cog()
        cog.bot = bot

        with (
            patch("cogs.temp_voice.store.get_config", new_callable=AsyncMock,
                  return_value={"config": config, "revision": 2}),
            patch("cogs.temp_voice.store.save_config", new_callable=AsyncMock) as save_config,
            patch("cogs.temp_voice.store.rooms", new_callable=AsyncMock, return_value=[]),
            patch.object(cog.heartbeat, "start"),
        ):
            await cog.restore()

        save_config.assert_not_awaited()
        channel.fetch_message.assert_not_awaited()
        message.edit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
