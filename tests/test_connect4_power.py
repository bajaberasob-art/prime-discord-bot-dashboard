"""Unit tests for Power-Up Connect 4 rules and Pillow board rendering."""
import io
import unittest
from types import SimpleNamespace

from cogs.connect4_power import (
    COLS, ROWS, POWERS, Connect4Player, Connect4State, apply_move,
    find_winner, landing_row, new_board, render_connect4_board,
)


def player(uid, number):
    return Connect4Player(SimpleNamespace(id=uid, name=f"Player {uid}", display_name=f"Player {uid}"), number)


class Connect4RulesTests(unittest.TestCase):
    def test_board_dimensions_and_landing(self):
        board = new_board()
        self.assertEqual(len(board), ROWS)
        self.assertTrue(all(len(row) == COLS for row in board))
        self.assertEqual(landing_row(board, 3), 5)
        board[5][3] = 1
        self.assertEqual(landing_row(board, 3), 4)

    def test_landing_rejects_bad_column_and_full_column(self):
        board = new_board()
        with self.assertRaisesRegex(ValueError, "invalid_column"):
            landing_row(board, 7)
        for row in range(ROWS):
            board[row][0] = 1
        self.assertIsNone(landing_row(board, 0))

    def test_detects_horizontal_vertical_and_diagonal_wins(self):
        board = new_board()
        board[5][1:5] = [1, 1, 1, 1]
        self.assertEqual(find_winner(board), (1, ((5, 1), (5, 2), (5, 3), (5, 4))))
        board = new_board()
        for row in range(2, 6):
            board[row][3] = 2
        winner, cells = find_winner(board)
        self.assertEqual(winner, 2)
        self.assertEqual(len(cells), 4)
        board = new_board()
        for row, col in ((5, 0), (4, 1), (3, 2), (2, 3)):
            board[row][col] = 1
        self.assertEqual(find_winner(board)[0], 1)

    def test_regular_move_alternates_turns_and_wins(self):
        state = Connect4State()
        used = {1: set(), 2: set()}
        for player_no, col in ((1, 0), (2, 0), (1, 1), (2, 1), (1, 2), (2, 2), (1, 3)):
            apply_move(state, player_no, col, used_powers=used[player_no])
        self.assertEqual(state.winner, 1)
        self.assertEqual(len(state.winning_cells), 4)
        with self.assertRaisesRegex(ValueError, "game_over"):
            apply_move(state, 2, 4)

    def test_rejects_wrong_turn_and_frozen_column(self):
        state = Connect4State()
        with self.assertRaisesRegex(ValueError, "not_your_turn"):
            apply_move(state, 2, 0)
        apply_move(state, 1, 2, power="freeze_column", used_powers=set())
        self.assertEqual(state.frozen_column, 2)
        with self.assertRaisesRegex(ValueError, "column_frozen"):
            apply_move(state, 2, 2)
        apply_move(state, 2, 1)
        self.assertIsNone(state.frozen_column)

    def test_heavy_anvil_crushes_disk_under_landing_cell(self):
        state = Connect4State()
        state.board[5][0] = 2
        state.current_player = 1
        used = set()
        result = apply_move(state, 1, 0, power="heavy_anvil", used_powers=used)
        self.assertEqual(result["row"], 5)
        self.assertEqual(state.board[5][0], 1)
        self.assertIn((5, 0), result["removed"])
        self.assertIn("heavy_anvil", used)

    def test_bomb_clears_3x3_area_and_consumes_power(self):
        state = Connect4State()
        state.board[5][3] = 2
        state.board[5][2] = 1
        state.board[4][3] = 2
        state.board[4][2] = 1
        state.current_player = 1
        used = set()
        result = apply_move(state, 1, 3, power="disk_bomb", used_powers=used)
        self.assertIn("disk_bomb", used)
        self.assertTrue(result["removed"])
        # The explosion is centered on the newly landed disk, not on
        # the bottom of the column when the center column is already stacked.
        center_row = int(result["row"])
        for r in range(max(0, center_row - 1), min(6, center_row + 2)):
            for c in range(2, 5):
                self.assertEqual(state.board[r][c], 0)
        # A disk outside the 3x3 blast radius must remain untouched.
        self.assertEqual(state.board[5][2], 1)

    def test_each_power_is_single_use(self):
        state = Connect4State()
        used = {"heavy_anvil"}
        with self.assertRaisesRegex(ValueError, "power_already_used"):
            apply_move(state, 1, 0, power="heavy_anvil", used_powers=used)

    def test_freeze_power_is_consumed(self):
        state = Connect4State()
        used = set()
        apply_move(state, 1, 4, power="freeze_column", used_powers=used)
        self.assertIn("freeze_column", used)

    def test_power_list_contains_three_unique_abilities(self):
        self.assertEqual(len(POWERS), 3)
        self.assertEqual(len(set(POWERS)), 3)


class Connect4RenderingTests(unittest.TestCase):
    def test_board_renders_valid_png_with_expected_size(self):
        players = [player(1, 1), player(2, 2)]
        state = Connect4State()
        apply_move(state, 1, 3)
        image = render_connect4_board(players, state)
        self.assertEqual(image.size, (1200, 850))
        self.assertEqual(image.mode, "RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(buffer.getvalue()), 1000)


if __name__ == "__main__":
    unittest.main()
