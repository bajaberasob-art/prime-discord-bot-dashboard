import unittest
from types import SimpleNamespace

from profile_store_collaboration import (
    DEFAULT_TTL_SECONDS,
    PERMISSIONS,
    _hash_token,
    permission_required_for_request,
    validate_permissions,
    validate_ttl,
)


class CollaborationInputTests(unittest.TestCase):
    def test_permissions_are_deduplicated_and_normalized(self):
        self.assertEqual(
            validate_permissions(["publish_changes", "edit_designs", "edit_designs"]),
            ["edit_designs", "publish_changes"],
        )

    def test_owner_only_capabilities_cannot_be_granted_by_link(self):
        for invalid in (
            [],
            ["manage_collaborators"],
            ["settings"],
            ["manage_products", 4],
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_permissions(invalid)

    def test_duration_defaults_to_24_hours_and_accepts_only_supported_values(self):
        self.assertEqual(DEFAULT_TTL_SECONDS, 86400)
        for duration in (7200, 86400, 604800):
            self.assertEqual(validate_ttl(duration), duration)
        for duration in (True, 3600, 0, 86401, "86400"):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    validate_ttl(duration)

    def test_token_storage_uses_fixed_length_digest_not_plaintext(self):
        digest = _hash_token("random-temporary-token-that-is-long-enough")
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, "random-temporary-token-that-is-long-enough")
        self.assertTrue(all(ch in "0123456789abcdef" for ch in digest))

    def test_sensitive_settings_and_wallet_routes_are_owner_only(self):
        for path in (
            "/api/guild/123456789012345/profile-store/settings",
            "/api/guild/123456789012345/profile-store/users/234567890123456/gems",
            "/api/guild/123456789012345/profile-store/collaboration",
        ):
            request = SimpleNamespace(path=path, method="POST")
            self.assertIs(permission_required_for_request(request, set(PERMISSIONS)), False)

    def test_asset_upload_requires_upload_permission(self):
        request = SimpleNamespace(
            path="/api/guild/123456789012345/profile-store/items/7/asset",
            method="POST",
        )
        self.assertEqual(permission_required_for_request(request, {"upload_templates"}), "upload_templates")
        self.assertEqual(permission_required_for_request(request, {"edit_designs"}), "edit_designs")
        self.assertEqual(permission_required_for_request(request, {"manage_pricing"}), "upload_templates")

    def test_catalog_reorder_and_delete_require_product_management(self):
        requests = (
            SimpleNamespace(path="/api/guild/123456789012345/profile-store/items/reorder", method="PUT"),
            SimpleNamespace(path="/api/guild/123456789012345/profile-store/items/17", method="DELETE"),
            SimpleNamespace(path="/api/guild/123456789012345/profile-store/categories/3", method="DELETE"),
        )
        for request in requests:
            self.assertEqual(permission_required_for_request(request, {"manage_products"}), "manage_products")
            self.assertEqual(permission_required_for_request(request, {"edit_designs"}), "manage_products")

    def test_item_updates_are_checked_by_changed_field_group(self):
        request = SimpleNamespace(
            path="/api/guild/123456789012345/profile-store/items/17",
            method="PUT",
        )
        self.assertIsNone(permission_required_for_request(request, {"edit_designs"}))
        self.assertIsNone(permission_required_for_request(request, {"manage_pricing"}))

    def test_reading_catalog_does_not_grant_mutation(self):
        request = SimpleNamespace(
            path="/api/guild/123456789012345/profile-store",
            method="GET",
        )
        self.assertIsNone(permission_required_for_request(request, set()))


if __name__ == "__main__":
    unittest.main()
