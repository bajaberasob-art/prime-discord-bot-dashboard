import os
import tempfile
import unittest
from unittest.mock import patch

import database
from cogs.games import GameBoardView, _game_embed
from prime_games.engine import (
    apply_action,
    attach_message,
    create_session,
    ensure_game_schema,
    get_session,
)
from prime_games.registry import registry


class PremiumMiniGameRulesTests(unittest.TestCase):
    def setUp(self):
        self.players = [1101, 2202]

    def test_registry_exposes_the_four_new_games_and_rewards(self):
        self.assertEqual(
            {game.key for game in registry.all()},
            {"xo", "connect4", "rps", "quiz", "word", "emoji", "dice", "memory"},
        )
        self.assertEqual(registry.get("word").reward_coins, 15)
        self.assertEqual(registry.get("emoji").reward_coins, 15)
        self.assertEqual(registry.get("dice").reward_coins, 10)
        self.assertEqual(registry.get("memory").reward_coins, 20)

    def test_word_scramble_scores_answers_and_switches_players(self):
        game = registry.get("word")
        state = game.initial_state(self.players)
        puzzle = state["puzzles"][0]
        correct = game.reduce_action(
            state, self.players, self.players[0], {"answer": puzzle["correct"]}
        )
        self.assertEqual(correct.state["scores"]["1101"], 1)
        self.assertTrue(correct.state["last_answer"]["correct"])
        self.assertEqual(correct.state["last_answer"]["correct_option"], puzzle["options"][puzzle["correct"]])
        self.assertEqual(correct.next_player_id, 2202)

        wrong_index = (puzzle["correct"] + 1) % len(puzzle["options"])
        wrong = game.reduce_action(
            state, self.players, self.players[0], {"answer": wrong_index}
        )
        self.assertEqual(wrong.state["scores"]["1101"], 0)
        self.assertFalse(wrong.state["last_answer"]["correct"])

    def test_emoji_decode_rounds_are_arabic_multiple_choice_puzzles(self):
        game = registry.get("emoji")
        state = game.initial_state(self.players)
        self.assertEqual(len(state["puzzles"]), 6)
        puzzle = state["puzzles"][0]
        self.assertIn("clue", puzzle)
        self.assertEqual(len(puzzle["options"]), 4)
        outcome = game.reduce_action(
            state, self.players, self.players[0], {"answer": puzzle["correct"]}
        )
        self.assertEqual(outcome.state["scores"]["1101"], 1)
        self.assertEqual(outcome.next_player_id, 2202)

    def test_dice_duel_ends_after_six_turns_and_selects_highest_total(self):
        game = registry.get("dice")
        state = game.initial_state(self.players)
        outcome = None
        # Player one rolls 6, 5, 4; player two rolls 1, 2, 3.
        with patch("prime_games.registry.random.randint", side_effect=[6, 1, 5, 2, 4, 3]):
            current_player = 1101
            for _ in range(6):
                outcome = game.reduce_action(state, self.players, current_player, {"roll": True})
                state = outcome.state
                current_player = outcome.next_player_id

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.winner_id, 1101)
        self.assertIsNone(outcome.next_player_id)
        self.assertEqual(outcome.state["totals"], {"1101": 15, "2202": 6})
        self.assertEqual(outcome.state["turn_count"], 6)
        self.assertEqual(len(outcome.state["rolls"]["1101"]), 3)
        self.assertEqual(len(outcome.state["rolls"]["2202"]), 3)

    def test_memory_match_keeps_turn_and_mismatch_passes_it(self):
        game = registry.get("memory")
        state = game.initial_state(self.players)
        self.assertEqual(len(state["symbols"]), 12)
        self.assertEqual(sorted(state["symbols"].count(symbol) for symbol in set(state["symbols"])), [2] * 6)

        symbol_indexes = {}
        for index, symbol in enumerate(state["symbols"]):
            symbol_indexes.setdefault(symbol, []).append(index)
        first_pair = next(indexes for indexes in symbol_indexes.values() if len(indexes) == 2)
        first = game.reduce_action(state, self.players, 1101, {"index": first_pair[0]})
        self.assertEqual(first.next_player_id, 1101)
        self.assertEqual(first.state["open_cards"], [first_pair[0]])

        match = game.reduce_action(first.state, self.players, 1101, {"index": first_pair[1]})
        self.assertEqual(match.next_player_id, 1101)
        self.assertEqual(match.state["scores"]["1101"], 1)
        self.assertEqual(set(match.state["matched"]), set(first_pair))

        remaining = [index for index in range(12) if index not in set(match.state["matched"])]
        mismatch_first = remaining[0]
        mismatch_second = next(index for index in remaining[1:] if state["symbols"][index] != state["symbols"][mismatch_first])
        opened = game.reduce_action(match.state, self.players, 1101, {"index": mismatch_first})
        mismatch = game.reduce_action(opened.state, self.players, 1101, {"index": mismatch_second})
        self.assertEqual(mismatch.next_player_id, 2202)
        self.assertTrue(mismatch.state["pending_mismatch"])
        self.assertEqual(set(mismatch.state["open_cards"]), {mismatch_first, mismatch_second})

        new_card = next(index for index in remaining if index not in {mismatch_first, mismatch_second})
        opponent_pick = game.reduce_action(mismatch.state, self.players, 2202, {"index": new_card})
        self.assertEqual(opponent_pick.next_player_id, 2202)
        self.assertEqual(opponent_pick.state["open_cards"], [new_card])
        self.assertFalse(opponent_pick.state["pending_mismatch"])

    def test_memory_finishing_pair_declares_the_high_score_winner(self):
        game = registry.get("memory")
        state = {
            "symbols": ["🍉", "🍉", "🚀", "🚀", "🎧", "🎧", "🦊", "🦊", "🌙", "🌙", "⚽", "⚽"],
            "matched": list(range(10)),
            "open_cards": [10],
            "pending_mismatch": False,
            "scores": {"1101": 2, "2202": 1},
            "last_turn_outcome": None,
        }
        outcome = game.reduce_action(state, self.players, 1101, {"index": 11})
        self.assertEqual(outcome.winner_id, 1101)
        self.assertTrue(outcome.state["last_turn_outcome"] == "match")
        self.assertEqual(len(outcome.state["matched"]), 12)

    def test_persistent_controls_and_embeds_exist_for_all_new_games(self):
        sessions = {}
        for game_key in ("word", "emoji", "dice", "memory"):
            game = registry.get(game_key)
            sessions[game_key] = {
                "id": 880 + len(sessions),
                "game_key": game_key,
                "status": "active",
                "players": self.players,
                "current_player_id": self.players[0],
                "winner_id": None,
                "state": game.initial_state(self.players),
            }

        expected_counts = {"word": 4, "emoji": 4, "dice": 1, "memory": 12}
        for game_key, session in sessions.items():
            view = GameBoardView(session)
            self.assertIsNone(view.timeout)
            self.assertEqual(len(view.children), expected_counts[game_key])
            custom_ids = [item.custom_id for item in view.children]
            self.assertEqual(len(custom_ids), len(set(custom_ids)))
            self.assertTrue(all(custom_id.startswith(f"primegame:{game_key}:") for custom_id in custom_ids))
            embed = _game_embed(session)
            self.assertIn("PRIME", embed.title)
            self.assertTrue(embed.footer.text)


class PremiumMiniGameEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime-premium-games.db")
        await database.init_db()
        await ensure_game_schema()

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def test_dice_reward_uses_existing_wallet_and_transaction_ledger(self):
        session = await create_session(
            guild_id=950, channel_id=450, game_key="dice", players=[11, 22]
        )
        self.assertTrue(await attach_message(session["id"], 9901))

        result = None
        with patch("prime_games.registry.random.randint", side_effect=[6, 1, 5, 2, 4, 3]):
            for player_id in (11, 22, 11, 22, 11, 22):
                result = await apply_action(session["id"], player_id, {"roll": True})

        self.assertIsNotNone(result)
        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], 11)
        self.assertEqual(result["reward"], 10)
        self.assertEqual(result["session"]["status"], "finished")
        account = await database.get_or_create_user(11, 950)
        self.assertEqual(account["balance"], 110)
        async with database.connect() as db:
            async with db.execute(
                "SELECT amount, kind FROM economy_transactions WHERE guild_id = ? AND to_user_id = ?",
                (950, 11),
            ) as cursor:
                rows = await cursor.fetchall()
        self.assertEqual([(int(row[0]), row[1]) for row in rows], [(10, "game_reward")])

    async def test_memory_first_card_is_saved_without_ending_the_players_turn(self):
        session = await create_session(
            guild_id=951, channel_id=451, game_key="memory", players=[31, 32]
        )
        self.assertTrue(await attach_message(session["id"], 9902))
        first_index = 0
        result = await apply_action(session["id"], 31, {"index": first_index})
        self.assertTrue(result["ok"])
        self.assertEqual(result["session"]["current_player_id"], 31)
        self.assertEqual(result["session"]["state"]["open_cards"], [first_index])

        restored = await get_session(session["id"])
        self.assertEqual(restored["state"]["open_cards"], [first_index])
        self.assertEqual(restored["status"], "active")


if __name__ == "__main__":
    unittest.main()
