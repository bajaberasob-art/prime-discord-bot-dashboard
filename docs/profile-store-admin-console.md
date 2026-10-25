# PRIME Store Admin Console

## Open the console

While signed in to the PRIME dashboard, select a server and open **إدارة متجر PRIME** from the left navigation. The dedicated route is:

`/guild/{guild_id}/profile-store-admin`

The page is protected by the existing dashboard session, same-origin and CSRF checks, live administrator grant, and the bot-owner / configured-store-admin gate. The store catalog and categories are global to PRIME; the selected guild is the authorization context and the namespace used for new asset files.

## Products

The console can create, edit, publish, hide, and soft-delete catalog items. Each item has a technical slug, name, description, item type, custom section, optional subcategory, rarity, price, currency, and featured/active flags.

- **Free:** set the price mode to free (the saved catalog price is zero).
- **Paid:** choose PRIME coins or PRIME gems and enter a positive integer price.
- **Image upload:** attach a PNG, JPEG, or WebP image up to 5 MiB. The server verifies the real image format, checks dimensions and frame count, re-encodes the image to remove metadata, and stores it outside the public dashboard directory.
- **Item types:** background/banner, profile-card template, badge/sticker, flag, card color, shape/frame, and club logo. Image-backed card templates are accepted by the upload endpoint as well as backgrounds and badges.

The admin-only asset-preview endpoint is authorized separately; shoppers receive assets through the existing storefront session endpoint. Removing a product hides it from the catalog without deleting purchase/inventory history.

## Sections

Sections have a slug, display name, description, icon, sort order, and active status. Active sections are returned by the public storefront API and rendered as purchase-page tabs. Product edits use the section slug, so renaming a section migrates the category value on products that reference it. Hiding a section removes its tab; products remain available from **كل الأقسام**. Deleting a custom section moves its products to **كل الأقسام**. Built-in sections cannot be deleted.

Existing catalog category strings are migrated into stable section slugs without deleting products. The migration also normalizes legacy `all` category values to a section suited to the item's type.

## Verification boundaries

Unit/integration tests cover category CRUD, category-to-product migration, image upload and validation, private image preview, default section migration, and the owner console routes. The image upload path intentionally accepts static images only; animated GIF/APNG/WebP are rejected. Deployment to Discloud and visual confirmation in a live dashboard require the PR to be merged and the app redeployed.
