import asyncio
import io
import os
import tempfile
import unittest

import aiosqlite
from PIL import Image

import database
from cogs.games import GameBoardView
from cogs.gaming import _members, _scrim_embed
from cogs.card_generator import generate_xo_game_card
from prime_games.engine import (
    apply_action,
    attach_message,
    create_session,
    ensure_game_schema,
    get_session,
    list_active_sessions,
)


class PrimeGamesEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db = database.DB_NAME
        self.directory = tempfile.TemporaryDirectory()
        database.DB_NAME = os.path.join(self.directory.name, "prime-games.db")
        await database.init_db()
        await ensure_game_schema()

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db
        self.directory.cleanup()

    async def test_xo_moves_finish_once_and_reward_existing_economy(self):
        session = await create_session(
            guild_id=700,
            channel_id=300,
            game_key="xo",
            players=[11, 22],
        )
        self.assertEqual(session["current_player_id"], 11)
        self.assertEqual(session["state"]["board"], [None] * 9)
        self.assertEqual(await list_active_sessions(700), [])
        self.assertTrue(await attach_message(session["id"], 8800))
        self.assertFalse(await attach_message(session["id"], 8801))
        self.assertEqual(len(await list_active_sessions(700)), 1)

        moves = [
            (11, 0),
            (22, 3),
            (11, 1),
            (22, 4),
            (11, 2),
        ]
        final = None
        for player_id, square in moves:
            final = await apply_action(session["id"], player_id, {"index": square})
            self.assertTrue(final["ok"], final)
        self.assertTrue(final["finished"])
        self.assertEqual(final["winner_id"], 11)
        self.assertEqual(final["reward"], 25)
        self.assertEqual(final["session"]["status"], "finished")
        self.assertEqual(final["session"]["state"]["board"][:3], ["X", "X", "X"])

        repeated = await apply_action(session["id"], 11, {"index": 8})
        self.assertFalse(repeated["ok"])
        self.assertEqual(repeated["reason"], "game_finished")

        account = await database.get_or_create_user(11, 700)
        self.assertEqual(account["balance"], 125)
        async with database.connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM prime_game_reward_claims WHERE session_id = ?",
                (session["id"],),
            ) as cur:
                self.assertEqual((await cur.fetchone())[0], 1)
            async with db.execute(
                "SELECT amount, kind FROM economy_transactions WHERE guild_id = ? AND to_user_id = ?",
                (700, 11),
            ) as cur:
                rows = await cur.fetchall()
                self.assertEqual([(int(row[0]), row[1]) for row in rows], [(25, "game_reward")])

    async def test_simultaneous_moves_cannot_advance_the_same_turn_twice(self):
        session = await create_session(
            guild_id=701,
            channel_id=301,
            game_key="xo",
            players=[31, 32],
        )
        await attach_message(session["id"], 8810)
        first, second = await asyncio.gather(
            apply_action(session["id"], 31, {"index": 0}),
            apply_action(session["id"], 31, {"index": 1}),
        )
        self.assertEqual(sum(bool(item["ok"]) for item in (first, second)), 1)
        reloaded = await get_session(session["id"])
        self.assertEqual(sum(value is not None for value in reloaded["state"]["board"]), 1)
        self.assertEqual(reloaded["current_player_id"], 32)

    async def test_scrim_teams_require_complete_unique_rosters_and_checkin_is_idempotent(self):
        scrim = await database.create_scrim_config(
            702, 302, "Friday scrim", "Valorant", 2, 3
        )
        first = await database.reserve_scrim_slot(
            scrim["id"], "PRIME Alpha", 41, [41, 42]
        )
        self.assertIsNotNone(first)
        self.assertEqual(first["slot_number"], 1)

        # A roster must exactly match team_size, include its leader, and not
        # reuse members already assigned to another team.
        self.assertIsNone(await database.reserve_scrim_slot(scrim["id"], "Incomplete", 43, [43]))
        self.assertIsNone(await database.reserve_scrim_slot(scrim["id"], "Overlapping", 42, [42, 44]))
        self.assertIsNone(await database.reserve_scrim_slot(scrim["id"], "Duplicate", 43, [43, 43]))
        second = await database.reserve_scrim_slot(
            scrim["id"], "PRIME Beta", 43, [43, 44]
        )
        self.assertIsNotNone(second)
        self.assertEqual(second["slot_number"], 2)

        self.assertTrue(await database.set_scrim_checkin_all(scrim["id"], True))
        self.assertTrue(await database.set_scrim_checkin_all(scrim["id"], True))
        live = await database.get_active_scrims(702)
        self.assertTrue(all(item["checked_in"] for item in live[0]["registrations"]))
        self.assertEqual(live[0]["occupied_slots"], 2)

        self.assertTrue(await database.close_scrim(scrim["id"]))
        self.assertFalse(await database.set_scrim_checkin_all(scrim["id"], True))
        archived = await database.get_scrims(702)
        self.assertEqual(len(archived), 1)
        self.assertFalse(archived[0]["is_active"])
        self.assertEqual(archived[0]["occupied_slots"], 2)

    async def test_tournament_bracket_advances_winners_and_rejects_duplicate_results(self):
        tournament_id = await database.create_tournament(
            703, 303, "Knockout", 3, 99
        )
        for player_id in (53, 51, 52):
            added, _, _ = await database.add_tournament_entry(tournament_id, player_id)
            self.assertTrue(added)

        expected = await database.get_tournament_entries(tournament_id)
        started = await database.start_tournament_with_bracket(
            tournament_id,
            expected,
            [51, 52, 53],
        )
        self.assertIsNotNone(started)
        round_one = [m for m in started["matches"] if int(m["round_number"]) == 1]
        self.assertEqual(len(round_one), 2)
        bye = next(m for m in round_one if m["player_two_id"] is None)
        played = next(m for m in round_one if m["player_two_id"] is not None)
        self.assertEqual(bye["status"], "completed")
        self.assertEqual(bye["winner_id"], bye["player_one_id"])
        self.assertEqual(played["status"], "scheduled")

        first_winner = int(played["player_one_id"])
        result = await database.record_tournament_bracket_result(
            703, tournament_id, 1, int(played["match_number"]),
            first_winner, "3-1", 99,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["round_completed"])
        self.assertFalse(result["tournament_completed"])
        self.assertIsNone(await database.record_tournament_bracket_result(
            703, tournament_id, 1, int(played["match_number"]),
            first_winner, "3-1", 99,
        ))

        matches = await database.get_tournament_bracket(703, tournament_id)
        final = next(m for m in matches if int(m["round_number"]) == 2)
        self.assertEqual(final["status"], "scheduled")
        self.assertEqual(
            {int(final["player_one_id"]), int(final["player_two_id"])},
            {first_winner, int(bye["winner_id"])},
        )
        champion = int(final["player_two_id"])
        completed = await database.record_tournament_bracket_result(
            703, tournament_id, 2, int(final["match_number"]), champion, "2-0", 99,
        )
        self.assertTrue(completed["tournament_completed"])
        self.assertIsNone(await database.record_tournament_bracket_result(
            703, tournament_id, 2, int(final["match_number"]), champion, "2-0", 99,
        ))
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT status FROM tournaments WHERE id = ?", (tournament_id,)
            ) as cur:
                self.assertEqual((await cur.fetchone())["status"], "completed")

    async def test_team_score_is_saved_and_match_reference_is_idempotent(self):
        self.assertTrue(await database.record_tournament_score(
            704, "Alpha", "Beta", score="3-2", match_ref="final-2026", recorded_by=88
        ))
        self.assertFalse(await database.record_tournament_score(
            704, "Alpha", "Beta", score="3-2", match_ref="final-2026", recorded_by=88
        ))
        scores = await database.get_tournament_scores(704)
        self.assertEqual(
            [(row["team_name"], row["points"], row["wins"], row["losses"]) for row in scores],
            [("Alpha", 3, 1, 0), ("Beta", 0, 0, 1)],
        )
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT score, recorded_by FROM tournament_score_results WHERE guild_id = ? AND match_ref = ?",
                (704, "final-2026"),
            ) as cur:
                result = await cur.fetchone()
        self.assertEqual(result["score"], "3-2")
        self.assertEqual(result["recorded_by"], 88)


    async def test_persistent_xo_view_uses_stable_unique_component_ids(self):
        session = await create_session(
            guild_id=705,
            channel_id=305,
            game_key="xo",
            players=[61, 62],
        )
        view = GameBoardView(session)
        self.assertIsNone(view.timeout)
        self.assertEqual(len(view.children), 9)
        self.assertEqual(
            [item.custom_id for item in view.children],
            [f"primegame:xo:{session['id']}:{index}" for index in range(9)],
        )
        next_state = {**session, "state": {"board": ["X"] + [None] * 8}}
        restored = GameBoardView(next_state)
        self.assertTrue(restored.children[0].disabled)
        self.assertFalse(restored.children[1].disabled)

    async def test_scrim_roster_parser_rejects_invalid_and_repeated_members(self):
        self.assertEqual(_members("22, 33", 11), [11, 22, 33])
        with self.assertRaises(ValueError):
            _members("22, not-an-id", 11)
        with self.assertRaises(ValueError):
            _members("22, 22", 11)
        with self.assertRaises(ValueError):
            _members("11", 11)

    async def test_scrim_embed_stays_inside_discord_description_limit(self):
        registrations = [
            {
                "slot_number": index,
                "team_name": "T" * 100,
                "leader_id": 100000 + index,
                "checked_in": index % 2 == 0,
            }
            for index in range(1, 129)
        ]
        embed = await _scrim_embed({
            "id": 90,
            "title": "Tournament scrim",
            "game_type": "Valorant",
            "team_size": 5,
            "max_slots": 128,
            "occupied_slots": 128,
            "registrations": registrations,
        })
        self.assertLessEqual(len(embed.description or ""), 4096)
        self.assertIn("إخفاء", embed.description or "")

    async def test_xo_card_uses_existing_shared_image_renderer(self):
        image_data = await generate_xo_game_card(
            "PRIME Alpha",
            "PRIME Beta",
            ["X", "O", None, None, "X", None, "O", None, None],
            "PRIME Alpha",
            game_id=15,
        )
        self.assertIsInstance(image_data, io.BytesIO)
        self.assertTrue(image_data.getvalue().startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(image_data) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (960, 560))
            image.verify()

    async def test_game_reward_respects_existing_wallet_daily_budget(self):
        async with database.connect() as db:
            await db.executemany(
                """
                INSERT INTO prime_game_reward_claims
                    (session_id, guild_id, user_id, amount)
                VALUES (?, ?, ?, ?)
                """,
                [(91001, 706, 71, 120), (91002, 706, 71, 125)],
            )
            await db.commit()

        session = await create_session(
            guild_id=706,
            channel_id=306,
            game_key="xo",
            players=[71, 72],
        )
        await attach_message(session["id"], 8860)
        final = None
        for player_id, square in [(71, 0), (72, 3), (71, 1), (72, 4), (71, 2)]:
            final = await apply_action(session["id"], player_id, {"index": square})
        self.assertTrue(final["ok"])
        self.assertEqual(final["reward"], 5)
        self.assertTrue(final["reward_capped"])
        account = await database.get_or_create_user(71, 706)
        self.assertEqual(account["balance"], 105)

    async def test_team_score_rejects_a_score_where_selected_winner_lost(self):
        with self.assertRaisesRegex(ValueError, "الفائز"):
            await database.record_tournament_score(
                707, "Alpha", "Beta", score="2-3", match_ref="wrong-winner", recorded_by=88
            )

if __name__ == "__main__":
    unittest.main()
