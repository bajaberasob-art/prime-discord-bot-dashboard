import os
import sqlite3
import tempfile
import unittest

import database
import profile_store_db


class ProfileStoreDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_name = database.DB_NAME
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix=f"prime_profile_store_{os.getpid()}_"
        )
        database.DB_NAME = os.path.join(self.temp_dir.name, "test.db")
        await database.init_db()
        await profile_store_db.ensure_profile_store_schema()

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db_name
        self.temp_dir.cleanup()

    async def test_schema_tables_indexes_and_singleton_defaults(self):
        expected_tables = {
            "profile_store_migrations",
            "profile_store_settings",
            "profile_store_items",
            "user_profile_inventory",
            "user_profile_card_settings",
            "user_profile_equipped_items",
            "profile_store_sessions",
        }
        async with database.connect() as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ) as cursor:
                tables = {row[0] for row in await cursor.fetchall()}
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ) as cursor:
                indexes = {row[0] for row in await cursor.fetchall()}
            async with db.execute(
                """
                SELECT enabled, custom_upload_enabled, custom_upload_price,
                       custom_upload_currency, max_custom_upload_bytes,
                       session_ttl_seconds
                FROM profile_store_settings WHERE settings_id = 1
                """
            ) as cursor:
                settings = await cursor.fetchone()

        self.assertTrue(expected_tables.issubset(tables))
        self.assertTrue({
            "idx_profile_store_items_catalog",
            "idx_profile_inventory_user",
            "idx_profile_store_sessions_expiry",
            "idx_profile_store_sessions_owner",
        }.issubset(indexes))
        self.assertEqual(tuple(settings), (1, 0, 0, "moon", 5 * 1024 * 1024, 900))

    async def test_schema_initialization_is_idempotent(self):
        await profile_store_db.ensure_profile_store_schema()
        await profile_store_db.ensure_profile_store_schema()

        async with database.connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM profile_store_settings"
            ) as cursor:
                settings_count = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT version FROM profile_store_migrations ORDER BY version"
            ) as cursor:
                versions = [row[0] for row in await cursor.fetchall()]

        self.assertEqual(settings_count, 1)
        self.assertEqual(versions, [1, profile_store_db.PROFILE_STORE_SCHEMA_VERSION])

    async def test_catalog_accepts_free_and_paid_items_but_rejects_invalid_values(self):
        async with database.connect() as db:
            await db.execute(
                """
                INSERT INTO profile_store_items
                    (slug, name, item_type, category, rarity, price, currency)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("default-pink", "وردي افتراضي", "card_color", "colors", "common", 0, "moon"),
            )
            await db.execute(
                """
                INSERT INTO profile_store_items
                    (slug, name, item_type, category, rarity, price, currency)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("gold-frame", "إطار ذهبي", "card_template", "templates", "legendary", 200, "gem"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO profile_store_items
                        (slug, name, item_type, price)
                    VALUES ('negative-item', 'Invalid', 'background', -1)
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO profile_store_items
                        (slug, name, item_type, rarity)
                    VALUES ('bad-rarity', 'Invalid', 'background', 'mythic')
                    """
                )
            await db.commit()

            async with db.execute(
                """
                SELECT slug, price, currency FROM profile_store_items
                WHERE slug IN ('default-pink', 'gold-frame')
                ORDER BY slug
                """
            ) as cursor:
                rows = [tuple(row) for row in await cursor.fetchall()]

        self.assertEqual(rows, [
            ("default-pink", 0, "moon"),
            ("gold-frame", 200, "gem"),
        ])

    async def test_default_templates_are_independent_catalog_items_with_varied_dimensions(self):
        import json
        async with database.connect() as db:
            async with db.execute(
                """
                SELECT slug, asset_path, price, metadata_json
                FROM profile_store_items
                WHERE item_type = 'card_template' AND slug LIKE 'profile-%'
                  AND is_active = 1 AND deleted_at IS NULL
                ORDER BY slug
                """
            ) as cursor:
                rows = [tuple(row) for row in await cursor.fetchall()]

        self.assertEqual(len(rows), 7)
        self.assertEqual(len({row[0] for row in rows}), 7)
        self.assertTrue(all(row[1] and row[1].endswith(".png") for row in rows))
        dimensions = set()
        for row in rows:
            metadata = json.loads(row[3])
            dimensions.add((metadata["width"], metadata["height"]))
        self.assertGreaterEqual(len(dimensions), 4)

    async def test_card_settings_and_equipped_items_require_owned_inventory(self):
        async with database.connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO profile_store_items (slug, name, item_type, price)
                VALUES ('background-1', 'Background 1', 'background', 100)
                """
            )
            item_id = cursor.lastrowid
            await db.execute(
                """
                INSERT INTO user_profile_inventory
                    (guild_id, user_id, item_id, price_paid, currency)
                VALUES (99, 1234, ?, 100, 'moon')
                """,
                (item_id,),
            )
            await db.execute(
                """
                INSERT INTO user_profile_card_settings
                    (guild_id, user_id, background_item_id)
                VALUES (99, 1234, ?)
                """,
                (item_id,),
            )
            await db.execute(
                """
                INSERT INTO user_profile_equipped_items
                    (guild_id, user_id, slot_key, item_id)
                VALUES (99, 1234, 'badge_1', ?)
                """,
                (item_id,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO user_profile_card_settings
                        (guild_id, user_id, background_item_id)
                    VALUES (99, 5678, ?)
                    """,
                    (item_id,),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO user_profile_equipped_items
                        (guild_id, user_id, slot_key, item_id)
                    VALUES (99, 1234, 'badge_2', ?)
                    """,
                    (item_id,),
                )
            await db.commit()

            async with db.execute(
                """
                SELECT background_item_id FROM user_profile_card_settings
                WHERE guild_id = 99 AND user_id = 1234
                """
            ) as cursor:
                selected = (await cursor.fetchone())[0]
            async with db.execute(
                """
                SELECT slot_key FROM user_profile_equipped_items
                WHERE guild_id = 99 AND user_id = 1234
                """
            ) as cursor:
                slots = [row[0] for row in await cursor.fetchall()]

        self.assertEqual(selected, item_id)
        self.assertEqual(slots, ["badge_1"])


    async def test_profile_session_is_issued_for_member_with_15_minute_expiry(self):
        from datetime import datetime
        token, expires_at = await profile_store_db.create_profile_store_session(99, 1234)
        digest = profile_store_db.hash_session_token(token)
        self.assertTrue(token)
        self.assertEqual(len(digest), 64)
        async with database.connect() as db:
            async with db.execute(
                """
                SELECT guild_id, user_id, token_hash, created_at, expires_at
                FROM profile_store_sessions WHERE token_hash = ?
                """,
                (digest,),
            ) as cursor:
                row = await cursor.fetchone()
        self.assertEqual((row[0], row[1], row[2]), (99, 1234, digest))
        self.assertEqual(row[4], expires_at)
        created = datetime.fromisoformat(row[3].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        self.assertEqual((expires - created).total_seconds(),
                         profile_store_db.DEFAULT_SESSION_TTL_SECONDS)

    async def test_disabled_store_rejects_new_temporary_sessions(self):
        async with database.connect() as db:
            await db.execute("UPDATE profile_store_settings SET enabled = 0 WHERE settings_id = 1")
            await db.commit()
        with self.assertRaises(profile_store_db.ProfileStoreDisabled):
            await profile_store_db.create_profile_store_session(99, 1234)

    async def test_session_token_helper_returns_digest_and_rejects_empty_input(self):
        digest = profile_store_db.hash_session_token("secret-token")
        self.assertEqual(len(digest), 64)
        self.assertTrue(all(char in "0123456789abcdef" for char in digest))
        self.assertNotEqual(digest, "secret-token")
        with self.assertRaises(ValueError):
            profile_store_db.hash_session_token("")

    async def test_temporary_sessions_store_hashes_and_reject_invalid_expiry(self):
        token_hash = profile_store_db.hash_session_token("temporary-secret")
        created = "2026-10-25T12:00:00Z"
        expires = "2026-10-25T12:15:00Z"
        async with database.connect() as db:
            await db.execute(
                """
                INSERT INTO profile_store_sessions
                    (token_hash, guild_id, user_id, created_at, expires_at)
                VALUES (?, 99, 1234, ?, ?)
                """,
                (token_hash, created, expires),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                await db.execute(
                    """
                    INSERT INTO profile_store_sessions
                        (token_hash, guild_id, user_id, created_at, expires_at)
                    VALUES (?, 99, 1234, ?, ?)
                    """,
                    (profile_store_db.hash_session_token("bad-expiry"), expires, created),
                )
            await db.commit()

            async with db.execute(
                """
                SELECT token_hash, guild_id, user_id, created_at, expires_at,
                       consumed_at, revoked_at
                FROM profile_store_sessions
                """
            ) as cursor:
                row = await cursor.fetchone()
            async with db.execute(
                "PRAGMA table_info(profile_store_sessions)"
            ) as cursor:
                columns = {column[1] for column in await cursor.fetchall()}

        self.assertEqual(row[0], token_hash)
        self.assertNotEqual(row[0], "temporary-secret")
        self.assertEqual((row[1], row[2]), (99, 1234))
        self.assertEqual(row[3:5], (created, expires))
        self.assertIsNone(row[5])
        self.assertIsNone(row[6])
        self.assertIn("token_hash", columns)
        self.assertNotIn("token", columns)


if __name__ == "__main__":
    unittest.main()
