import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import aiosqlite
import database
import profile_store_db
import profile_store_public


class PrimeProfilePublicStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_name = database.DB_NAME
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prime_profile_public_store_")
        database.DB_NAME = os.path.join(self.temp_dir.name, "store.db")
        await database.init_db()
        await profile_store_db.ensure_profile_store_schema()
        self.session = {"guild_id": 99, "user_id": 1234}

    async def asyncTearDown(self):
        database.DB_NAME = self.original_db_name
        self.temp_dir.cleanup()

    async def _add_item(
        self, slug, *, item_type="background", price=20, currency="moon",
        name="Test item", asset_path=None, category="test", subcategory="demo",
    ):
        async with database.connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO profile_store_items
                    (slug, name, description, item_type, category, subcategory,
                     rarity, price, currency, asset_path, is_active, is_featured)
                VALUES (?, ?, ?, ?, ?, ?, 'common', ?, ?, ?, 1, 0)
                """,
                (slug, name, "Item for storefront integration tests", item_type,
                 category, subcategory, price, currency, asset_path),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def test_store_state_reads_existing_prime_wallet_and_owned_catalog(self):
        await self._add_item("test-store-background", asset_path="assets/profile_templates/01_portrait_neon_pink.png")
        request = SimpleNamespace(app={})
        data = await profile_store_public._store_state(request, self.session)

        self.assertEqual(data["brand"], "PRIME")
        self.assertEqual(data["wallet"]["coins"], 100)
        self.assertEqual(data["wallet"]["gems"], 0)
        self.assertEqual(data["guild"]["id"], "99")
        self.assertEqual(data["user"]["id"], "1234")
        self.assertTrue(any(item["slug"] == "profile-neon-wide" for item in data["items"]))
        self.assertTrue(any(item["slug"] == "test-store-background" for item in data["items"]))
        self.assertTrue(all("moon" not in item.get("currency_label", "") for item in data["items"]))

    async def test_coin_purchase_is_atomic_and_duplicate_purchase_does_not_charge_twice(self):
        item_id = await self._add_item("prime-coins-item", price=35, currency="moon")
        result = await profile_store_public._purchase_item(self.session, item_id)
        self.assertEqual(result["balance"], 65)
        self.assertEqual(result["currency"], "moon")

        with self.assertRaises(profile_store_public.StoreActionError) as caught:
            await profile_store_public._purchase_item(self.session, item_id)
        self.assertEqual(caught.exception.code, "already_owned")

        async with database.connect() as db:
            async with db.execute(
                "SELECT balance FROM users WHERE guild_id = 99 AND user_id = 1234"
            ) as cursor:
                wallet = await cursor.fetchone()
            async with db.execute(
                """
                SELECT COUNT(*) FROM user_profile_inventory
                WHERE guild_id = 99 AND user_id = 1234 AND item_id = ?
                """,
                (item_id,),
            ) as cursor:
                owned_count = (await cursor.fetchone())[0]
            async with db.execute(
                """
                SELECT COUNT(*) FROM profile_store_transactions
                WHERE guild_id = 99 AND user_id = 1234 AND item_id = ?
                  AND transaction_type = 'purchase'
                """,
                (item_id,),
            ) as cursor:
                transaction_count = (await cursor.fetchone())[0]
        self.assertEqual(wallet["balance"], 65)
        self.assertEqual(owned_count, 1)
        self.assertEqual(transaction_count, 1)

    async def test_gem_purchase_uses_separate_wallet_and_ledger(self):
        item_id = await self._add_item("prime-gem-item", price=30, currency="gem")
        async with database.connect() as db:
            await db.execute(
                """
                INSERT INTO user_profile_gem_wallets (guild_id, user_id, balance)
                VALUES (99, 1234, 55)
                """
            )
            await db.commit()

        result = await profile_store_public._purchase_item(self.session, item_id)
        self.assertEqual(result["balance"], 25)
        async with database.connect() as db:
            async with db.execute(
                "SELECT balance FROM user_profile_gem_wallets WHERE guild_id = 99 AND user_id = 1234"
            ) as cursor:
                wallet = await cursor.fetchone()
            async with db.execute(
                """
                SELECT currency, delta, balance_after FROM profile_store_transactions
                WHERE guild_id = 99 AND user_id = 1234 AND item_id = ?
                """,
                (item_id,),
            ) as cursor:
                ledger = await cursor.fetchone()
        self.assertEqual(wallet["balance"], 25)
        self.assertEqual(tuple(ledger), ("gem", -30, 25))

    async def test_insufficient_balance_does_not_create_inventory_or_ledger_entry(self):
        item_id = await self._add_item("too-expensive-prime-item", price=500, currency="moon")
        with self.assertRaises(profile_store_public.StoreActionError) as caught:
            await profile_store_public._purchase_item(self.session, item_id)
        self.assertEqual(caught.exception.code, "insufficient_coins")
        async with database.connect() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM user_profile_inventory WHERE guild_id = 99 AND user_id = 1234 AND item_id = ?",
                (item_id,),
            ) as cursor:
                inventory_count = (await cursor.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM profile_store_transactions WHERE guild_id = 99 AND user_id = 1234 AND item_id = ?",
                (item_id,),
            ) as cursor:
                ledger_count = (await cursor.fetchone())[0]
        self.assertEqual(inventory_count, 0)
        self.assertEqual(ledger_count, 0)

    async def test_item_must_be_owned_before_equip_and_equipped_state_is_persisted(self):
        item_id = await self._add_item("equip-profile-template", item_type="card_template", price=0)
        with self.assertRaises(profile_store_public.StoreActionError) as caught:
            await profile_store_public._equip_item(self.session, item_id)
        self.assertEqual(caught.exception.code, "item_not_owned")

        await profile_store_public._purchase_item(self.session, item_id)
        result = await profile_store_public._equip_item(self.session, item_id)
        self.assertEqual(result["slot_key"], "template_item_id")
        data = await profile_store_public._store_state(SimpleNamespace(app={}), self.session)
        item = next(item for item in data["items"] if item["item_id"] == item_id)
        self.assertTrue(item["owned"])
        self.assertTrue(item["equipped"])

        await profile_store_public._unequip_item(self.session, item_id)
        data = await profile_store_public._store_state(SimpleNamespace(app={}), self.session)
        item = next(item for item in data["items"] if item["item_id"] == item_id)
        self.assertTrue(item["owned"])
        self.assertFalse(item["equipped"])

    async def test_one_time_discord_link_token_cannot_be_replayed(self):
        token, expires_at = await profile_store_db.create_profile_store_session(99, 1234)
        exchange = await profile_store_public._exchange_link_token(SimpleNamespace(), token)
        self.assertIsNotNone(exchange)
        cookie_token, csrf_token, max_age = exchange
        self.assertTrue(cookie_token)
        self.assertTrue(csrf_token)
        self.assertGreater(max_age, 0)
        self.assertLessEqual(max_age, profile_store_db.DEFAULT_SESSION_TTL_SECONDS)

        self.assertIsNone(await profile_store_public._exchange_link_token(SimpleNamespace(), token))
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT consumed_at FROM profile_store_sessions WHERE token_hash = ?",
                (profile_store_db.hash_session_token(token),),
            ) as cursor:
                source = await cursor.fetchone()
            async with db.execute(
                "SELECT guild_id, user_id, csrf_hash FROM profile_store_web_sessions WHERE session_hash = ?",
                (profile_store_db.hash_session_token(cookie_token),),
            ) as cursor:
                web_session = await cursor.fetchone()
        self.assertTrue(source["consumed_at"])
        self.assertEqual((web_session["guild_id"], web_session["user_id"]), (99, 1234))
        self.assertEqual(web_session["csrf_hash"], profile_store_db.hash_session_token(csrf_token))

    def test_asset_path_blocks_traversal_and_accepts_approved_png(self):
        approved = profile_store_public._resolve_asset_path(
            "assets/profile_templates/01_portrait_neon_pink.png"
        )
        self.assertIsNotNone(approved)
        self.assertTrue(approved.is_file())
        self.assertIsNone(profile_store_public._resolve_asset_path("../../web_server.py"))
        self.assertIsNone(profile_store_public._resolve_asset_path("/etc/passwd"))
        self.assertIsNone(profile_store_public._resolve_asset_path("assets/profile_templates/../profile_card_generator.py"))

    def test_write_requests_require_matching_origin(self):
        good = SimpleNamespace(
            headers={"Origin": "https://prime-panel.discloud.app"},
            host="prime-panel.discloud.app",
            scheme="https",
        )
        bad = SimpleNamespace(
            headers={"Origin": "https://attacker.example"},
            host="prime-panel.discloud.app",
            scheme="https",
        )
        missing = SimpleNamespace(headers={}, host="prime-panel.discloud.app", scheme="https")
        self.assertTrue(profile_store_public._origin_matches(good))
        self.assertFalse(profile_store_public._origin_matches(bad))
        self.assertFalse(profile_store_public._origin_matches(missing))


if __name__ == "__main__":
    unittest.main()
