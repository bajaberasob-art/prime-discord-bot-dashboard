import unittest
from types import SimpleNamespace

from command_registration import publish_command_groups


class FakeCommand:
    def __init__(self, payload):
        self.payload = payload
        self.name = payload["name"]

    def to_dict(self, tree=None):
        return dict(self.payload)


class FakeRemoteCommand:
    def __init__(self, payload):
        self.payload = payload
        self.name = payload["name"]

    def to_dict(self):
        return dict(self.payload)


class FakeTree:
    def __init__(self, commands, remote):
        self.commands = commands
        self.remote = remote
        self.fetch_guilds = []
        self.fetch_count = 0

    def get_command(self, name):
        return self.commands.get(name)

    async def fetch_commands(self, *, guild=None):
        self.fetch_count += 1
        self.fetch_guilds.append(guild)
        return self.remote


class FakeHTTP:
    def __init__(self):
        self.global_upserts = []
        self.guild_upserts = []

    async def upsert_global_command(self, app_id, payload):
        self.global_upserts.append((app_id, dict(payload)))

    async def upsert_guild_command(self, app_id, guild_id, payload):
        self.guild_upserts.append((app_id, guild_id, dict(payload)))


def payload(name, description, *, options=None):
    return {
        "name": name,
        "description": description,
        "type": 1,
        "options": options or [],
        "default_member_permissions": None,
        "dm_permission": True,
    }


class CommandRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def make_bot(self, local, remote):
        bot = SimpleNamespace(application_id=12345, http=FakeHTTP())
        bot.tree = FakeTree(
            {name: FakeCommand(data) for name, data in local.items()},
            [FakeRemoteCommand(data) for data in remote],
        )
        return bot

    async def test_only_changed_requested_groups_are_upserted(self):
        game = payload("game", "ألعاب PRIME", options=[{"name": "xo"}])
        tournament = payload("tournament", "جولات البطولات", options=[{"name": "bracket"}])
        rank = payload("rank", "رتب الأعضاء")
        bot = self.make_bot(
            {"game": game, "tournament": tournament, "rank": rank},
            [
                payload("game", "ألعاب PRIME", options=[{"name": "xo"}]),
                payload("rank", "رتب الأعضاء"),
            ],
        )

        self.assertTrue(await publish_command_groups(bot, ("game", "tournament")))

        self.assertEqual(
            [item[1]["name"] for item in bot.http.global_upserts],
            ["tournament"],
        )
        self.assertEqual(bot.http.guild_upserts, [])
        self.assertEqual(bot.tree.fetch_count, 1)

    async def test_guild_specific_registration_uses_the_selected_guild(self):
        game = payload("game", "ألعاب PRIME")
        bot = self.make_bot({"game": game}, [])
        guild = SimpleNamespace(id=9876)

        self.assertTrue(await publish_command_groups(bot, ("game",), guild=guild))

        self.assertEqual(
            [(app_id, guild_id, item["name"]) for app_id, guild_id, item in bot.http.guild_upserts],
            [(12345, 9876, "game")],
        )
        self.assertEqual(bot.http.global_upserts, [])
        self.assertEqual(bot.tree.fetch_guilds, [guild])

    async def test_standalone_visual_game_command_is_upserted_without_broad_sync(self):
        musical_chairs = payload("musical_chairs", "ابدأ الكراسي الموسيقية")
        bot = self.make_bot({"musical_chairs": musical_chairs}, [])

        self.assertTrue(await publish_command_groups(bot, ("musical_chairs",)))

        self.assertEqual(
            [item[1]["name"] for item in bot.http.global_upserts],
            ["musical_chairs"],
        )
        self.assertEqual(bot.http.guild_upserts, [])
        self.assertEqual(bot.tree.fetch_count, 1)

    async def test_missing_local_group_fails_safely_without_upserting(self):
        bot = self.make_bot({}, [])

        self.assertFalse(await publish_command_groups(bot, ("game",)))
        self.assertEqual(bot.http.global_upserts, [])
        self.assertEqual(bot.tree.fetch_count, 0)


if __name__ == "__main__":
    unittest.main()
