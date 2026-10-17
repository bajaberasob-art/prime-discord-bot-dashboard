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
        channel_info = self._command(
            "channelinfo",
            "عرض معلومات قناة",
            [SimpleNamespace(name="channel", required=False, type="channel")],
        )
        create_channel = self._command(
            "create_channel",
            "إنشاء قناة نصية",
            [
                SimpleNamespace(name="name", required=True, type="string"),
                SimpleNamespace(name="category", required=False, type="channel"),
            ],
        )
        unreviewed = self._command(
            "massban",
            "حظر جماعي",
            [SimpleNamespace(name="members", required=True, type="string")],
        )
        bot = SimpleNamespace(
            tree=SimpleNamespace(
                walk_commands=lambda: iter([channel_info, create_channel, unreviewed])
            )
        )

        catalog = build_reviewed_command_catalog(bot)

        self.assertEqual(
            [item["command_name"] for item in catalog],
            ["channelinfo", "create_channel"],
        )
        self.assertEqual(catalog[0]["tool"], "fetch_channel")
        self.assertEqual(catalog[0]["execution_path"], "read_skill")
        self.assertEqual(catalog[0]["description"], "عرض معلومات قناة")
        self.assertEqual(
            catalog[0]["parameters"],
            [{"name": "channel", "required": False, "type": "channel"}],
        )
        self.assertEqual(catalog[1]["tool"], "create_channel")
        self.assertEqual(catalog[1]["policy_command"], "create_channel")
        self.assertEqual(
            [item["name"] for item in catalog[1]["parameters"]],
            ["name", "category"],
        )
        self.assertNotIn("massban", [item["command_name"] for item in catalog])

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
