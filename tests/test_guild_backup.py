import json
import os
import sqlite3
import time
import unittest
from unittest.mock import Mock

from aiohttp.streams import StreamReader
from aiohttp.test_utils import make_mocked_request

import database
import guild_backup
import web_server as ws
from tests.dashboard_harness import FakeBot, FakeGuild


GUILD_ID = int(FakeGuild.id)
OTHER_GUILD_ID = GUILD_ID + 1


def _seed_database():
    with sqlite3.connect(database.DB_NAME) as conn:
        conn.executemany(
            "INSERT INTO guild_settings (guild_id, prefix) VALUES (?, ?)",
            [(GUILD_ID, "!"), (OTHER_GUILD_ID, "?")],
        )
        conn.executemany(
            "INSERT INTO users (user_id, guild_id, balance, bank) VALUES (?, ?, ?, ?)",
            [(51, GUILD_ID, 125, 300), (52, OTHER_GUILD_ID, 250, 500)],
        )
        conn.executemany(
            "INSERT INTO user_levels (guild_id, user_id, text_xp, text_level) "
            "VALUES (?, ?, ?, ?)",
            [(GUILD_ID, 51, 1000, 4), (OTHER_GUILD_ID, 52, 2400, 7)],
        )
        conn.executemany(
            "INSERT INTO giveaways "
            "(id, guild_id, channel_id, message_id, prize, ends_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (2001, GUILD_ID, 11, 101, "Guild one giveaway", "2026-12-01", 51),
                (2002, OTHER_GUILD_ID, 22, 202, "Guild two giveaway", "2026-12-02", 52),
            ],
        )
        conn.executemany(
            "INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
            [(2001, 51), (2002, 52)],
        )
        conn.executemany(
            "INSERT INTO tournaments "
            "(id, guild_id, channel_id, title, max_players, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (4001, GUILD_ID, 11, "Guild one tournament", 16, 51),
                (4002, OTHER_GUILD_ID, 22, "Guild two tournament", 32, 52),
            ],
        )
        conn.executemany(
            "INSERT INTO tournament_entries (tournament_id, user_id) VALUES (?, ?)",
            [(4001, 51), (4002, 52)],
        )
        conn.executemany(
            "INSERT INTO scrim_configs (id, guild_id, channel_id, title, game_type) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (3001, GUILD_ID, 11, "Guild one scrim", "FPS"),
                (3002, OTHER_GUILD_ID, 22, "Guild two scrim", "FPS"),
            ],
        )
        conn.executemany(
            "INSERT INTO scrim_registrations "
            "(id, scrim_id, slot_number, team_name, leader_id, members_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (3101, 3001, 1, "Team one", 51, "[51]"),
                (3102, 3002, 1, "Team two", 52, "[52]"),
            ],
        )
        conn.executemany(
            "INSERT INTO announcement_line_images "
            "(guild_id, image_id, mime, width, height, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (GUILD_ID, "image-one", "image/png", 1, 1, b"\x89PNG-one", 1.0),
                (OTHER_GUILD_ID, "image-two", "image/png", 1, 1, b"\x89PNG-two", 2.0),
            ],
        )
        conn.execute(
            "INSERT INTO user_dashboard_preferences (user_id, theme_json) VALUES (?, ?)",
            ("51", '{"theme":"private"}'),
        )


def _read_rows(table, where, values):
    with sqlite3.connect(database.DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                f"SELECT * FROM {table} WHERE {where}",
                values,
            ).fetchall()
        ]


def _request(method, path, body=None, csrf=True, session=True):
    headers = {"Host": "dash.test", "Origin": "https://dash.test"}
    if session:
        headers["Cookie"] = "bot_session=backup-session"
    if csrf:
        headers["X-CSRF-Token"] = "backup-csrf"
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    req = make_mocked_request(method, path, headers=headers)
    req.match_info["guild_id"] = str(GUILD_ID)
    if body is not None:
        reader = StreamReader(Mock(), 2**16)
        reader.feed_data(body)
        reader.feed_eof()
        req._payload = reader
    return req


async def _call(handler, req):
    try:
        return await handler(req)
    except ws.web.HTTPException as error:
        return error


class GuildBackupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_db = database.DB_NAME
        database.DB_NAME = "/tmp/test_guild_backup.db"
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(database.DB_NAME + suffix)
            except FileNotFoundError:
                pass
        await database.init_db()
        _seed_database()
        database.invalidate_guild_settings()
        ws.bot_ref = FakeBot()
        ws.SESSIONS.clear()
        ws.RATE_BUCKETS.clear()
        ws.GRANT_CACHE.clear()
        ws.SESSIONS["backup-session"] = {
            "id": "10",
            "username": "backup-admin",
            "avatar": "",
            "csrf": "backup-csrf",
            "guilds": [{"id": str(GUILD_ID)}],
            "expires_at": time.time() + 600,
        }

    async def asyncTearDown(self):
        database.DB_NAME = self.previous_db
        ws.SESSIONS.clear()
        ws.RATE_BUCKETS.clear()
        ws.GRANT_CACHE.clear()

    def test_export_contains_only_guild_data_and_encodes_binary_and_child_rows(self):
        body, summary = guild_backup.create_guild_backup(GUILD_ID)
        backup = json.loads(body)
        tables = backup["tables"]

        self.assertEqual(backup["guild_id"], str(GUILD_ID))
        self.assertIn("user_levels", tables)
        self.assertIn("giveaway_entries", tables)
        self.assertIn("scrim_registrations", tables)
        self.assertIn("tournament_entries", tables)
        self.assertNotIn("user_dashboard_preferences", tables)
        self.assertNotIn("streak_stages", tables)

        def rows(table):
            columns = tables[table]["columns"]
            return [dict(zip(columns, row)) for row in tables[table]["rows"]]

        self.assertEqual([row["guild_id"] for row in rows("users")], [GUILD_ID])
        self.assertEqual([row["text_level"] for row in rows("user_levels")], [4])
        self.assertEqual([row["user_id"] for row in rows("giveaway_entries")], [51])
        self.assertEqual([row["user_id"] for row in rows("tournament_entries")], [51])
        self.assertEqual([row["team_name"] for row in rows("scrim_registrations")], ["Team one"])
        blob = rows("announcement_line_images")[0]["payload"]
        self.assertIn("$prime_backup_binary", blob)
        self.assertEqual(summary["guild_id"], str(GUILD_ID))
        self.assertGreater(summary["table_count"], 50)
        self.assertGreater(summary["row_count"], 0)
        self.assertNotIn("Guild two giveaway", body.decode("utf-8"))
        self.assertNotIn("image-two", body.decode("utf-8"))

    async def test_restore_replaces_this_guild_and_preserves_other_guild_and_global_data(self):
        original, _ = guild_backup.create_guild_backup(GUILD_ID)
        await database.get_guild_settings(GUILD_ID)
        with sqlite3.connect(database.DB_NAME) as conn:
            conn.execute("UPDATE guild_settings SET prefix = '$' WHERE guild_id = ?", (GUILD_ID,))
            conn.execute("UPDATE users SET balance = 999 WHERE guild_id = ?", (GUILD_ID,))
            conn.execute("UPDATE user_levels SET text_xp = 9999 WHERE guild_id = ?", (GUILD_ID,))
            conn.execute("DELETE FROM giveaway_entries WHERE giveaway_id = 2001")
            conn.execute("INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (2001, 999)")
            conn.execute(
                "UPDATE announcement_line_images SET payload = ? WHERE guild_id = ?",
                (b"changed", GUILD_ID),
            )
            conn.execute(
                "UPDATE user_dashboard_preferences SET theme_json = ? WHERE user_id = '51'",
                ('{"theme":"new-private"}',),
            )

        summary = guild_backup.restore_guild_backup(GUILD_ID, original)
        self.assertGreater(summary["row_count"], 0)
        self.assertEqual((await database.get_guild_settings(GUILD_ID))["settings"]["prefix"], "!")
        self.assertEqual(_read_rows("users", "guild_id = ?", (GUILD_ID,))[0]["balance"], 125)
        self.assertEqual(_read_rows("users", "guild_id = ?", (OTHER_GUILD_ID,))[0]["balance"], 250)
        self.assertEqual(_read_rows("user_levels", "guild_id = ?", (GUILD_ID,))[0]["text_xp"], 1000)
        self.assertEqual(_read_rows("user_levels", "guild_id = ?", (OTHER_GUILD_ID,))[0]["text_xp"], 2400)
        self.assertEqual(_read_rows("giveaway_entries", "giveaway_id = ?", (2001,))[0]["user_id"], 51)
        self.assertEqual(_read_rows("giveaway_entries", "giveaway_id = ?", (2002,))[0]["user_id"], 52)
        self.assertEqual(_read_rows("scrim_registrations", "scrim_id = ?", (3001,))[0]["team_name"], "Team one")
        self.assertEqual(_read_rows("scrim_registrations", "scrim_id = ?", (3002,))[0]["team_name"], "Team two")
        image = _read_rows("announcement_line_images", "guild_id = ?", (GUILD_ID,))[0]
        other_image = _read_rows("announcement_line_images", "guild_id = ?", (OTHER_GUILD_ID,))[0]
        self.assertEqual(image["payload"], b"\x89PNG-one")
        self.assertEqual(other_image["payload"], b"\x89PNG-two")
        self.assertEqual(
            _read_rows("user_dashboard_preferences", "user_id = ?", ("51",))[0]["theme_json"],
            '{"theme":"new-private"}',
        )

    def test_mismatched_or_conflicting_backup_does_not_partially_change_database(self):
        original, _ = guild_backup.create_guild_backup(GUILD_ID)
        with self.assertRaises(guild_backup.GuildBackupError):
            guild_backup.restore_guild_backup(OTHER_GUILD_ID, original)
        with self.assertRaises(guild_backup.GuildBackupError):
            guild_backup.restore_guild_backup(GUILD_ID, b"not a JSON backup")

        tampered = json.loads(original)
        user_columns = tampered["tables"]["users"]["columns"]
        guild_index = user_columns.index("guild_id")
        tampered["tables"]["users"]["rows"][0][guild_index] = OTHER_GUILD_ID
        with self.assertRaises(guild_backup.GuildBackupError):
            guild_backup.restore_guild_backup(
                GUILD_ID,
                json.dumps(tampered).encode("utf-8"),
            )

        with sqlite3.connect(database.DB_NAME) as conn:
            conn.execute("UPDATE guild_settings SET prefix = '$' WHERE guild_id = ?", (GUILD_ID,))
            conn.execute("DELETE FROM giveaways WHERE id = 2001")
            conn.execute(
                "INSERT INTO giveaways "
                "(id, guild_id, channel_id, prize, ends_at, created_by) "
                "VALUES (2001, ?, 99, 'Other server conflict', '2026-12-03', 99)",
                (OTHER_GUILD_ID,),
            )
        with self.assertRaises(guild_backup.GuildBackupError):
            guild_backup.restore_guild_backup(GUILD_ID, original)

        self.assertEqual(_read_rows("guild_settings", "guild_id = ?", (GUILD_ID,))[0]["prefix"], "$")
        self.assertEqual(
            _read_rows("giveaways", "id = ?", (2001,))[0]["prize"],
            "Other server conflict",
        )
        self.assertEqual(_read_rows("users", "guild_id = ?", (GUILD_ID,))[0]["balance"], 125)

    async def test_download_and_restore_routes_require_session_and_csrf(self):
        download_route = next(
            route.handler
            for route in ws.routes._items
            if route.method == "GET" and route.path == "/api/guild/{guild_id}/backup"
        )
        restore_route = next(
            route.handler
            for route in ws.routes._items
            if route.method == "POST"
            and route.path == "/api/guild/{guild_id}/backup/restore"
        )

        download = await _call(
            download_route,
            _request("GET", f"/api/guild/{GUILD_ID}/backup"),
        )
        self.assertEqual(download.status, 200)
        self.assertIn("attachment;", download.headers["Content-Disposition"])
        backup_body = download.body

        denied = await _call(
            restore_route,
            _request(
                "POST",
                f"/api/guild/{GUILD_ID}/backup/restore",
                body=backup_body,
                csrf=False,
            ),
        )
        self.assertEqual(denied.status, 403)

        with sqlite3.connect(database.DB_NAME) as conn:
            conn.execute("UPDATE guild_settings SET prefix = '$' WHERE guild_id = ?", (GUILD_ID,))
        restored = await _call(
            restore_route,
            _request(
                "POST",
                f"/api/guild/{GUILD_ID}/backup/restore",
                body=backup_body,
            ),
        )
        self.assertEqual(restored.status, 200, restored.text)
        self.assertTrue(json.loads(restored.text)["ok"])

        no_session = await _call(
            download_route,
            _request("GET", f"/api/guild/{GUILD_ID}/backup", session=False),
        )
        self.assertEqual(no_session.status, 401)
