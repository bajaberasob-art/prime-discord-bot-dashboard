import asyncio
import io
import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.buckshot import (
    MAX_HP,
    TACTICAL_ITEMS,
    BuckshotGame,
    BuckshotPlayer,
    Buckshot,
    apply_item,
    deal_round_items,
    load_shells,
    render_buckshot_board,
    resolve_shot,
)


def member(user_id: int, name: str = "Player") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, name=f"{name} {user_id}", display_name=f"{name} {user_id}")


class BuckshotAmmoTests(unittest.TestCase):
    def test_magazine_has_two_to_six_shells_and_both_types(self):
        for seed in range(100):
            shells = load_shells(random.Random(seed))
            self.assertGreaterEqual(len(shells), 2)
            self.assertLessEqual(len(shells), 6)
            self.assertIn(True, shells)
            self.assertIn(False, shells)

    def test_round_items_are_randomly_assigned_to_each_living_player(self):
        players = [BuckshotPlayer(member(i)) for i in range(1, 5)]
        players[-1].alive = False
        inventory = deal_round_items(players, random.Random(7))
        self.assertEqual(set(inventory), {1, 2, 3})
        self.assertTrue(set(inventory.values()).issubset(set(TACTICAL_ITEMS)))

    def test_blank_self_shot_keeps_turn_but_blank_opponent_shot_does_not(self):
        self.assertEqual(resolve_shot(False, target_self=True, shooter_hp=2, target_hp=3), (2, 3, True))
        self.assertEqual(resolve_shot(False, target_self=False, shooter_hp=3, target_hp=2), (3, 2, False))

    def test_live_self_shot_deals_one_or_two_damage_and_passes_turn(self):
        self.assertEqual(resolve_shot(True, target_self=True, shooter_hp=3, target_hp=3), (2, 3, False))
        self.assertEqual(resolve_shot(True, target_self=True, shooter_hp=3, target_hp=3, damage=2), (1, 3, False))

    def test_live_shot_damages_target_and_clamps_hp_at_zero(self):
        self.assertEqual(resolve_shot(True, target_self=False, shooter_hp=3, target_hp=1, damage=2), (3, 0, False))


class BuckshotItemTests(unittest.TestCase):
    def setUp(self):
        self.player = BuckshotPlayer(member(10))
        self.player.inventory = set(TACTICAL_ITEMS)

    def test_magnifier_consumes_once(self):
        self.assertTrue(apply_item(self.player, "magnifier"))
        self.assertNotIn("magnifier", self.player.inventory)
        self.assertFalse(apply_item(self.player, "magnifier"))

    def test_saw_and_handcuffs_are_single_use_inventory_items(self):
        self.assertTrue(apply_item(self.player, "saw"))
        self.assertTrue(apply_item(self.player, "handcuffs"))
        self.assertFalse(apply_item(self.player, "saw"))
        self.assertFalse(apply_item(self.player, "handcuffs"))

    def test_cigarettes_restore_one_hp_but_never_exceed_starting_health(self):
        self.player.hp = 1
        self.assertTrue(apply_item(self.player, "cigarettes"))
        self.assertEqual(self.player.hp, 2)
        self.player.inventory.add("cigarettes")
        self.player.hp = MAX_HP
        self.assertFalse(apply_item(self.player, "cigarettes"))
        self.assertEqual(self.player.hp, MAX_HP)
        self.assertIn("cigarettes", self.player.inventory)

    def test_item_is_not_consumed_when_missing(self):
        self.player.inventory.clear()
        self.assertFalse(apply_item(self.player, "saw"))


class BuckshotLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_release_clears_only_its_own_session(self):
        cog = Buckshot.__new__(Buckshot)
        cog.active_games = {}
        cog._game_lock = asyncio.Lock()
        game = BuckshotGame(cog, [member(1), member(2)])
        game.guild_id = 123
        cog.active_games[123] = game
        await cog._release_game(game)
        self.assertNotIn(123, cog.active_games)

    async def test_release_does_not_remove_a_replacement_session(self):
        cog = Buckshot.__new__(Buckshot)
        cog.active_games = {}
        cog._game_lock = asyncio.Lock()
        old = BuckshotGame(cog, [member(1), member(2)])
        new = BuckshotGame(cog, [member(3), member(4)])
        old.guild_id = new.guild_id = 123
        cog.active_games[123] = new
        await cog._release_game(old)
        self.assertIs(cog.active_games[123], new)


class BuckshotRenderingTests(unittest.TestCase):
    def test_board_is_rgba_and_serializes_to_a_valid_png_header(self):
        players = [BuckshotPlayer(member(1, "Alice")), BuckshotPlayer(member(2, "Bob"))]
        image = render_buckshot_board(
            players, shells_left=4, live_left=2, round_number=1,
            current_id=1, last_event="اختبار اللوحة",
        )
        self.assertEqual(image.mode, "RGBA")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(buffer.getvalue()), 1000)


if __name__ == "__main__":
    unittest.main()
