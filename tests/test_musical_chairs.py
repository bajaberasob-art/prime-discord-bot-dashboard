import io
import unittest
from types import SimpleNamespace

from cogs.musical_chairs import (
    MAX_PLAYERS,
    MIN_PLAYERS,
    MUSIC_MIN_SECONDS,
    MUSIC_MAX_SECONDS,
    eliminate_early_press,
    MusicalChairsGameView,
    render_musical_chairs_image,
    resolve_round,
)
from game_engine import draw_arabic_text, _font


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
        self.assertEqual(survivors, [5, 3, 1])
        self.assertEqual(eliminated, [2, 4])


    def test_early_press_eliminates_only_the_player_who_clicked(self):
        alive, eliminated = eliminate_early_press([10, 20, 30], 20)
        self.assertEqual(alive, [10, 30])
        self.assertTrue(eliminated)

    def test_early_press_from_non_player_does_not_change_round(self):
        alive, eliminated = eliminate_early_press([10, 20, 30], 99)
        self.assertEqual(alive, [10, 20, 30])
        self.assertFalse(eliminated)



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
            chair_assignments={0: 101},
        )
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size[0], 1200)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_arabic_and_mixed_direction_text_draws_without_error(self):
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (900, 160), "#111625")
        draw = ImageDraw.Draw(image)
        samples = (
            "الكراسي الموسيقية",
            "ROUND 2 • توقفت الموسيقى! اجلس بسرعة",
            "اللاعبون الباقون: 3   •   الكراسي: 2",
            "كرسي 1 • Abdullah 🎵",
        )
        for index, sample in enumerate(samples):
            draw_arabic_text(
                draw,
                (850, 18 + index * 34),
                sample,
                font=_font(22, bold=index == 0),
                anchor="ra",
            )
        # Ensure the output contains visible glyphs rather than a blank canvas.
        self.assertGreater(len(image.getbbox()), 0)
        self.assertGreater(len(set(image.getdata())), 1)

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

    def test_game_view_builds_one_button_for_each_chair(self):
        cog = SimpleNamespace(bot=SimpleNamespace(session=None))
        players = [
            SimpleNamespace(id=index, name=f"Player {index}", display_name=f"Player {index}")
            for index in range(1, 11)
        ]
        view = MusicalChairsGameView(cog, players)
        self.assertEqual(len(view.chair_buttons), 9)
        self.assertEqual(len(view.children), 9)
        self.assertTrue(all(button.style.value == 3 for button in view.chair_buttons.values()))

    def test_game_view_rejects_more_than_ten_players(self):
        cog = SimpleNamespace(bot=SimpleNamespace(session=None))
        players = [
            SimpleNamespace(id=index, name=f"Player {index}", display_name=f"Player {index}")
            for index in range(1, 12)
        ]
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            MusicalChairsGameView(cog, players)

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
        self.assertEqual(MAX_PLAYERS, 10)
        self.assertEqual(MUSIC_MIN_SECONDS, 4.0)
        self.assertEqual(MUSIC_MAX_SECONDS, 8.0)


if __name__ == "__main__":
    unittest.main()
