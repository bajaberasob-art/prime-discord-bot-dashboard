"""Unit tests for Mafia Royale role configuration, night actions and rendering."""
import io
import random
import unittest
from types import SimpleNamespace

from cogs.mafia import (
    ROLE_ORDER, MafiaPlayer, MafiaSettings, distribute_roles, investigate,
    resolve_night, tally_trial, check_victory, render_setup_board,
    render_mafia_board,
)


def member(uid):
    return SimpleNamespace(id=uid, name=f"Player {uid}", display_name=f"Player {uid}")


def players_with_roles(*roles):
    return [MafiaPlayer(member(index + 1), role=role) for index, role in enumerate(roles)]


class MafiaSettingsTests(unittest.TestCase):
    def test_citizen_is_always_enabled(self):
        settings = MafiaSettings(enabled_roles={"godfather"})
        settings.toggle_role("citizen")
        self.assertIn("citizen", settings.enabled_roles)
        settings.toggle_role("godfather")
        self.assertIn("citizen", settings.enabled_roles)

    def test_unknown_role_and_option_are_rejected(self):
        settings = MafiaSettings()
        with self.assertRaisesRegex(ValueError, "unknown_role"):
            settings.toggle_role("wizard")
        with self.assertRaisesRegex(ValueError, "unknown_option"):
            settings.toggle_option("unknown")

    def test_validate_player_count_and_mafia_role(self):
        settings = MafiaSettings(enabled_roles={"citizen", "doctor"})
        with self.assertRaisesRegex(ValueError, "enable_at_least_one_mafia_role"):
            settings.validate(5)
        settings.enabled_roles.add("godfather")
        with self.assertRaisesRegex(ValueError, "invalid_player_count"):
            settings.validate(4)
        settings.validate(5)

    def test_all_roles_have_labels_and_descriptions(self):
        from cogs.mafia import ROLE_LABELS, ROLE_DESCRIPTIONS
        self.assertEqual(set(ROLE_ORDER), set(ROLE_LABELS))
        self.assertEqual(set(ROLE_ORDER), set(ROLE_DESCRIPTIONS))


class MafiaRoleTests(unittest.TestCase):
    def test_distribution_uses_enabled_roles_and_fills_with_citizens(self):
        settings = MafiaSettings(enabled_roles={"godfather", "detective", "doctor", "citizen"})
        members = [member(i) for i in range(1, 7)]
        result = distribute_roles(members, settings, random.Random(7))
        self.assertEqual(len(result), 6)
        roles = [p.role for p in result]
        self.assertEqual(roles.count("godfather"), 1)
        self.assertEqual(roles.count("detective"), 1)
        self.assertEqual(roles.count("doctor"), 1)
        self.assertEqual(roles.count("citizen"), 3)

    def test_investigation_godfather_appears_innocent(self):
        godfather = MafiaPlayer(member(1), role="godfather")
        self.assertEqual(investigate(godfather), "innocent")
        godfather.framed = True
        self.assertEqual(investigate(godfather), "mafia")

    def test_framed_citizen_appears_mafia(self):
        citizen = MafiaPlayer(member(1), role="citizen", framed=True)
        self.assertEqual(investigate(citizen), "mafia")

    def test_lovers_are_linked_when_two_are_present(self):
        settings = MafiaSettings(enabled_roles={"godfather", "lover", "citizen"})
        result = distribute_roles([member(i) for i in range(1, 7)], settings, random.Random(9))
        lovers = [p for p in result if p.role == "lover"]
        self.assertEqual(len(lovers), 2)
        self.assertEqual(lovers[0].lover_id, lovers[1].user_id)
        self.assertEqual(lovers[1].lover_id, lovers[0].user_id)


class MafiaResolutionTests(unittest.TestCase):
    def test_doctor_saves_mafia_target(self):
        ps = players_with_roles("godfather", "doctor", "citizen", "detective", "citizen")
        result = resolve_night(ps, mafia_target=ps[2].user_id, doctor_target=ps[2].user_id)
        self.assertNotIn(ps[2].user_id, result["deaths"])
        self.assertTrue(ps[2].alive)

    def test_mafia_kill_and_lovers_chain_death(self):
        ps = players_with_roles("godfather", "citizen", "lover", "lover", "detective")
        ps[2].lover_id, ps[3].lover_id = ps[3].user_id, ps[2].user_id
        result = resolve_night(ps, mafia_target=ps[2].user_id)
        self.assertIn(ps[2].user_id, result["deaths"])
        self.assertIn(ps[3].user_id, result["deaths"])
        self.assertFalse(ps[3].alive)

    def test_vigilante_dies_from_regret_after_killing_town(self):
        ps = players_with_roles("godfather", "vigilante", "citizen", "doctor", "citizen")
        result = resolve_night(ps, mafia_target=None, vigilante_target=ps[2].user_id)
        self.assertIn(ps[2].user_id, result["deaths"])
        self.assertIn(ps[1].user_id, result["deaths"])
        self.assertIn("vigilante_regret", result["events"])

    def test_trial_tie_acquits_and_execution_awards_jester(self):
        result = tally_trial({1: "execute", 2: "spare", 3: "execute"}, candidate_id=4, living_ids={1, 2, 3, 4})
        self.assertFalse(result["executed"])
        result = tally_trial({1: "execute", 2: "execute", 3: "spare"}, candidate_id=4, living_ids={1, 2, 3, 4})
        self.assertTrue(result["executed"])
        self.assertEqual(result["winner"], "jester")

    def test_victory_conditions(self):
        ps = players_with_roles("citizen", "detective", "doctor")
        self.assertEqual(check_victory(ps), "town")
        ps = players_with_roles("godfather", "framer", "citizen", "citizen")
        self.assertEqual(check_victory(ps), "mafia")
        ps = players_with_roles("godfather", "citizen", "citizen")
        self.assertIsNone(check_victory(ps))


class MafiaRenderingTests(unittest.TestCase):
    def test_setup_and_game_boards_render_valid_pngs(self):
        settings = MafiaSettings()
        setup = render_setup_board(settings, player_count=8, host_name="Host")
        game = render_mafia_board(players_with_roles("godfather", "citizen", "detective"), phase="reveal", headline="كشف الأقنعة")
        self.assertEqual(setup.size, (1200, 850))
        self.assertEqual(game.size, (1200, 800))
        for image in (setup, game):
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            self.assertTrue(buffer.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(len(buffer.getvalue()), 1000)


if __name__ == "__main__":
    unittest.main()
