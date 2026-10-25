import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import CookieJar, web
from aiohttp.test_utils import TestClient, TestServer

import aiosqlite
import database
import profile_store_admin
import profile_store_db
import profile_store_public


class PrimeProfilePublicStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_name = database.DB_NAME
        self.original_routes_registered = profile_store_public._registered
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prime_profile_public_store_")
        database.DB_NAME = os.path.join(self.temp_dir.name, "store.db")
        await database.init_db()
        await profile_store_db.ensure_profile_store_schema()
        self.session = {"guild_id": 99, "user_id": 1234}

    async def asyncTearDown(self):
        profile_store_public._registered = self.original_routes_registered
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

    async def test_admin_gem_grant_is_scoped_and_audited(self):
        original_registered = profile_store_admin._registered
        profile_store_admin._registered = False

        async def authorize(_request, *, write=False, management_tier=None):
            return {"_local_dev": True, "id": 777}, SimpleNamespace(id=99)

        async def read_json_body(request):
            return await request.json()

        routes = web.RouteTableDef()
        profile_store_admin.register_profile_store_admin_routes(routes, authorize, read_json_body)
        app = web.Application()
        app.add_routes(routes)
        client = TestClient(TestServer(app))
        try:
            await client.start_server()
            granted = await client.post(
                "/api/guild/99/profile-store/users/1234/gems",
                json={"delta": 60, "reason": "launch_reward"},
            )
            self.assertEqual(granted.status, 200, await granted.text())
            self.assertEqual((await granted.json())["balance"], 60)

            async with database.connect(aiosqlite.Row) as db:
                async with db.execute(
                    "SELECT balance FROM user_profile_gem_wallets WHERE guild_id = 99 AND user_id = 1234"
                ) as cursor:
                    wallet = await cursor.fetchone()
                async with db.execute(
                    """
                    SELECT actor_user_id, target_user_id, action, delta, balance_after, reason
                    FROM profile_store_admin_audit
                    WHERE guild_id = 99 AND target_user_id = 1234
                    ORDER BY audit_id DESC LIMIT 1
                    """
                ) as cursor:
                    audit = await cursor.fetchone()
            self.assertEqual(wallet["balance"], 60)
            self.assertEqual(tuple(audit), (777, 1234, "gem_grant", 60, 60, "launch_reward"))

            wrong_scope = await client.post(
                "/api/guild/100/profile-store/users/1234/gems",
                json={"delta": 20, "reason": "wrong_scope"},
            )
            self.assertEqual(wrong_scope.status, 403)

            overdraw = await client.post(
                "/api/guild/99/profile-store/users/1234/gems",
                json={"delta": -100, "reason": "invalid_overdraw"},
            )
            self.assertEqual(overdraw.status, 409)
            async with database.connect(aiosqlite.Row) as db:
                async with db.execute(
                    "SELECT balance FROM user_profile_gem_wallets WHERE guild_id = 99 AND user_id = 1234"
                ) as cursor:
                    wallet = await cursor.fetchone()
            self.assertEqual(wallet["balance"], 60)
        finally:
            await client.close()
            profile_store_admin._registered = original_registered

    async def test_http_store_session_csrf_and_free_purchase_flow(self):
        profile_store_public._registered = False
        routes = web.RouteTableDef()
        profile_store_public.register_profile_store_public_routes(routes)
        app = web.Application()
        app["bot"] = None
        app.add_routes(routes)
        client = TestClient(TestServer(app), cookie_jar=CookieJar(unsafe=True))
        try:
            await client.start_server()

            page = await client.get("/profile-store")
            self.assertEqual(page.status, 200)
            self.assertIn("PRIME", await page.text())
            self.assertEqual(page.headers.get("Referrer-Policy"), "no-referrer")
            self.assertEqual(page.headers.get("X-Frame-Options"), "DENY")

            anonymous = await client.get("/api/profile-store/state")
            self.assertEqual(anonymous.status, 401)
            self.assertEqual((await anonymous.json())["error"], "store_session_expired")

            token, _expires_at = await profile_store_db.create_profile_store_session(99, 1234)
            exchange = await client.get(
                "/profile-store", params={"token": token}, allow_redirects=False,
            )
            self.assertEqual(exchange.status, 302)
            self.assertEqual(exchange.headers.get("Location"), "/profile-store")
            self.assertIn("prime_profile_store", exchange.headers.getall("Set-Cookie")[0])

            state_response = await client.get("/api/profile-store/state")
            self.assertEqual(state_response.status, 200)
            data = await state_response.json()
            self.assertEqual(data["user"]["id"], "1234")
            self.assertGreaterEqual(data["wallet"]["coins"], 0)

            async with database.connect(aiosqlite.Row) as db:
                async with db.execute(
                    "SELECT item_id FROM profile_store_items WHERE slug = 'profile-neon-wide' AND is_active = 1"
                ) as cursor:
                    item = await cursor.fetchone()
            self.assertIsNotNone(item)
            item_id = int(item["item_id"])

            preview_without_bot = await client.get(f"/api/profile-store/preview/{item_id}")
            self.assertEqual(preview_without_bot.status, 503)
            self.assertEqual((await preview_without_bot.json())["error"], "bot_unavailable")

            rejected = await client.post(
                "/api/profile-store/purchase", json={"item_id": item_id},
            )
            self.assertEqual(rejected.status, 403)
            self.assertEqual((await rejected.json())["error"], "csrf")

            csrf_cookie = client.session.cookie_jar.filter_cookies(client.make_url("/"))["prime_profile_csrf"].value
            origin = str(client.make_url("/")).rstrip("/")
            wrong_origin = await client.post(
                "/api/profile-store/purchase",
                json={"item_id": item_id},
                headers={"Origin": "https://attacker.example", "X-CSRF-Token": csrf_cookie},
            )
            self.assertEqual(wrong_origin.status, 403)
            self.assertEqual((await wrong_origin.json())["error"], "csrf")

            oversized = await client.post(
                "/api/profile-store/purchase",
                data="x" * (profile_store_public.MAX_REQUEST_BODY + 1),
                headers={
                    "Content-Type": "application/json",
                    "Origin": origin,
                    "X-CSRF-Token": csrf_cookie,
                },
            )
            self.assertEqual(oversized.status, 400)
            self.assertEqual((await oversized.json())["error"], "request_too_large")

            purchased = await client.post(
                "/api/profile-store/purchase",
                json={"item_id": item_id},
                headers={"Origin": origin, "X-CSRF-Token": csrf_cookie},
            )
            self.assertEqual(purchased.status, 200, await purchased.text())
            payload = await purchased.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["purchase"]["item_id"], item_id)

            duplicate = await client.post(
                "/api/profile-store/purchase",
                json={"item_id": item_id},
                headers={"Origin": origin, "X-CSRF-Token": csrf_cookie},
            )
            self.assertEqual(duplicate.status, 409)
            self.assertEqual((await duplicate.json())["error"], "already_owned")
        finally:
            await client.close()

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
        self.assertTrue(all(item["currency"] in {"moon", "gem"} for item in data["items"]))

    async def test_coin_purchase_is_atomic_and_duplicate_purchase_does_not_charge_twice(self):
        item_id = await self._add_item("prime-coins-item", price=35, currency="moon")
        result = await profile_store_public._purchase_item(self.session, item_id)
        self.assertEqual(result["balance"], 65)
        self.assertEqual(result["currency"], "moon")

        with self.assertRaises(profile_store_public.StoreActionError) as caught:
            await profile_store_public._purchase_item(self.session, item_id)
        self.assertEqual(caught.exception.code, "already_owned")

        async with database.connect(aiosqlite.Row) as db:
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
        async with database.connect(aiosqlite.Row) as db:
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
        configuration = await profile_store_db.get_profile_card_configuration(99, 1234)
        self.assertIsNotNone(configuration["template"])
        self.assertEqual(int(configuration["template"]["item_id"]), item_id)
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

    def test_preview_changes_are_temporary_and_do_not_mutate_equipped_configuration(self):
        original_template = {"item_id": 10, "item_type": "card_template", "name": "Default"}
        original_badges = [{"item_id": 20, "item_type": "badge"}]
        current = {"template": original_template, "badges": original_badges}
        selected_template = {
            "item_id": 11, "item_type": "card_template", "name": "Preview",
            "style": {"accent": "#43D9FF"}, "metadata": {"layout": "portrait"},
        }
        preview = profile_store_public._apply_preview_item(current, selected_template)
        self.assertEqual(preview["template"]["item_id"], 11)
        self.assertEqual(current["template"]["item_id"], 10)
        self.assertEqual(preview["preview_item_id"], 11)

        selected_badge = {"item_id": 21, "item_type": "badge"}
        badge_preview = profile_store_public._apply_preview_item(current, selected_badge)
        self.assertEqual([item["item_id"] for item in badge_preview["badges"]], [20, 21])
        self.assertEqual([item["item_id"] for item in current["badges"]], [20])

        color_preview = profile_store_public._apply_preview_item(
            {"color_style": {"accent": "#43D9FF"}}, 
            {"item_id": 33, "item_type": "card_color", "style": {"accent": "#8B72FF"}},
        )
        self.assertEqual(color_preview["color_style"]["accent"], "#8B72FF")

    def test_storefront_routes_and_brand_are_registered_without_lona_or_crescent_branding(self):
        original_registered = profile_store_public._registered
        try:
            profile_store_public._registered = False
            routes = web.RouteTableDef()
            profile_store_public.register_profile_store_public_routes(routes)
            app = web.Application()
            app.add_routes(routes)
            paths = {resource.canonical for resource in app.router.resources()}
            self.assertIn("/profile-store", paths)
            self.assertIn("/api/profile-store/state", paths)
            self.assertIn("/api/profile-store/purchase", paths)
            self.assertIn("/api/profile-store/equip", paths)
            self.assertIn("/api/profile-store/assets/{item_id}", paths)
            self.assertIn("/api/profile-store/preview/{item_id}", paths)

            root = Path(__file__).resolve().parents[1] / "dashboard"
            html = (root / "profile-store.html").read_text("utf-8")
            css = (root / "profile-store.css").read_text("utf-8")
            js = (root / "profile-store.js").read_text("utf-8")
            self.assertIn("PRIME", html)
            self.assertIn("--cyan: #43d9ff", css)
            self.assertIn('@media (max-width: 720px)', css)
            self.assertIn('@media (max-width: 560px)', css)
            self.assertIn('@media (max-width: 390px)', css)
            self.assertIn('name="viewport"', html)
            self.assertIn("بلورات PRIME", js)
            self.assertIn("updateLivePreview(item)", js)
            self.assertIn("previewTemplateArt", html + js)
            self.assertIn("/api/profile-store/preview/", js)
            self.assertIn("عرض البطاقة الفعلية", html)
            self.assertNotIn("Lona", html + css + js)
            self.assertNotIn("🌙", html + css + js)
        finally:
            profile_store_public._registered = original_registered

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
