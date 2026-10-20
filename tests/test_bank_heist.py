import io
import random
import unittest
from types import SimpleNamespace

from cogs.bank_heist import (
    MAX_PLAYERS, MIN_PLAYERS, STAGE_NAMES, HeistPlayer, HeistState,
    normalize_decision, render_heist_board, resolve_heist_stage,
)


def players(count=3):
    return [HeistPlayer(SimpleNamespace(id=i, name="Player {}".format(i), display_name="Player {}".format(i))) for i in range(1, count+1)]


class HeistRulesTests(unittest.TestCase):
    def test_player_limits_and_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            HeistState(players(2))
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            HeistState(players(MAX_PLAYERS+1))
        duplicate = [players(3)[0], players(3)[0], players(3)[2]]
        with self.assertRaisesRegex(ValueError, "duplicate_players"):
            HeistState(duplicate)
        self.assertGreaterEqual(MIN_PLAYERS, 2)

    def test_betrayal_is_only_allowed_in_final_stage(self):
        with self.assertRaisesRegex(ValueError, "betrayal_only_final_stage"):
            normalize_decision("betray", final_stage=False)
        self.assertEqual(normalize_decision("betray", final_stage=True), "betray")
        with self.assertRaisesRegex(ValueError, "invalid_decision"):
            normalize_decision("unknown", final_stage=True)

    def test_successful_stage_increases_loot_heat_and_stage(self):
        state = HeistState(players())
        result = resolve_heist_stage(state, {1:"cooperate", 2:"cooperate", 3:"cooperate"}, stage_success=True)
        self.assertTrue(result["success"])
        self.assertEqual(state.stage, 1)
        self.assertGreater(state.loot, 0)
        self.assertGreater(state.heat, 0)
        self.assertEqual(len(state.history), 1)

    def test_failed_stage_arrests_everyone_still_inside_and_clears_pot(self):
        state = HeistState(players())
        state.loot = 5000
        result = resolve_heist_stage(state, {1:"cooperate", 2:"cooperate", 3:"cooperate"}, stage_success=False)
        self.assertEqual(set(result["arrested"]), {1,2,3})
        self.assertTrue(state.finished)
        self.assertEqual(state.loot, 0)
        self.assertTrue(all(p.status == "arrested" for p in state.players))

    def test_withdrawal_is_paid_and_withdrawn_player_is_not_arrested_on_failure(self):
        state = HeistState(players())
        state.loot = 900
        result = resolve_heist_stage(state, {1:"withdraw", 2:"cooperate", 3:"cooperate"}, stage_success=False)
        p1 = next(p for p in state.players if p.user_id == 1)
        self.assertEqual(p1.status, "withdrawn")
        self.assertGreater(p1.payout, 0)
        self.assertNotIn(1, result["arrested"])
        self.assertEqual(set(result["arrested"]), {2,3})

    def test_successful_solo_betrayal_takes_remaining_pot(self):
        state = HeistState(players())
        state.stage = 3
        state.loot = 12000
        result = resolve_heist_stage(state, {1:"betray", 2:"cooperate", 3:"cooperate"},
                                     stage_success=True, betrayal_success=True)
        thief = next(p for p in state.players if p.user_id == 1)
        self.assertEqual(thief.status, "betrayed")
        self.assertGreaterEqual(thief.payout, 12000)
        self.assertEqual(result["betrayal_winner_id"], 1)
        self.assertTrue(state.finished)
        self.assertTrue(all(p.status == "arrested" for p in state.players if p.user_id != 1))

    def test_final_team_success_splits_pot_when_no_betrayal(self):
        state = HeistState(players())
        state.stage = 3
        state.loot = 9000
        resolve_heist_stage(state, {1:"cooperate", 2:"cooperate", 3:"withdraw"}, stage_success=True)
        self.assertTrue(state.finished)
        self.assertEqual(next(p for p in state.players if p.user_id == 3).status, "withdrawn")
        self.assertTrue(all(p.status in {"winner", "withdrawn"} for p in state.players))

    def test_cannot_resolve_finished_game(self):
        state = HeistState(players())
        state.finished = True
        with self.assertRaisesRegex(ValueError, "game_already_finished"):
            resolve_heist_stage(state, {1:"cooperate"})


class HeistRenderingTests(unittest.TestCase):
    def test_board_is_valid_png_and_has_expected_size(self):
        state_players = players(4)
        image = render_heist_board(state_players, stage=1, loot=12500, heat=72, last_event="اختبار")
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (1200, 820))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(buffer.getvalue()), 1000)

    def test_stage_labels_cover_four_stages(self):
        self.assertEqual(len(STAGE_NAMES), 4)
        self.assertIn("ركوب سيارة الهروب", STAGE_NAMES)


if __name__ == "__main__":
    unittest.main()
