import unittest
from types import SimpleNamespace

from prime_ai.command_catalog import build_reviewed_command_catalog


class ReviewedCommandCatalogTests(unittest.TestCase):
    def _command(self, name, description="", parameters=None, qualified_name=None):
        return SimpleNamespace(
            name=name,
            qualified_name=qualified_name or name,
            description=description,
            parameters=parameters or [],
        )

    def test_catalog_uses_loaded_metadata_but_only_for_reviewed_bridges(self):
        names = (
            "channelinfo", "create_channel", "rename_channel", "delete_channel",
            "setnick", "give_role", "take_role", "timeout", "kick", "ban",
            "unban", "lock", "unlock",
        )
        reviewed = [
            self._command(
                name, f"وصف {name}",
                [SimpleNamespace(name="target", required=True, type="string")],
            )
            for name in names
        ]
        unreviewed = [
            self._command("massban", "حظر جماعي"),
            self._command("emergency", "وضع الطوارئ"),
            self._command("roleall", "منح رتبة للجميع"),
        ]
        bot = SimpleNamespace(
            tree=SimpleNamespace(
                walk_commands=lambda: iter(reviewed + unreviewed)
            )
        )

        catalog = build_reviewed_command_catalog(bot)
        by_name = {item["command_name"]: item for item in catalog}

        self.assertEqual([item["command_name"] for item in catalog], list(names))
        self.assertEqual(by_name["channelinfo"]["tool"], "fetch_channel")
        self.assertEqual(by_name["channelinfo"]["execution_path"], "read_skill")
        self.assertEqual(by_name["channelinfo"]["operation"], "read")
        self.assertEqual(by_name["channelinfo"]["description"], "وصف channelinfo")
        self.assertEqual(
            by_name["channelinfo"]["parameters"],
            [{"name": "target", "required": True, "type": "string"}],
        )
        self.assertEqual(by_name["create_channel"]["tool"], "create_channel")
        self.assertEqual(by_name["create_channel"]["policy_command"], "create_channel")
        self.assertEqual(by_name["rename_channel"]["tool"], "rename_channel")
        self.assertEqual(by_name["delete_channel"]["risk"], "CRITICAL")
        self.assertEqual(by_name["setnick"]["tool"], "set_member_nickname")
        self.assertEqual(by_name["give_role"]["tool"], "assign_role")
        self.assertEqual(by_name["take_role"]["tool"], "remove_role")
        self.assertEqual(by_name["timeout"]["execution_path"], "linked_handler")
        self.assertEqual(by_name["kick"]["tool"], "kick_member")
        self.assertEqual(by_name["ban"]["tool"], "ban_member")
        self.assertEqual(by_name["unban"]["tool"], "unban_member")
        self.assertEqual(by_name["lock"]["tool"], "set_channel_mode")
        self.assertEqual(by_name["unlock"]["tool"], "set_channel_mode")

        for name in ("massban", "emergency", "roleall"):
            self.assertNotIn(name, by_name)

    def test_missing_or_unavailable_command_tree_returns_empty_catalog(self):
        self.assertEqual(build_reviewed_command_catalog(SimpleNamespace()), [])
        self.assertEqual(
            build_reviewed_command_catalog(
                SimpleNamespace(tree=SimpleNamespace(walk_commands=lambda: iter(())))
            ),
            [],
        )

        def broken_walker():
            raise RuntimeError("command tree unavailable")

        self.assertEqual(
            build_reviewed_command_catalog(
                SimpleNamespace(tree=SimpleNamespace(walk_commands=broken_walker))
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
