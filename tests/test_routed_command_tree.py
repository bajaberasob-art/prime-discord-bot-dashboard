import unittest

import discord
from discord import app_commands

from routed_command_tree import RoutedCommandTree


def make_command(name: str):
    @app_commands.command(name=name, description="Test routed command")
    async def command(interaction: discord.Interaction) -> None:
        return None

    return command


class RoutedCommandTreeRollbackTests(unittest.TestCase):
    def setUp(self):
        self.client = discord.Client(intents=discord.Intents.none())
        self.client._active_cog_name = "ChatJailCog"
        self.tree = RoutedCommandTree(self.client)
        self.tree._slash_groups = {}

    def test_remove_command_removes_nested_child_and_empty_group(self):
        command = make_command("clear_user")
        self.tree.add_command(command)

        self.assertIn("clear_user", [item.name for item in self.tree._slash_groups["chat"].commands])
        removed = self.tree.remove_command("clear_user")

        self.assertIs(removed, command)
        self.assertNotIn("chat", self.tree._slash_groups)
        self.assertIsNone(self.tree.get_command("chat"))

    def test_removing_one_child_preserves_other_group_commands(self):
        first = make_command("clear_user")
        second = make_command("cleanup")
        self.tree.add_command(first)
        self.tree.add_command(second)

        self.tree.remove_command("clear_user")

        group = self.tree._slash_groups["chat"]
        self.assertEqual([item.name for item in group.commands], ["cleanup"])
        self.assertIs(self.tree.get_command("chat"), group)

    def test_failed_load_orphan_is_purged_before_retry(self):
        stale_command = make_command("clear_user")
        self.tree.add_command(stale_command)

        self.tree.remove_cog_grouped_commands("ChatJailCog")
        self.assertNotIn("chat", self.tree._slash_groups)

        replacement = make_command("clear_user")
        self.tree.add_command(replacement)

        group = self.tree._slash_groups["chat"]
        self.assertEqual([item.name for item in group.commands], ["clear_user"])
        self.assertIs(self.tree.get_command("chat"), group)


if __name__ == "__main__":
    unittest.main()
