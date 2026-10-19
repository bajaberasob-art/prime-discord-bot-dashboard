import unittest

from cogs.games import GameBoardView, _game_embed
from prime_games.registry import registry


class ExpandedMiniGameTests(unittest.TestCase):
    def setUp(self):
        self.players = [1101, 2202]

    def test_registry_exposes_all_four_playable_games(self):
        self.assertEqual(
            {game.key for game in registry.all()},
            {"xo", "connect4", "rps", "quiz"},
        )
        self.assertEqual(registry.get("xo").reward_coins, 25)
        self.assertEqual(registry.get("connect4").reward_coins, 20)
        self.assertEqual(registry.get("rps").reward_coins, 10)
        self.assertEqual(registry.get("quiz").reward_coins, 15)

    def test_connect_four_finds_horizontal_winner(self):
        game = registry.get("connect4")
        state = game.initial_state(self.players)
        moves = [
            (1101, 0), (2202, 6),
            (1101, 1), (2202, 6),
            (1101, 2), (2202, 5),
            (1101, 3),
        ]
        outcome = None
        for player, column in moves:
            outcome = game.reduce_action(state, self.players, player, {"column": column})
            state = outcome.state
            if outcome.winner_id is not None:
                break

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.winner_id, 1101)
        self.assertEqual(outcome.state["board"][5 * 7:6 * 7], ["R", "R", "R", "R", None, "Y", "Y"])

    def test_connect_four_rejects_a_full_column(self):
        game = registry.get("connect4")
        state = game.initial_state(self.players)
        current = 1101
        for _ in range(6):
            outcome = game.reduce_action(state, self.players, current, {"column": 0})
            state = outcome.state
            current = 2202 if current == 1101 else 1101
        with self.assertRaisesRegex(ValueError, "column_full"):
            game.reduce_action(state, self.players, current, {"column": 0})

    def test_rock_paper_scissors_keeps_first_pick_unresolved_until_second_pick(self):
        game = registry.get("rps")
        state = game.initial_state(self.players)
        first = game.reduce_action(state, self.players, 1101, {"choice": "rock"})
        self.assertIsNone(first.winner_id)
        self.assertFalse(first.is_draw)
        self.assertEqual(first.next_player_id, 2202)

        second = game.reduce_action(first.state, self.players, 2202, {"choice": "scissors"})
        self.assertEqual(second.winner_id, 1101)
        self.assertIsNone(second.next_player_id)
        self.assertEqual(second.state["choices"], {"1101": "rock", "2202": "scissors"})

    def test_rock_paper_scissors_tie_is_a_draw(self):
        game = registry.get("rps")
        state = game.initial_state(self.players)
        first = game.reduce_action(state, self.players, 1101, {"choice": "paper"})
        second = game.reduce_action(first.state, self.players, 2202, {"choice": "paper"})
        self.assertTrue(second.is_draw)
        self.assertIsNone(second.winner_id)

    def test_quiz_awards_point_and_switches_turn(self):
        game = registry.get("quiz")
        state = game.initial_state(self.players)
        question = state["questions"][0]
        answer = question["correct"]
        outcome = game.reduce_action(state, self.players, 1101, {"answer": answer})
        self.assertEqual(outcome.state["scores"]["1101"], 1)
        self.assertEqual(outcome.next_player_id, 2202)
        self.assertTrue(outcome.state["last_answer"]["correct"])
        self.assertEqual(outcome.state["question_index"], 1)

    def test_game_views_have_valid_controls_for_each_new_game(self):
        connect_view = GameBoardView({
            "id": 10, "game_key": "connect4", "status": "active",
            "state": {"board": [None] * 42},
        })
        rps_view = GameBoardView({
            "id": 11, "game_key": "rps", "status": "active",
            "state": {"choices": {}},
        })
        quiz_state = registry.get("quiz").initial_state(self.players)
        quiz_view = GameBoardView({
            "id": 12, "game_key": "quiz", "status": "active",
            "state": quiz_state,
        })
        self.assertEqual(len(connect_view.children), 7)
        self.assertEqual(len(rps_view.children), 3)
        self.assertEqual(len(quiz_view.children), 4)
        self.assertTrue(all(item.custom_id for item in connect_view.children + rps_view.children + quiz_view.children))

    def test_rps_embed_does_not_leak_first_players_choice(self):
        embed = _game_embed({
            "id": 13,
            "game_key": "rps",
            "status": "active",
            "players": self.players,
            "current_player_id": 2202,
            "winner_id": None,
            "state": {"choices": {"1101": "rock"}},
        })
        self.assertNotIn("🪨 حجر", embed.description or "")
        self.assertIn("بشكل مخفي", embed.description or "")


if __name__ == "__main__":
    unittest.main()
