import os
import tempfile
import unittest

import database
from cogs.games import GameBoardView, _game_embed
from prime_games.engine import apply_action, attach_message, create_session, ensure_game_schema
from prime_games.registry import registry


class EmojiAndTreasureRulesTests(unittest.TestCase):
    def setUp(self):
        self.players = [3101, 4202]

    def test_emoji_decode_has_five_clues_and_scores_correct_answers(self):
        game = registry.get("emoji")
        state = game.initial_state(self.players)
        self.assertEqual(len(state["puzzles"]), 5)
        puzzle = state["puzzles"][0]
        self.assertEqual(len(puzzle["options"]), 4)
        self.assertIn("clue", puzzle)
        result = game.reduce_action(
            state, self.players, self.players[0], {"answer": puzzle["correct"]}
        )
        self.assertEqual(result.state["scores"]["3101"], 1)
        self.assertTrue(result.state["last_answer"]["correct"])
        self.assertEqual(result.next_player_id, 4202)

    def test_emoji_controls_are_persistent_and_embed_shows_the_clue(self):
        game = registry.get("emoji")
        session = {
            "id": 731,
            "game_key": "emoji",
            "status": "active",
            "players": self.players,
            "current_player_id": self.players[0],
            "winner_id": None,
            "state": game.initial_state(self.players),
        }
        view = GameBoardView(session)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 4)
        self.assertTrue(all(item.custom_id.startswith("primegame:emoji:731:") for item in view.children))
        embed = _game_embed(session)
        self.assertIn("فك شفرة الإيموجي", embed.title)
        self.assertIn("الرموز", embed.description or "")

    def test_treasure_board_hides_unopened_crates_and_rejects_repeated_picks(self):
        game = registry.get("treasure")
        state = game.initial_state(self.players)
        self.assertEqual(len(state["crates"]), 16)
        self.assertEqual(state["revealed"], {})
        first = game.reduce_action(state, self.players, self.players[0], {"index": 0})
        self.assertEqual(first.next_player_id, 4202)
        self.assertEqual(first.state["turn_count"], 1)
        self.assertIn("0", first.state["revealed"])
        with self.assertRaisesRegex(ValueError, "treasure_crate_open"):
            game.reduce_action(first.state, self.players, self.players[1], {"index": 0})

        session = {
            "id": 732,
            "game_key": "treasure",
            "status": "active",
            "players": self.players,
            "current_player_id": self.players[0],
            "winner_id": None,
            "state": state,
        }
        view = GameBoardView(session)
        self.assertEqual(len(view.children), 16)
        self.assertTrue(all("📦" in item.label for item in view.children))
        embed = _game_embed(session)
        self.assertIn("صائد الكنز", embed.title)
        self.assertNotIn("👑", embed.description or "")

    def test_treasure_hunt_finishes_after_eight_turns_by_total_points(self):
        game = registry.get("treasure")
        state = game.initial_state(self.players)
        points = [int(crate["points"]) for crate in state["crates"]]
        best = sorted(range(16), key=lambda index: points[index], reverse=True)[:4]
        worst = sorted(range(16), key=lambda index: points[index])[:4]
        outcome = None
        for round_index in range(4):
            outcome = game.reduce_action(
                state, self.players, self.players[0], {"index": best[round_index]}
            )
            state = outcome.state
            outcome = game.reduce_action(
                state, self.players, self.players[1], {"index": worst[round_index]}
            )
            state = outcome.state

        self.assertEqual(state["turn_count"], 8)
        self.assertEqual(len(state["revealed"]), 8)
        self.assertEqual(state["scores"]["3101"], 23)
        self.assertEqual(state["scores"]["4202"], -8)
        self.assertEqual(outcome.winner_id, 3101)
        self.assertIsNone(outcome.next_player_id)


class CreativeGameEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime-creative-games.db")
        await database.init_db()
        await ensure_game_schema()

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def test_treasure_winner_is_paid_through_the_existing_wallet_ledger(self):
        session = await create_session(
            guild_id=970, channel_id=470, game_key="treasure", players=[41, 42]
        )
        self.assertTrue(await attach_message(session["id"], 9970))
        crates = session["state"]["crates"]
        points = [int(crate["points"]) for crate in crates]
        best = sorted(range(16), key=lambda index: points[index], reverse=True)[:4]
        worst = sorted(range(16), key=lambda index: points[index])[:4]

        result = None
        for round_index in range(4):
            result = await apply_action(session["id"], 41, {"index": best[round_index]})
            self.assertTrue(result["ok"], result)
            result = await apply_action(session["id"], 42, {"index": worst[round_index]})
            self.assertTrue(result["ok"], result)

        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], 41)
        self.assertEqual(result["reward"], 20)
        account = await database.get_or_create_user(41, 970)
        self.assertEqual(account["balance"], 120)
        async with database.connect() as db:
            async with db.execute(
                "SELECT amount, kind FROM economy_transactions WHERE guild_id = ? AND to_user_id = ?",
                (970, 41),
            ) as cursor:
                rows = await cursor.fetchall()
        self.assertEqual([(int(row[0]), row[1]) for row in rows], [(20, "game_reward")])


if __name__ == "__main__":
    unittest.main()
