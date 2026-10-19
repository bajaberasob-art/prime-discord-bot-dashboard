import os
import tempfile
import unittest
from unittest.mock import patch

import database
from cogs.games import GameBoardView, _game_embed
from prime_games.engine import apply_action, create_session, ensure_game_schema, get_session
from prime_games.registry import registry


class PrimeArcadeExpansionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime-arcade.db")
        await database.init_db()
        await ensure_game_schema()
        self.players = [1101, 2202]

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def test_registry_lists_three_new_games_with_bounded_rewards(self):
        self.assertEqual(
            {game.key for game in registry.all()},
            {"xo", "connect4", "rps", "quiz", "memory", "dice", "word"},
        )
        self.assertEqual(registry.get("memory").reward_coins, 20)
        self.assertEqual(registry.get("dice").reward_coins, 15)
        self.assertEqual(registry.get("word").reward_coins, 15)

    async def test_memory_requires_two_different_cards_from_same_player(self):
        session = await create_session(
            guild_id=910, channel_id=501, game_key="memory", players=self.players
        )
        cards = session["state"]["cards"]
        first_index = 0
        matching_index = cards.index(cards[first_index], first_index + 1)

        first = await apply_action(session["id"], self.players[0], {"index": first_index})
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["session"]["current_player_id"], self.players[0])
        wrong_turn = await apply_action(session["id"], self.players[1], {"index": matching_index})
        self.assertFalse(wrong_turn["ok"])
        self.assertEqual(wrong_turn["reason"], "not_your_turn")

        second = await apply_action(
            session["id"], self.players[0], {"index": matching_index}
        )
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["session"]["state"]["matched"], sorted([first_index, matching_index]))
        self.assertEqual(second["session"]["state"]["scores"][str(self.players[0])], 1)
        self.assertEqual(second["session"]["current_player_id"], self.players[1])

    async def test_memory_has_sixteen_persistent_controls_and_renders_hidden_cards(self):
        session = await create_session(
            guild_id=911, channel_id=502, game_key="memory", players=self.players
        )
        view = GameBoardView(session)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 16)
        self.assertTrue(all(item.custom_id for item in view.children))
        embed = _game_embed(session)
        self.assertIn("تحدّي الذاكرة", embed.title)
        self.assertIn("❔", embed.description or "")

    async def test_dice_duel_finishes_five_rounds_and_pays_existing_wallet(self):
        session = await create_session(
            guild_id=912, channel_id=503, game_key="dice", players=self.players
        )
        with patch("prime_games.registry.random.randint", side_effect=[6, 1] * 5):
            result = None
            for _ in range(10):
                current = await get_session(session["id"])
                result = await apply_action(
                    session["id"], current["current_player_id"], {"roll": "roll"}
                )
                self.assertTrue(result["ok"], result)
        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], self.players[0])
        self.assertEqual(result["reward"], 15)
        self.assertEqual(result["session"]["status"], "finished")
        account = await database.get_or_create_user(self.players[0], 912)
        self.assertEqual(account["balance"], 115)

    async def test_word_scramble_tracks_correct_answers_and_shows_four_options(self):
        session = await create_session(
            guild_id=913, channel_id=504, game_key="word", players=self.players
        )
        self.assertEqual(len(session["state"]["puzzles"]), 5)
        self.assertTrue(all(len(puzzle["options"]) == 4 for puzzle in session["state"]["puzzles"]))
        first_puzzle = session["state"]["puzzles"][0]
        result = await apply_action(
            session["id"], self.players[0], {"answer": first_puzzle["correct"]}
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["session"]["state"]["scores"][str(self.players[0])], 1)
        self.assertEqual(result["session"]["current_player_id"], self.players[1])
        self.assertEqual(len(GameBoardView(session).children), 4)
        embed = _game_embed(result["session"])
        self.assertIn("رتّب الحروف", embed.description or "")

    async def test_word_invalid_answer_does_not_advance_round(self):
        session = await create_session(
            guild_id=914, channel_id=505, game_key="word", players=self.players
        )
        result = await apply_action(session["id"], self.players[0], {"answer": 99})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "invalid_answer")
        current = await get_session(session["id"])
        self.assertEqual(current["state"]["round_index"], 0)


if __name__ == "__main__":
    unittest.main()
