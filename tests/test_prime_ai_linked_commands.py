import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from prime_ai.errors import AccessDenied
from prime_ai.linked_commands import execute_linked_command
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


if __name__ == "__main__":
    unittest.main()
