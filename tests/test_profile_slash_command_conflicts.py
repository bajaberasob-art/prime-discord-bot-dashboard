import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def slash_command_names(relative_path: str) -> set[str]:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    names: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "command"
                and isinstance(func.value, ast.Name)
                and func.value.id == "app_commands"
            ):
                continue

            explicit_name = next(
                (
                    keyword.value.value
                    for keyword in decorator.keywords
                    if keyword.arg == "name"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ),
                None,
            )
            names.add(explicit_name or node.name)

    return names


class ProfileSlashCommandConflictTests(unittest.TestCase):
    def test_economy_profile_and_visual_profile_have_distinct_slash_names(self):
        economy_commands = slash_command_names("cogs/economy.py")
        visual_commands = slash_command_names("cogs/profile.py")

        self.assertIn("profile", economy_commands)
        self.assertIn("profile_card", visual_commands)
        self.assertTrue(
            economy_commands.isdisjoint(visual_commands),
            f"Duplicate slash commands: {economy_commands & visual_commands}",
        )

    def test_profile_publisher_targets_the_renamed_visual_command(self):
        source = (ROOT / "cogs/profile.py").read_text(encoding="utf-8")

        self.assertIn('bot.tree.get_command("profile_card")', source)
        self.assertIn('existing.get("profile_card")', source)
        self.assertIn('upsert_global_command(bot.application_id, payload)', source)
        self.assertIn('upsert_guild_command(bot.application_id, int(guild.id), payload)', source)


if __name__ == "__main__":
    unittest.main()
