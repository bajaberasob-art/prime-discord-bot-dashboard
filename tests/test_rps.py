import os
import tempfile
import unittest

import database
from cogs.games import GameBoardView, _game_embed
from prime_games.engine import apply_action, create_session, ensure_game_schema
from prime_games.registry import registry


class RockPaperScissorsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime-rps.db")
        await database.init_db()
        await ensure_game_schema()

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def _session(self):
        return await create_session(
            guild_id=810,
            channel_id=410,
            game_key="rps",
            players=[101, 202],
        )

    async def test_registry_uses_existing_reward_policy(self):
        game = registry.get("rps")
        self.assertEqual(game.min_players, 2)
        self.assertEqual(game.max_players, 2)
        self.assertEqual(game.reward_coins, 25)
        self.assertEqual(game.initial_state([101, 202]), {"choices": {}})

    async def test_first_choice_is_secret_until_both_players_choose(self):
        session = await self._session()
        first = await apply_action(session["id"], 101, {"choice": "rock"})

        self.assertTrue(first["ok"])
        self.assertFalse(first["finished"])
        self.assertEqual(first["session"]["current_player_id"], 202)
        self.assertEqual(first["session"]["state"]["choices"], {"101": "rock"})
        embed = _game_embed(first["session"])
        visible_text = str(embed.to_dict()).lower()
        self.assertNotIn("rock", visible_text)
        self.assertNotIn("الاختيارات المكشوفة", visible_text)

        wrong_turn = await apply_action(session["id"], 101, {"choice": "paper"})
        self.assertFalse(wrong_turn["ok"])
        self.assertEqual(wrong_turn["reason"], "not_your_turn")

    async def test_rock_beats_scissors_and_uses_existing_wallet(self):
        session = await self._session()
        first = await apply_action(session["id"], 101, {"choice": "rock"})
        self.assertTrue(first["ok"])
        second = await apply_action(session["id"], 202, {"choice": "scissors"})

        self.assertTrue(second["ok"])
        self.assertTrue(second["finished"])
        self.assertEqual(second["winner_id"], 101)
        self.assertEqual(second["reward"], 25)
        self.assertEqual(second["session"]["status"], "finished")
        account = await database.get_or_create_user(101, 810)
        self.assertEqual(account["balance"], 125)

        visible_text = str(_game_embed(second["session"], reward=25).to_dict())
        self.assertIn("الاختيارات المكشوفة", visible_text)
        self.assertIn("🪨 حجر", visible_text)
        self.assertIn("✂️ مقص", visible_text)

    async def test_equal_choices_end_in_draw_without_reward(self):
        session = await self._session()
        first = await apply_action(session["id"], 101, {"choice": "paper"})
        second = await apply_action(session["id"], 202, {"choice": "paper"})
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["finished"])
        self.assertTrue(second["is_draw"])
        self.assertIsNone(second["winner_id"])
        self.assertEqual(second["reward"], 0)

    async def test_invalid_choice_is_rejected_without_changing_turn(self):
        session = await self._session()
        invalid = await apply_action(session["id"], 101, {"choice": "fire"})
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["reason"], "invalid_choice")
        fresh = await apply_action(session["id"], 101, {"choice": "scissors"})
        self.assertTrue(fresh["ok"])
        self.assertEqual(fresh["session"]["current_player_id"], 202)

    async def test_persistent_view_exposes_three_stable_choice_buttons(self):
        session = await self._session()
        view = GameBoardView(session)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(
            [item.custom_id for item in view.children],
            [
                f"primegame:rps:{session['id']}:rock",
                f"primegame:rps:{session['id']}:paper",
                f"primegame:rps:{session['id']}:scissors",
            ],
        )
        self.assertTrue(all(not item.disabled for item in view.children))

        ended = {**session, "status": "finished"}
        self.assertTrue(all(item.disabled for item in GameBoardView(ended).children))


if __name__ == "__main__":
    unittest.main()
