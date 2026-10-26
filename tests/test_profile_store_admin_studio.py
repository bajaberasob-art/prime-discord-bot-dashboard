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
        ):
            with self.subTest(required=required):
                self.assertIn(required, source)

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
            default_uploaded_template_metadata(5000, 200)

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
        self.assertIn("custom:", (Path(__file__).resolve().parents[1] / "dashboard" / "profile-store.js").read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
