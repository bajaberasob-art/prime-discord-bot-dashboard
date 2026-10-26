# PRIME Profile Store Category Management

This change extends the existing PRIME Profile Store. It does not create a second catalog or wallet.

## Data model

- `profile_store_items` remains the source of truth for product IDs, prices, currency, asset references, active/featured state, and item type.
- `user_profile_inventory`, `user_profile_card_settings`, `user_profile_equipped_items`, and `profile_store_transactions` continue to own purchase and equip state.
- `profile_store_categories` stores manageable top-level sections and one level of subcategories. Existing `category` and `subcategory` text values are backfilled during schema initialization; existing product IDs and inventory are unchanged.
- `profile_store_catalog_audit` records catalog, category, asset, and sensitive settings changes with actor, timestamp, entity, and before/after snapshots.

Categories are archived by setting `is_active = 0`, not hard-deleted. Hiding a parent or child section removes its products from the customer catalog without deleting the products or ownership records. Renaming a section updates the legacy category text fields on matching products in the same database transaction.

## Admin API

All endpoints use the existing dashboard authorization and CSRF/origin protections. The existing admin catalog response now includes a `categories` array.

- `GET /api/guild/{guild_id}/profile-store` — settings, products, and category tree.
- `POST /api/guild/{guild_id}/profile-store/categories` — create a section or subcategory.
- `PUT /api/guild/{guild_id}/profile-store/categories/{category_id}` — rename, reorder, move, publish, or hide a category.
- `PUT /api/guild/{guild_id}/profile-store/categories/reorder` — update the display order using the complete current category ID list.
- `DELETE /api/guild/{guild_id}/profile-store/categories/{category_id}` — archive a category; does not delete products.
- Existing `POST /api/guild/{guild_id}/profile-store/items`, `PUT`, `DELETE`, and `POST .../items/{item_id}/asset` remain the product and asset APIs.

Product create/update/delete and asset upload events are recorded in `profile_store_catalog_audit` in the same transaction as the catalog change. Product deletion is a soft delete so purchase and inventory foreign keys remain valid.

## Asset policy

The upload endpoint validates actual image format, declared MIME, extension, file size, pixel count, and animation bounds, strips source metadata, and stores assets outside the public dashboard directory. PNG/JPEG/WEBP are re-encoded as static images. GIF uploads preserve animation with a maximum of 60 frames, 32 million aggregate frame pixels, and a 5MB sanitized output limit; APNG and animated WebP remain rejected. The admin page shows a local preview before upload. Profile cards are rendered as PNG, so an animated GIF used as a profile background is represented by its first frame in the final card image.

## Verification

CI runs JavaScript syntax checks, Python compilation, and the existing plus new test suite. Tests cover category migration, category payload validation, category API/audit integration, customer-store visibility, existing purchases, and image validation. A green CI run does not itself verify a production Discloud deployment; verify the live dashboard and a test purchase separately after deployment.
