"""Tests for Visual Multiplayer UNO deck, rules, turns and Pillow board."""
import io
import random
import unittest
from types import SimpleNamespace

from PIL import Image

from cogs.uno_visual import (
    UNOCard, UNOPlayer, UNOState, UNO_COLORS, COLOR_HEX, MIN_PLAYERS,
    MAX_PLAYERS, create_deck, can_play, playable_cards, render_uno_board,
)


def member(uid, name=None):
    return SimpleNamespace(id=uid, name=name or f"Player {uid}", display_name=name or f"Player {uid}")


class UNODeckTests(unittest.TestCase):
    def test_standard_deck_has_108_cards_and_correct_distribution(self):
        deck = create_deck(random.Random(5))
        self.assertEqual(len(deck), 108)
        for color in UNO_COLORS:
            cards = [c for c in deck if c.color == color]
            self.assertEqual(sum(c.value == "0" for c in cards), 1)
            for value in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "skip", "reverse", "draw2"):
                self.assertEqual(sum(c.value == value for c in cards), 2)
        self.assertEqual(sum(c.value == "wild" for c in deck), 4)
        self.assertEqual(sum(c.value == "wild4" for c in deck), 4)

    def test_playability_matches_color_value_or_wild(self):
        top = UNOCard("red", "5")
        self.assertTrue(can_play(UNOCard("red", "2"), top, "red"))
        self.assertTrue(can_play(UNOCard("blue", "5"), top, "red"))
        self.assertTrue(can_play(UNOCard(None, "wild"), top, "red"))
        self.assertFalse(can_play(UNOCard("green", "2"), top, "red"))

    def test_playable_indices_are_stable(self):
        self.assertEqual(playable_cards([UNOCard("red", "1"), UNOCard("blue", "2"), UNOCard(None, "wild")],
                                        UNOCard("red", "7"), "red"), [0, 2])


class UNORulesTests(unittest.TestCase):
    def setUp(self):
        self.state = UNOState.new_game([member(i) for i in range(1, 5)], random.Random(10))

    def test_player_limits_are_enforced(self):
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            UNOState.new_game([member(1)])
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            UNOState.new_game([member(i) for i in range(1, 10)])

    def test_new_game_deals_seven_cards_and_starts_with_number(self):
        self.assertTrue(all(len(p.hand) == 7 for p in self.state.players))
        self.assertTrue(self.state.top_card.value.isdigit())
        self.assertEqual(len(self.state.draw_pile) + sum(len(p.hand) for p in self.state.players) + len(self.state.discard_pile), 108)

    def test_wrong_turn_and_invalid_card_are_rejected(self):
        current = self.state.current_player
        with self.assertRaisesRegex(ValueError, "not_your_turn"):
            self.state.play_card(self.state.players[1].user_id, 0)
        with self.assertRaisesRegex(ValueError, "invalid_card_index"):
            self.state.play_card(current.user_id, 999)

    def test_illegal_card_is_rejected(self):
        current = self.state.current_player
        current.hand = [UNOCard("green", "1")]
        self.state.discard_pile = [UNOCard("red", "2")]
        self.state.current_color = "red"
        with self.assertRaisesRegex(ValueError, "card_not_playable"):
            self.state.play_card(current.user_id, 0)

    def test_wild_requires_valid_color(self):
        current = self.state.current_player
        current.hand = [UNOCard(None, "wild")]
        with self.assertRaisesRegex(ValueError, "choose_color"):
            self.state.play_card(current.user_id, 0)
        self.state.play_card(current.user_id, 0, chosen_color="blue")
        self.assertEqual(self.state.current_color, "blue")

    def test_reverse_changes_direction_and_two_player_reverse_skips(self):
        current = self.state.current_player
        self.state.players = self.state.players[:2]
        current.hand = [UNOCard("red", "reverse"), UNOCard("blue", "2")]
        self.state.discard_pile = [UNOCard("red", "1")]
        self.state.current_color = "red"
        self.state.play_card(current.user_id, 0)
        self.assertEqual(self.state.direction, -1)
        self.assertEqual(self.state.current_index, 0)

    def test_skip_and_draw_two_skip_next_player(self):
        current = self.state.current_player
        current.hand = [UNOCard("red", "skip"), UNOCard("blue", "2")]
        self.state.discard_pile = [UNOCard("red", "1")]
        self.state.current_color = "red"
        self.state.play_card(current.user_id, 0)
        self.assertEqual(self.state.current_index, 2)
        current = self.state.current_player
        current.hand = [UNOCard("blue", "draw2"), UNOCard("green", "4")]
        self.state.discard_pile = [UNOCard("blue", "3")]
        self.state.current_color = "blue"
        self.state.play_card(current.user_id, 0)
        self.assertEqual(len(self.state.players[3].hand), 9)
        self.assertEqual(self.state.current_index, 0)

    def test_draw_requires_current_player_and_returns_card(self):
        current = self.state.current_player
        cards = self.state.draw_cards(current.user_id, 1)
        self.assertEqual(len(cards), 1)
        self.assertEqual(current.hand[-1], cards[0])

    def test_catch_uno_draws_two_cards_from_target(self):
        current = self.state.current_player
        target = self.state.players[1]
        target.hand = [UNOCard("red", "7")]
        target.uno_called = False
        before = len(target.hand)
        caught = self.state.catch_uno(current.user_id, target.user_id)
        self.assertEqual(caught, 2)
        self.assertEqual(len(target.hand), before + 2)

    def test_call_uno_marks_single_card_player(self):
        player = self.state.current_player
        player.hand = [UNOCard("red", "7")]
        self.assertTrue(self.state.call_uno(player.user_id))
        self.assertTrue(player.uno_called)

    def test_winning_card_finishes_game(self):
        current = self.state.current_player
        current.hand = [UNOCard("red", "7")]
        self.state.discard_pile = [UNOCard("red", "2")]
        self.state.current_color = "red"
        self.state.play_card(current.user_id, 0)
        self.assertTrue(self.state.finished)
        self.assertEqual(self.state.winner_id, current.user_id)


class UNORenderingTests(unittest.TestCase):
    def test_table_board_renders_png(self):
        state = UNOState.new_game([member(i) for i in range(1, 5)], random.Random(7))
        image = render_uno_board(state, {})
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (1240, 820))
        output = io.BytesIO()
        image.save(output, format="PNG")
        self.assertTrue(output.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(output.getvalue()), 1000)


if __name__ == "__main__":
    unittest.main()
