import unittest
from unittest.mock import patch

from prime_games.registry import (
    _initial_dice_state,
    _initial_memory_state,
    _initial_rps_state,
    _reduce_dice_action,
    _reduce_memory_action,
    _reduce_rps_action,
    _reduce_word_action,
)


class ClassicGameMechanicsTests(unittest.TestCase):
    def test_memory_combo_multiplier_scores_more_and_retains_extra_turn(self):
        players = [101, 202]
        state = {
            "cards": ["A", "A", "B", "B", "C", "C", "D", "D",
                      "E", "E", "F", "F", "G", "G", "H", "H"],
            "matched": [], "revealed": [], "pending_index": None,
            "scores": {"101": 0, "202": 0}, "pairs": {"101": 0, "202": 0},
            "combo": {"101": 0, "202": 0}, "trap_used": {"101": False, "202": False},
        }
        for index in (0, 1, 2, 3):
            state = _reduce_memory_action(state, players, 101, {"index": index}).state
        self.assertEqual(state["combo"]["101"], 2)
        self.assertEqual(state["combo_multiplier"], 2)
        self.assertEqual(state["scores"]["101"], 3)  # 1 + 2 points
        self.assertEqual(state["pairs"]["101"], 2)

    def test_memory_trap_shuffles_two_closed_cards_once(self):
        players = [101, 202]
        state = {
            "cards": list("ABCDEFGHIJKLMNOP"), "matched": [], "revealed": [],
            "pending_index": None, "scores": {"101": 0, "202": 0},
            "pairs": {"101": 0, "202": 0}, "combo": {"101": 0, "202": 0},
            "trap_used": {"101": False, "202": False},
        }
        result = _reduce_memory_action(state, players, 101, {"trap": True})
        self.assertNotEqual(result.state["cards"], state["cards"])
        self.assertTrue(result.state["trap_used"]["101"])
        self.assertEqual(result.next_player_id, 202)
        with self.assertRaisesRegex(ValueError, "trap_used"):
            _reduce_memory_action(result.state, players, 101, {"trap": True})

    def test_word_hint_is_private_state_and_costs_one_point_not_two(self):
        players = [101, 202]
        state = {
            "round_index": 0,
            "puzzles": [{"scrambled": "مرق", "options": ["قمر", "نجم", "شمس", "بحر"], "correct": 0}],
            "scores": {"101": 2, "202": 0},
        }
        hinted = _reduce_word_action(state, players, 101, {"hint": True}).state
        self.assertEqual(hinted["hints"]["0"], "ق")
        self.assertEqual(hinted["scores"]["101"], 1)
        solved = _reduce_word_action(hinted, players, 101, {"answer_text": "قمر"}).state
        self.assertEqual(solved["scores"]["101"], 2)
        self.assertTrue(solved["last_answer"]["correct"])

    def test_word_hint_without_points_forfeits_round_point(self):
        players = [101, 202]
        state = {
            "round_index": 0,
            "puzzles": [{"scrambled": "مرق", "options": ["قمر", "نجم", "شمس", "بحر"], "correct": 0}],
            "scores": {"101": 0, "202": 0},
        }
        hinted = _reduce_word_action(state, players, 101, {"hint": True}).state
        solved = _reduce_word_action(hinted, players, 101, {"answer_text": "قمر"}).state
        self.assertEqual(solved["scores"]["101"], 0)

    def test_rps_double_strike_deals_two_hp_damage(self):
        players = [101, 202]
        state = _initial_rps_state(players)
        state = _reduce_rps_action(state, players, 101, {"ability": "double"}).state
        state = _reduce_rps_action(state, players, 101, {"choice": "rock"}).state
        state = _reduce_rps_action(state, players, 202, {"choice": "scissors"}).state
        self.assertEqual(state["hp"]["202"], 1)
        self.assertEqual(state["last_round"]["damage"], 2)

    def test_rps_shield_blocks_incoming_double_damage(self):
        players = [101, 202]
        state = _initial_rps_state(players)
        state = _reduce_rps_action(state, players, 101, {"ability": "double"}).state
        state = _reduce_rps_action(state, players, 101, {"choice": "rock"}).state
        state = _reduce_rps_action(state, players, 202, {"ability": "shield"}).state
        state = _reduce_rps_action(state, players, 202, {"choice": "scissors"}).state
        self.assertEqual(state["hp"]["202"], 3)
        self.assertEqual(state["last_round"]["damage"], 0)

    def test_dice_reroll_is_once_per_match_and_double_attack_reduces_hp(self):
        players = [101, 202]
        state = _initial_dice_state(players)
        state = _reduce_dice_action(state, players, 101, {"ability": "double"}).state
        with patch("prime_games.registry.random.randint", side_effect=[2, 6, 3]):
            state = _reduce_dice_action(state, players, 101, {"roll": "roll"}).state
            state = _reduce_dice_action(state, players, 101, {"reroll": True}).state
            self.assertEqual(state["round_rolls"]["101"], 6)
            with self.assertRaisesRegex(ValueError, "ability_used"):
                _reduce_dice_action(state, players, 101, {"reroll": True})
            state = _reduce_dice_action(state, players, 101, {"lock": True}).state
            state = _reduce_dice_action(state, players, 202, {"roll": "roll"}).state
            state = _reduce_dice_action(state, players, 202, {"lock": True}).state
        self.assertEqual(state["hp"]["202"], 1)
        self.assertEqual(state["last_round"]["damage"], 2)


if __name__ == "__main__":
    unittest.main()
