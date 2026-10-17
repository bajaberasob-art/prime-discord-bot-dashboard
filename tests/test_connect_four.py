import os
import tempfile
import unittest

import database
from cogs.games import GameBoardView, _game_embed
from prime_games.engine import apply_action, create_session, ensure_game_schema
from prime_games.registry import registry


class ConnectFourTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime-connect-four.db")
        await database.init_db()
        await ensure_game_schema()

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def _session(self, *, players=(11, 22)):
        return await create_session(
            guild_id=800,
            channel_id=400,
            game_key="connect4",
            players=list(players),
        )

    async def test_registry_exposes_connect_four_with_shared_reward_policy(self):
        game = registry.get("connect4")
        self.assertEqual(game.min_players, 2)
        self.assertEqual(game.max_players, 2)
        self.assertEqual(game.reward_coins, 25)
        self.assertEqual(len(game.initial_state([11, 22])["board"]), 42)

    async def test_horizontal_win_and_reward_use_existing_wallet(self):
        session = await self._session()
        self.assertEqual(session["state"]["board"], [None] * 42)

        # Alternating drops produce four X checkers across the bottom row.
        moves = [(11, 0), (22, 0), (11, 1), (22, 1), (11, 2), (22, 2), (11, 3)]
        result = None
        for player_id, column in moves:
            result = await apply_action(session["id"], player_id, {"column": column})
            self.assertTrue(result["ok"], result)

        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], 11)
        self.assertEqual(result["reward"], 25)
        self.assertEqual(result["session"]["status"], "finished")
        self.assertEqual(result["session"]["state"]["board"][35:42], ["X", "X", "X", "X", None, None, None])

        repeated = await apply_action(session["id"], 11, {"column": 4})
        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["reason"], "game_finished")
        account = await database.get_or_create_user(11, 800)
        self.assertEqual(account["balance"], 125)

    async def test_vertical_win_is_detected(self):
        session = await self._session()
        moves = [(11, 0), (22, 1), (11, 0), (22, 1), (11, 0), (22, 1), (11, 0)]
        result = None
        for player_id, column in moves:
            result = await apply_action(session["id"], player_id, {"column": column})
            self.assertTrue(result["ok"], result)
        self.assertEqual(result["winner_id"], 11)
        self.assertTrue(result["finished"])

    async def test_full_column_and_invalid_column_are_rejected_without_mutation(self):
        session = await self._session()
        for player_id in (11, 22, 11, 22, 11, 22):
            result = await apply_action(session["id"], player_id, {"column": 0})
            self.assertTrue(result["ok"], result)

        before = result["session"]["state"]["board"]
        full = await apply_action(session["id"], 11, {"column": 0})
        self.assertFalse(full["ok"])
        self.assertEqual(full["reason"], "column_full")
        invalid = await apply_action(session["id"], 11, {"column": 7})
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["reason"], "invalid_column")
        unchanged = await apply_action(session["id"], 11, {"column": 0})
        self.assertFalse(unchanged["ok"])
        self.assertEqual(unchanged["reason"], "column_full")
        self.assertEqual(before, (await database.get_session(session["id"]))["state"]["board"])

    async def test_persistent_view_has_seven_column_controls_and_disables_full_column(self):
        session = await self._session()
        board = [None] * 42
        for row in range(6):
            board[row * 7] = "X"
        session = {**session, "state": {"board": board}}

        view = GameBoardView(session)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 7)
        self.assertEqual(
            [item.custom_id for item in view.children],
            [f"primegame:connect4:{session['id']}:{column}" for column in range(7)],
        )
        self.assertTrue(view.children[0].disabled)
        self.assertTrue(all(not item.disabled for item in view.children[1:]))

        embed = _game_embed(session)
        self.assertIn("Connect Four", embed.title)
        self.assertIn("أربعة متتالية", embed.footer.text)


if __name__ == "__main__":
    unittest.main()
