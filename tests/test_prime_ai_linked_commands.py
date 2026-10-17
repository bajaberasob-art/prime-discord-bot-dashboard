import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from prime_ai.errors import AccessDenied
import prime_ai_runtime
from prime_ai.linked_commands import (
    execute_linked_command,
    ensure_read_skill_command_policy,
)
from command_policy_service import CommandPolicyDenied


class AICommandPolicyBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_ai_actions_honor_matching_slash_command_policies(self):
        guild = SimpleNamespace(id=100000000000000001)
        actor = SimpleNamespace(id=100000000000000010)
        channel = SimpleNamespace(id=100000000000000020)
        bindings = {
            "create_channel": "create_channel",
            "rename_channel": "rename_channel",
            "delete_channel": "delete_channel",
            "set_member_nickname": "setnick",
            "assign_role": "give_role",
            "remove_role": "take_role",
        }

        for tool, command_name in bindings.items():
            with self.subTest(tool=tool):
                with patch(
                    "command_policy_service.ensure_command_policy",
                    new=AsyncMock(return_value=None),
                ) as ensure:
                    result = await execute_linked_command(
                        SimpleNamespace(),
                        guild,
                        actor,
                        channel,
                        {"tool": tool, "arguments": {}},
                        {},
                    )
                self.assertIsNone(result)
                ensure.assert_awaited_once_with(
                    guild.id, command_name, actor, channel.id,
                )

    async def test_disabled_slash_policy_blocks_matching_ai_action(self):
        guild = SimpleNamespace(id=100000000000000001)
        actor = SimpleNamespace(id=100000000000000010)
        channel = SimpleNamespace(id=100000000000000020)
        with patch(
            "command_policy_service.ensure_command_policy",
            new=AsyncMock(side_effect=CommandPolicyDenied("command_disabled")),
        ) as ensure:
            with self.assertRaises(AccessDenied) as raised:
                await execute_linked_command(
                    SimpleNamespace(),
                    guild,
                    actor,
                    channel,
                    {"tool": "create_channel", "arguments": {"name": "updates"}},
                    {},
                )
        self.assertEqual(str(raised.exception), "command_disabled")
        ensure.assert_awaited_once_with(
            guild.id, "create_channel", actor, channel.id,
        )

    async def test_channel_read_skill_honors_channelinfo_command_policy(self):
        guild = SimpleNamespace(id=100000000000000001)
        actor = SimpleNamespace(id=100000000000000010)
        channel = SimpleNamespace(id=100000000000000020)
        with patch(
            "command_policy_service.ensure_command_policy",
            new=AsyncMock(return_value=None),
        ) as ensure:
            await ensure_read_skill_command_policy(
                guild, actor, channel, "fetch_channel",
            )
        ensure.assert_awaited_once_with(
            guild.id, "channelinfo", actor, channel.id,
        )

    async def test_disabled_channelinfo_policy_blocks_ai_channel_read(self):
        guild = SimpleNamespace(id=100000000000000001)
        actor = SimpleNamespace(id=100000000000000010)
        channel = SimpleNamespace(id=300000000000000020)
        with patch(
            "command_policy_service.ensure_command_policy",
            new=AsyncMock(side_effect=CommandPolicyDenied("command_disabled")),
        ) as ensure:
            with self.assertRaisesRegex(AccessDenied, "command_disabled"):
                await ensure_read_skill_command_policy(
                    guild, actor, channel, "fetch_channel",
                )
        ensure.assert_awaited_once_with(
            guild.id, "channelinfo", actor, channel.id,
        )

    async def test_fetch_channel_route_stops_before_read_when_command_disabled(self):
        guild = SimpleNamespace(id=100000000000000001)
        actor = SimpleNamespace(id=100000000000000010, roles=[])
        channel = SimpleNamespace(id=300000000000000020)
        skill = {"key": "channels", "skill_id": "CHANNEL_INFO", "enabled": True}
        with (
            patch.object(
                prime_ai_runtime.control,
                "get_skills",
                new=AsyncMock(return_value=[skill]),
            ),
            patch.object(prime_ai_runtime, "skill_policy", new=AsyncMock()),
            patch.object(
                prime_ai_runtime,
                "_ensure_read_skill_command_policy",
                new=AsyncMock(side_effect=AccessDenied("command_disabled")),
            ) as policy_bridge,
            patch.object(
                prime_ai_runtime,
                "get_skill_data",
                new=AsyncMock(return_value={"should_not_read": True}),
            ) as read_skill,
        ):
            result = await prime_ai_runtime.route_skill_request(
                guild, actor, channel,
                {"skill_key": "channels", "tool": "fetch_channel", "intent": "CHANNEL_INFO"},
            )
        self.assertEqual(result["error"], "command_disabled")
        policy_bridge.assert_awaited_once_with(guild, actor, channel, "fetch_channel")
        read_skill.assert_not_awaited()

    def test_command_policy_denials_have_clear_user_facing_message(self):
        for error in (
            "command_disabled", "command_role_restricted", "command_channel_restricted",
        ):
            with self.subTest(error=error):
                message = prime_ai_runtime.skill_error_text({"error": error})
                self.assertIn("سياسة الأمر المرتبط", message)
                self.assertNotIn("تعذر جلب هذه المعلومات", message)


if __name__ == "__main__":
    unittest.main()
