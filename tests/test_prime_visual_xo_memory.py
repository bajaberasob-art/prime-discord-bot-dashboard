import io
import unittest
from unittest.mock import patch

from PIL import Image

from prime_games.registry import registry
from prime_games.visual_renderers import render_memory_board, render_xo_board


class PrimeVisualXOMemoryTests(unittest.TestCase):
    def setUp(self):
        self.players = [1101, 2202]

    def test_infinite_xo_removes_each_players_oldest_piece_on_fourth_move(self):
        game = registry.get("xo")
        state = game.initial_state(self.players)
        moves = [
            (1101, 0), (2202, 3),
            (1101, 2), (2202, 4),
            (1101, 6), (2202, 8),
            (1101, 5),
        ]
        outcome = None
        for actor, index in moves:
            outcome = game.reduce_action(state, self.players, actor, {"index": index})
            state = outcome.state
        self.assertIsNone(outcome.winner_id)
        self.assertFalse(outcome.is_draw)
        self.assertIsNone(state["board"][0])
        self.assertEqual(sum(value == "X" for value in state["board"]), 3)
        self.assertEqual(sum(value == "O" for value in state["board"]), 3)
        self.assertEqual(
            [item["index"] for item in state["move_history"] if item["player_id"] == 1101],
            [2, 6, 5],
        )

    def test_infinite_xo_still_detects_a_win(self):
        game = registry.get("xo")
        state = game.initial_state(self.players)
        for actor, index in [(1101, 0), (2202, 3), (1101, 1), (2202, 4), (1101, 2)]:
            outcome = game.reduce_action(state, self.players, actor, {"index": index})
            state = outcome.state
        self.assertEqual(outcome.winner_id, 1101)

    def test_memory_has_seven_pairs_and_two_traps(self):
        state = registry.get("memory").initial_state(self.players)
        self.assertEqual(len(state["cards"]), 16)
        self.assertEqual(state["cards"].count("🃏"), 2)
        regular = [card for card in state["cards"] if card != "🃏"]
        self.assertEqual(len(regular), 14)
        self.assertEqual(set(regular.count(card) for card in set(regular)), {2})
        self.assertEqual(state["combo"], {"1101": 0, "2202": 0})

    def test_memory_combo_multiplier_accumulates_points(self):
        game = registry.get("memory")
        cards = ["🍎", "🍎", "⭐", "⭐", "🔷", "🔷", "🌙", "🌙",
                 "🔥", "🔥", "🎈", "🎈", "🍀", "🍀", "🃏", "🃏"]
        state = {
            "cards": cards, "matched": [], "revealed": [], "pending_index": None,
            "scores": {"1101": 0, "2202": 0}, "pairs": {"1101": 0, "2202": 0},
            "combo": {"1101": 0, "2202": 0},
        }
        state = game.reduce_action(state, self.players, 1101, {"index": 0}).state
        first_pair = game.reduce_action(state, self.players, 1101, {"index": 1})
        state = first_pair.state
        self.assertEqual(state["scores"]["1101"], 1)
        self.assertEqual(state["combo"]["1101"], 1)
        state = game.reduce_action(state, self.players, 1101, {"index": 2}).state
        second_pair = game.reduce_action(state, self.players, 1101, {"index": 3})
        self.assertEqual(second_pair.state["scores"]["1101"], 3)
        self.assertEqual(second_pair.state["combo"]["1101"], 2)
        self.assertEqual(second_pair.state["last_combo_multiplier"], 2)

    def test_memory_trap_shuffles_two_unmatched_cards_and_breaks_combo(self):
        game = registry.get("memory")
        cards = ["🃏", "🍎", "⭐", "🔷", "🌙", "🔥", "🎈", "🍀",
                 "🍎", "⭐", "🔷", "🌙", "🔥", "🎈", "🍀", "🃏"]
        state = {
            "cards": cards, "matched": [], "revealed": [0], "pending_index": 0,
            "scores": {"1101": 0, "2202": 0}, "pairs": {"1101": 0, "2202": 0},
            "combo": {"1101": 3, "2202": 0},
        }
        with patch("prime_games.registry.random.sample", return_value=[2, 3]):
            outcome = game.reduce_action(state, self.players, 1101, {"index": 1})
        self.assertEqual(outcome.state["last_match"], "trap")
        self.assertEqual(outcome.state["last_trap_indices"], [2, 3])
        self.assertEqual(outcome.state["cards"][2:4], ["🔷", "⭐"])
        self.assertEqual(outcome.state["combo"]["1101"], 0)
        self.assertEqual(outcome.next_player_id, 2202)

    def test_visual_renderers_return_valid_png_images(self):
        avatars = [
            Image.new("RGBA", (64, 64), (40, 200, 220, 255)),
            Image.new("RGBA", (64, 64), (240, 80, 120, 255)),
        ]
        xo = render_xo_board(
            {"board": ["X", "O", None, None, "X", None, "O", None, None],
             "move_history": [{"player_id": 1101, "index": 7}]},
            self.players, ["Alpha", "Beta"], avatars, 1101, None, "active",
        )
        memory = render_memory_board(
            {"cards": ["🍎"] * 16, "matched": [0, 1], "revealed": [2],
             "scores": {"1101": 3, "2202": 1}, "combo": {"1101": 2, "2202": 0}},
            self.players, ["Alpha", "Beta"], 1101, "active",
        )
        for image in (xo, memory):
            self.assertEqual(image.size, (1120, 720))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))


if __name__ == "__main__":
    unittest.main()
