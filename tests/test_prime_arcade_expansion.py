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
    expire_stale_sessions,
    get_session,
    list_active_sessions,
)
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
        # A successful match keeps the same player's turn.
        self.assertEqual(second["session"]["current_player_id"], self.players[0])

    async def test_memory_mismatch_switches_turn_without_awarding_a_pair(self):
        session = await create_session(
            guild_id=919, channel_id=510, game_key="memory", players=self.players
        )
        cards = session["state"]["cards"]
        first_index = 0
        other_index = next(index for index, symbol in enumerate(cards) if symbol != cards[first_index])

        first = await apply_action(session["id"], self.players[0], {"index": first_index})
        self.assertTrue(first["ok"], first)
        mismatch = await apply_action(session["id"], self.players[0], {"index": other_index})
        self.assertTrue(mismatch["ok"], mismatch)
        self.assertEqual(mismatch["session"]["current_player_id"], self.players[1])
        self.assertEqual(mismatch["session"]["state"]["revealed"], [first_index, other_index])
        self.assertFalse(mismatch["session"]["state"]["last_match"])
        self.assertEqual(mismatch["session"]["state"]["scores"][str(self.players[0])], 0)

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
        self.assertEqual(len(session["state"]["puzzles"]), 6)
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

    async def test_connect_four_uses_its_configured_reward_in_existing_wallet(self):
        session = await create_session(
            guild_id=915, channel_id=506, game_key="connect4", players=self.players
        )
        moves = [
            (self.players[0], 0), (self.players[1], 0),
            (self.players[0], 1), (self.players[1], 1),
            (self.players[0], 2), (self.players[1], 2),
            (self.players[0], 3),
        ]
        result = None
        for actor_id, column in moves:
            result = await apply_action(session["id"], actor_id, {"column": column})
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], self.players[0])
        self.assertEqual(result["reward"], 20)
        account = await database.get_or_create_user(self.players[0], 915)
        self.assertEqual(account["balance"], 120)

    async def test_rps_keeps_first_choice_hidden_and_pays_configured_reward(self):
        session = await create_session(
            guild_id=916, channel_id=507, game_key="rps", players=self.players
        )
        first = await apply_action(session["id"], self.players[0], {"choice": "rock"})
        self.assertTrue(first["ok"], first)
        hidden_embed = _game_embed(first["session"])
        self.assertNotIn("🪨 حجر", hidden_embed.description or "")
        second = await apply_action(session["id"], self.players[1], {"choice": "scissors"})
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["finished"])
        self.assertEqual(second["winner_id"], self.players[0])
        self.assertEqual(second["reward"], 10)
        account = await database.get_or_create_user(self.players[0], 916)
        self.assertEqual(account["balance"], 110)

    async def test_memory_completes_all_pairs_and_pays_winner_once(self):
        session = await create_session(
            guild_id=917, channel_id=508, game_key="memory", players=self.players
        )
        groups = {}
        for index, symbol in enumerate(session["state"]["cards"]):
            groups.setdefault(symbol, []).append(index)
        result = None
        for pair in groups.values():
            current = await get_session(session["id"])
            actor_id = current["current_player_id"]
            first = await apply_action(session["id"], actor_id, {"index": pair[0]})
            self.assertTrue(first["ok"], first)
            result = await apply_action(session["id"], actor_id, {"index": pair[1]})
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["finished"])
        self.assertFalse(result["is_draw"])
        self.assertEqual(result["winner_id"], self.players[0])
        self.assertEqual(len(result["session"]["state"]["matched"]), 16)
        self.assertEqual(result["reward"], 20)
        account = await database.get_or_create_user(self.players[0], 917)
        self.assertEqual(account["balance"], 120)

    async def test_quiz_winner_receives_configured_reward(self):
        session = await create_session(
            guild_id=920, channel_id=511, game_key="quiz", players=self.players
        )
        result = None
        for _ in range(6):
            current = await get_session(session["id"])
            question = current["state"]["questions"][current["state"]["question_index"]]
            actor_id = current["current_player_id"]
            answer = int(question["correct"])
            if actor_id == self.players[1]:
                answer = (answer + 1) % len(question["options"])
            result = await apply_action(session["id"], actor_id, {"answer": answer})
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], self.players[0])
        self.assertEqual(result["reward"], 15)
        account = await database.get_or_create_user(self.players[0], 920)
        self.assertEqual(account["balance"], 115)

    async def test_word_winner_receives_configured_reward(self):
        session = await create_session(
            guild_id=921, channel_id=512, game_key="word", players=self.players
        )
        result = None
        for _ in range(6):
            current = await get_session(session["id"])
            puzzle = current["state"]["puzzles"][current["state"]["round_index"]]
            actor_id = current["current_player_id"]
            answer = int(puzzle["correct"])
            if actor_id == self.players[1]:
                answer = (answer + 1) % len(puzzle["options"])
            result = await apply_action(session["id"], actor_id, {"answer": answer})
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["finished"])
        self.assertEqual(result["winner_id"], self.players[0])
        self.assertEqual(result["reward"], 15)
        account = await database.get_or_create_user(self.players[0], 921)
        self.assertEqual(account["balance"], 115)

    async def test_expired_idle_sessions_are_cancelled_for_ui_cleanup(self):
        old_session = await create_session(
            guild_id=922, channel_id=513, game_key="dice", players=self.players
        )
        fresh_session = await create_session(
            guild_id=922, channel_id=513, game_key="word", players=self.players
        )
        await attach_message(old_session["id"], 90001)
        await attach_message(fresh_session["id"], 90002)
        async with database.connect() as db:
            await db.execute(
                "UPDATE prime_game_sessions SET updated_at = datetime('now', '-4 days') WHERE id = ?",
                (old_session["id"],),
            )
            await db.commit()

        expired = await expire_stale_sessions()
        self.assertEqual([item["id"] for item in expired], [old_session["id"]])
        self.assertEqual(expired[0]["result"], "expired_idle")
        self.assertEqual((await get_session(old_session["id"]))["status"], "cancelled")
        self.assertEqual((await get_session(fresh_session["id"]))["status"], "active")

    async def test_all_game_types_restore_from_durable_active_sessions(self):
        game_keys = {"xo", "connect4", "rps", "quiz", "memory", "dice", "word"}
        expected = []
        for index, game_key in enumerate(sorted(game_keys), start=1):
            session = await create_session(
                guild_id=923, channel_id=514, game_key=game_key, players=self.players
            )
            await attach_message(session["id"], 91000 + index)
            expected.append(session["id"])

        restored = await list_active_sessions(guild_id=923, limit=None)
        self.assertEqual({item["game_key"] for item in restored}, game_keys)
        self.assertEqual({item["id"] for item in restored}, set(expected))
        for session in restored:
            view = GameBoardView(session)
            self.assertIsNone(view.timeout)
            self.assertGreater(len(view.children), 0)
            self.assertTrue(all(item.custom_id for item in view.children))

    async def test_word_challenge_has_six_fair_turns_and_resolves_tie(self):
        session = await create_session(
            guild_id=918, channel_id=509, game_key="word", players=self.players
        )
        result = None
        for _ in range(6):
            current = await get_session(session["id"])
            index = current["state"]["round_index"]
            correct = current["state"]["puzzles"][index]["correct"]
            result = await apply_action(
                session["id"], current["current_player_id"], {"answer": correct}
            )
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["finished"])
        self.assertTrue(result["is_draw"])
        self.assertEqual(result["session"]["state"]["scores"], {
            str(self.players[0]): 3,
            str(self.players[1]): 3,
        })
        self.assertEqual(result["reward"], 0)

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
