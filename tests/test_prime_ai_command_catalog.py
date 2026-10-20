import unittest
from types import SimpleNamespace

import prime_ai_control
from prime_ai.command_catalog import build_reviewed_command_catalog


class ReviewedCommandCatalogTests(unittest.TestCase):
    def _command(self, name, description="", parameters=None, qualified_name=None):
        return SimpleNamespace(
            name=name,
            qualified_name=qualified_name or name,
            description=description,
            parameters=parameters or [],
        )

    def test_catalog_exposes_only_explicitly_reviewed_write_bridges(self):
        names = [
            "create_channel", "rename_channel", "delete_channel", "setnick",
            "give_role", "take_role", "timeout", "kick", "ban", "unban",
            "lock", "unlock",
        ]
        commands = [
            self._command(
                name,
                f"وصف أمر {name}",
                [SimpleNamespace(name="target", required=False, type="string")],
            )
            for name in names
        ]
        # Read-only skills use their own policy bridge; arbitrary or bulk
        # moderation commands must not leak into the write-action catalog.
        commands.extend([
            self._command("channelinfo", "عرض معلومات قناة"),
            self._command("massban", "حظر جماعي"),
            self._command("nuke", "تطهير قناة"),
        ])
        bot = SimpleNamespace(
            tree=SimpleNamespace(walk_commands=lambda: iter(commands))
        )

        catalog = build_reviewed_command_catalog(bot)

        self.assertEqual([item["command_name"] for item in catalog], names)
        self.assertEqual(len(catalog), 12)
        by_name = {item["command_name"]: item for item in catalog}
        expected_tools = {
            "create_channel": "create_channel",
            "rename_channel": "rename_channel",
            "delete_channel": "delete_channel",
            "setnick": "set_member_nickname",
            "give_role": "assign_role",
            "take_role": "remove_role",
            "timeout": "timeout_member",
            "kick": "kick_member",
            "ban": "ban_member",
            "unban": "unban_member",
            "lock": "set_channel_mode",
            "unlock": "set_channel_mode",
        }
        self.assertEqual(
            {name: item["tool"] for name, item in by_name.items()},
            expected_tools,
        )
        for item in catalog:
            metadata = prime_ai_control.ACTION_REGISTRY[item["tool"]]
            self.assertEqual(item["risk"], metadata["risk"])
            self.assertEqual(
                item["required_permission"], metadata["discord_permission"]
            )
            self.assertEqual(item["operation"], "write")
            self.assertEqual(item["description"], f"وصف أمر {item['command_name']}")
            self.assertEqual(
                item["parameters"],
                [{"name": "target", "required": False, "type": "string"}],
            )
        self.assertEqual(by_name["lock"]["argument_hints"], {"mode": "read_only"})
        self.assertEqual(by_name["unlock"]["argument_hints"], {"mode": "open"})
        self.assertEqual(by_name["kick"]["execution_path"], "linked_handler")
        self.assertEqual(by_name["create_channel"]["execution_path"], "native_action")

        exposed = {item["command_name"] for item in catalog}
        self.assertNotIn("channelinfo", exposed)
        self.assertNotIn("massban", exposed)
        self.assertNotIn("nuke", exposed)

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
