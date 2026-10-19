import io
import unittest
from types import SimpleNamespace

from PIL import Image

from cogs.musical_chairs import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    MusicalChairsGameView,
    render_musical_chairs_image,
    resolve_round,
)


class MusicalChairsLogicTests(unittest.TestCase):
    def test_first_players_to_sit_fill_only_available_chairs(self):
        survivors, eliminated = resolve_round(
            [10, 20, 30, 40],
            [30, 20, 20, 999, 40],
        )
        self.assertEqual(survivors, [30, 20, 40])
        self.assertEqual(eliminated, [10])

    def test_unknown_and_eliminated_players_cannot_take_a_chair(self):
        survivors, eliminated = resolve_round([10, 20, 30], [99, 10, 99])
        self.assertEqual(survivors, [10])
        self.assertEqual(eliminated, [20, 30])

    def test_one_or_zero_players_do_not_create_an_extra_elimination(self):
        self.assertEqual(resolve_round([42], []), ([42], []))
        self.assertEqual(resolve_round([], []), ([], []))

    def test_round_elimination_is_deterministic_and_ordered(self):
        survivors, eliminated = resolve_round([1, 2, 3, 4, 5], [5, 3, 1])
        self.assertEqual(survivors, [5, 3, 1, 2][:4 - 1])
        self.assertEqual(eliminated, [2, 4])


class MusicalChairsRenderingTests(unittest.TestCase):
    def setUp(self):
        self.players = [
            SimpleNamespace(id=101, name="Host", display_name="Host"),
            SimpleNamespace(id=102, name="Player Two", display_name="Player Two"),
            SimpleNamespace(id=103, name="لاعب ثلاثة", display_name="لاعب ثلاثة"),
        ]

    def test_board_renders_png_compatible_image_for_music_phase(self):
        image = render_musical_chairs_image(
            self.players,
            alive_ids=[101, 102, 103],
            round_number=1,
            phase="music",
            chair_count=2,
        )
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size[0], 1200)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_board_marks_seated_eliminated_and_winner_states(self):
        image = render_musical_chairs_image(
            self.players,
            alive_ids=[102],
            seated_ids=[102],
            eliminated_ids=[101, 103],
            round_number=3,
            phase="finished",
            chair_count=0,
            winner_id=102,
        )
        self.assertEqual(image.size[0], 1200)
        self.assertGreater(image.size[1], 400)

    def test_game_view_enforces_supported_player_count(self):
        cog = SimpleNamespace(bot=SimpleNamespace(session=None))
        two_players = [
            SimpleNamespace(id=1, name="One", display_name="One"),
            SimpleNamespace(id=2, name="Two", display_name="Two"),
        ]
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            MusicalChairsGameView(cog, two_players)

    def test_game_view_rejects_duplicate_player_ids(self):
        cog = SimpleNamespace(bot=SimpleNamespace(session=None))
        players = [
            SimpleNamespace(id=1, name="One", display_name="One"),
            SimpleNamespace(id=1, name="Duplicate", display_name="Duplicate"),
            SimpleNamespace(id=2, name="Two", display_name="Two"),
        ]
        with self.assertRaisesRegex(ValueError, "duplicate_players"):
            MusicalChairsGameView(cog, players)

    def test_supported_player_bounds_are_explicit(self):
        self.assertEqual(MIN_PLAYERS, 3)
        self.assertEqual(MAX_PLAYERS, 12)


if __name__ == "__main__":
    unittest.main()
