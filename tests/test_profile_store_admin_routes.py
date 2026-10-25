import io
import os
import tempfile
import unittest
from types import SimpleNamespace

import aiosqlite
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

import database
import profile_store_admin
import profile_store_db


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGBA", (72, 48), (36, 180, 220, 180)).save(buffer, format="PNG")
    return buffer.getvalue()


class ProfileStoreAdminRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_db_name = database.DB_NAME
        self.original_registered = profile_store_admin._registered
        self.temp_dir = tempfile.TemporaryDirectory(prefix="prime_store_admin_routes_")
        database.DB_NAME = os.path.join(self.temp_dir.name, "store.db")
        await database.init_db()
        await profile_store_db.ensure_profile_store_schema()
        profile_store_admin._registered = False

        async def authorize(_request, *, write=False, management_tier=None):
            return {"id": 9001, "_local_dev": True, "csrf": "test-csrf"}, SimpleNamespace(id=99)

        async def read_json_body(request):
            return await request.json()

        routes = web.RouteTableDef()
        profile_store_admin.register_profile_store_admin_routes(routes, authorize, read_json_body)
        app = web.Application(client_max_size=40 * 1024 * 1024)
        app["bot"] = SimpleNamespace(owner_id=9001, owner_ids=set(), application=None)
        app.add_routes(routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        profile_store_admin._registered = self.original_registered
        database.DB_NAME = self.original_db_name
        self.temp_dir.cleanup()

    async def _create_item(self, *, slug, name, item_type, category):
        response = await self.client.post(
            "/api/guild/99/profile-store/items",
            json={
                "slug": slug, "name": name, "description": "Admin console upload test",
                "item_type": item_type, "category": category, "subcategory": "",
                "rarity": "common", "price": 0, "currency": "moon",
                "style": {}, "metadata": {}, "is_active": True, "is_featured": False,
            },
        )
        self.assertEqual(response.status, 201, await response.text())
        return (await response.json())["item"]

    async def test_categories_can_be_created_renamed_and_removed_without_deleting_products(self):
        created = await self.client.post(
            "/api/guild/99/profile-store/categories",
            json={
                "slug": "seasonal-banners", "name": "بانرات موسمية",
                "description": "بانرات المناسبات", "icon": "▤",
                "sort_order": 15, "is_active": True,
            },
        )
        self.assertEqual(created.status, 201, await created.text())
        category = (await created.json())["category"]

        product = await self._create_item(
            slug="seasonal-test-banner", name="بانر تجريبي",
            item_type="background", category="seasonal-banners",
        )
        update = await self.client.put(
            f"/api/guild/99/profile-store/categories/{category['category_id']}",
            json={
                "slug": "summer-banners", "name": "بانرات الصيف",
                "description": "قسم تم تغيير اسمه", "icon": "☀",
                "sort_order": 18, "is_active": True,
            },
        )
        self.assertEqual(update.status, 200, await update.text())

        listing = await self.client.get("/api/guild/99/profile-store")
        self.assertEqual(listing.status, 200)
        data = await listing.json()
        saved = next(item for item in data["items"] if item["item_id"] == product["item_id"])
        self.assertEqual(saved["category"], "summer-banners")
        self.assertTrue(any(item["slug"] == "summer-banners" for item in data["categories"]))

        deleted = await self.client.delete(
            f"/api/guild/99/profile-store/categories/{category['category_id']}"
        )
        self.assertEqual(deleted.status, 200, await deleted.text())
        listing = await self.client.get("/api/guild/99/profile-store")
        data = await listing.json()
        saved = next(item for item in data["items"] if item["item_id"] == product["item_id"])
        self.assertEqual(saved["category"], "all", "Deleting a section must preserve its product")
        self.assertFalse(any(item["slug"] == "summer-banners" for item in data["categories"]))

    async def test_uploaded_background_and_card_template_are_stored_and_privately_previewable(self):
        cases = (
            ("upload-background", "بانر رفع", "background", "banners"),
            ("upload-template", "قالب رفع", "card_template", "templates"),
        )
        for slug, name, item_type, category in cases:
            with self.subTest(item_type=item_type):
                item = await self._create_item(slug=slug, name=name, item_type=item_type, category=category)
                form = FormData()
                form.add_field("file", png_bytes(), filename=f"{slug}.png", content_type="image/png")
                upload = await self.client.post(
                    f"/api/guild/99/profile-store/items/{item['item_id']}/asset",
                    data=form,
                )
                self.assertEqual(upload.status, 201, await upload.text())
                upload_data = await upload.json()
                self.assertTrue(upload_data["has_asset"])
                self.assertEqual(upload_data["format"], "PNG")

                preview = await self.client.get(
                    f"/api/guild/99/profile-store/items/{item['item_id']}/asset"
                )
                self.assertEqual(preview.status, 200)
                self.assertEqual(preview.headers.get("Content-Type"), "image/png")
                loaded = Image.open(io.BytesIO(await preview.read()))
                self.assertEqual(loaded.size, (72, 48))

    async def test_default_sections_are_seeded_and_visible_to_admin(self):
        response = await self.client.get("/api/guild/99/profile-store")
        self.assertEqual(response.status, 200)
        data = await response.json()
        slugs = {category["slug"] for category in data["categories"]}
        self.assertTrue({"backgrounds", "templates", "banners", "stickers", "flags"}.issubset(slugs))
        self.assertIn("settings", data)
        self.assertIn("items", data)


if __name__ == "__main__":
    unittest.main()
