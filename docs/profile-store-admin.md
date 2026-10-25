# PRIME Store Admin Console

Open the console at `/profile-store-admin` on the dashboard host.

## What it manages

- Create, edit, publish, hide, or soft-delete catalog items.
- Upload sanitized static PNG, JPEG, or WebP artwork for backgrounds/banners, card templates, stickers/badges, flags, shapes, and club logos.
- Set the price to `0` for a free item or choose PRIME Coins / PRIME Gems for paid products.
- Set rarity, featured state, category, and subcategory. New category names appear automatically in the storefront's category filters.
- Turn the storefront on/off and configure member-upload settings and session lifetime.

## Access and storage

The console UI is only a shell. All catalog/settings reads and writes are protected by the existing dashboard session, guild management authorization, global profile-store owner check, and CSRF validation. Uploaded content is validated using Pillow, rejects animations and oversized/pixel-bomb files, is re-encoded to remove embedded metadata, and is stored outside the public dashboard directory. Admin previews use a guild-authorized endpoint rather than exposing filesystem paths.

The product catalog and wallet logic remain on the existing `profile_store_items` tables and purchase endpoints; this page does not create a separate store database.

## Limitations

Images are static only (PNG, JPEG, WebP); GIF/APNG/WebP animation is intentionally rejected for product uploads. A card template can include layout metadata through the advanced JSON editor. Live appearance must still be validated on the deployed bot after publishing changes.
