import unittest
from pathlib import Path

from profile_store_admin import default_uploaded_template_metadata


class ProfileStoreAdminStudioUITests(unittest.TestCase):
    def test_admin_studio_has_catalog_sections_pricing_upload_and_filters(self):
        page = Path(__file__).resolve().parents[1] / "dashboard" / "profile-store-admin.html"
        source = page.read_text("utf-8")
        for required in (
            "إدارة متجر البروفايل",
            "الخلفيات",
            "القوالب",
            "البانرات",
            "الاستكرات",
            "الأعلام",
            "السعر (0 = مجاني)",
            "type=\"file\"",
            "profile-store-admin.js",
            "أقسام المتجر",
            "filterType",
            "filterStatus",
            "storeEnabled",
            "maxUpload",
            "sessionTtl",
            "categoryForm",
            "categoryParent",
            "categoryList",
            "assetPreview",
            "أقسام المتجر",
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

    def test_category_management_is_connected_to_protected_api_and_store_visibility(self):
        admin = (Path(__file__).resolve().parents[1] / "profile_store_admin.py").read_text("utf-8")
        public = (Path(__file__).resolve().parents[1] / "profile_store_public.py").read_text("utf-8")
        client = (Path(__file__).resolve().parents[1] / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        for route in (
            '/api/guild/{guild_id}/profile-store/categories',
            '/api/guild/{guild_id}/profile-store/categories/{category_id}',
            '/api/guild/{guild_id}/profile-store/categories/reorder',
        ):
            with self.subTest(route=route):
                self.assertIn(route, admin)
        self.assertIn("profile_store_catalog_audit", admin)
        self.assertIn("profile_store_categories", public)
        self.assertIn("categoryForm", client)
        self.assertIn("categories/reorder", client)
        self.assertIn("createObjectURL", client)
        self.assertIn("image/gif", client)
        self.assertIn("image/gif", (Path(__file__).resolve().parents[1] / "dashboard" / "profile-store-admin.html").read_text("utf-8"))

    def test_admin_metrics_count_paid_and_hidden_products_using_category_visibility(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "dashboard" / "profile-store-admin.html").read_text("utf-8")
        client = (root / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        for metric_id in ("countAll", "countActive", "countFree", "countPaid", "countHidden", "countMissing"):
            with self.subTest(metric_id=metric_id):
                self.assertIn(f'id="{metric_id}"', page)
                self.assertIn(f"$('{'"'}{metric_id}{'"'}')", client)
        self.assertIn("function isShopVisible(item)", client)
        self.assertIn("if(parent&&!parent.is_active)return false", client)
        self.assertIn("if(child&&!child.is_active)return false", client)
        self.assertIn("items.filter(i=>Number(i.price)>0).length", client)
        self.assertIn("items.filter(i=>!isShopVisible(i)).length", client)

    def test_uploaded_template_receives_safe_default_layout_metadata(self):
        portrait = default_uploaded_template_metadata(1024, 1536)
        wide = default_uploaded_template_metadata(1860, 845)
        for metadata, expected in ((portrait, "portrait"), (wide, "wide")):
            self.assertEqual(metadata["layout"], expected)
            self.assertEqual(len(metadata["stats"]), 4)
            self.assertGreater(metadata["avatar"]["size"], 0)
            self.assertGreater(metadata["xp_bar"]["width"], 0)
            self.assertLessEqual(metadata["width"] * metadata["height"], 2_500_000)
            self.assertEqual({stat["key"] for stat in metadata["stats"]},
                             {"balance", "messages", "rank", "voice"})
        with self.assertRaisesRegex(ValueError, "template_aspect_ratio_not_supported"):
            default_uploaded_template_metadata(5000, 400)

    def test_admin_asset_preview_route_is_owner_gated_and_path_checked(self):
        source = (Path(__file__).resolve().parents[1] / "profile_store_admin.py").read_text("utf-8")
        self.assertIn('@routes.get("/profile-store-admin/{guild_id}")', source)
        self.assertIn('session, _guild = await store_authorize(request)', source)
        self.assertIn('@routes.get("/api/guild/{guild_id}/profile-store/items/{item_id}/asset")', source)
        self.assertIn('from profile_store_public import _resolve_asset_path', source)
        store = (Path(__file__).resolve().parents[1] / "profile_store_public.py").read_text("utf-8")
        self.assertIn('def _resolve_asset_path(raw_path)', store)
        self.assertIn('trusted_root not in candidate.parents', store)
        server = (Path(__file__).resolve().parents[1] / "web_server.py").read_text("utf-8")
        self.assertIn('"profile-store-admin.js": "application/javascript"', server)
        client = (Path(__file__).resolve().parents[1] / "dashboard" / "profile-store-admin.js").read_text("utf-8")
        self.assertIn("X-CSRF-Token", client)
        self.assertIn("data-section", client)
        self.assertIn("categories.map", client)
        self.assertIn("/items/", client)
        self.assertIn("filterCategory", client)
        self.assertIn("max_custom_upload_bytes", client)
        self.assertIn("session_ttl_seconds", client)
        self.assertIn("custom:", (Path(__file__).resolve().parents[1] / "dashboard" / "profile-store.js").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
