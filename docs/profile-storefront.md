# PRIME Profile Storefront

## Entry point

- Discord slash command: `/profile`.
- The bot sends an ephemeral, single-use store link valid for up to 15 minutes.
- `PROFILE_STORE_URL` should point to the HTTPS origin that runs `web_server.start_web_server`, normally `https://prime-panel.discloud.app/profile-store`.
- The server exchanges the one-time token for short-lived session and CSRF cookies, then redirects to `/profile-store` so the bearer token is removed from the visible URL.

## Public endpoints

- `GET /profile-store`: Arabic RTL storefront shell.
- `GET /api/profile-store/state`: current member, guild, active catalog, coins, crystals, inventory and equipped slots.
- `GET /api/profile-store/assets/{item_id}`: serves an active catalog asset only to an authenticated store session.
- `GET /api/profile-store/preview/{item_id}`: renders a temporary PNG using `profile_card_generator.generate_profile_card`; it does not persist the selected item or charge currency.
- `POST /api/profile-store/purchase`: atomic wallet deduction, inventory insert and transaction-ledger insert.
- `POST /api/profile-store/equip`: saves the selected template/background/color/flag or badge slot.
- `POST /api/profile-store/unequip`: removes the current item's equipped state.
- `POST /api/profile-store/logout`: revokes the current web session.

Write requests require the matching CSRF cookie/header and same-origin `Origin`. Catalog IDs are validated server-side. Purchases use an SQLite write transaction and verify ownership before inserting an inventory item, so replaying a purchase cannot charge twice.

## Currency and audit

- PRIME coins use the existing `users.balance` field, shared with the bot economy.
- Crystals use `user_profile_gem_wallets`, because the existing economy schema has no native crystal balance column.
- Crystal balances are changed only by the owner-authorized admin API route `POST /api/guild/{guild_id}/profile-store/users/{user_id}/gems`. The handler checks guild scope and writes both a transaction record and a privileged adjustment audit record.
- Crystal purchase prices therefore require an authorized admin grant/adjustment until a separate crystal-earning source is intentionally designed. Do not silently convert coins to crystals.

## Profile rendering

Equipment is stored in `user_profile_card_settings` or `user_profile_equipped_items`. The bot's `/profile` command reads those same records through `profile_store_db.get_profile_card_configuration` and generates the new card image. The Discord image already posted does not edit itself; after an equip change, use `/profile` to generate and show the updated card.

The storefront's quick preview is illustrative; **عرض البطاقة الفعلية** calls the actual renderer without saving the selection.

Approved repository-owned PNG templates resolve under `assets/profile_templates/`. Uploaded catalog images resolve under the configured private profile-store asset root, never from arbitrary web-root paths.

## Validation

The test suite covers single-use link exchange, session and CSRF enforcement, same-origin writes, bounded JSON bodies, duplicate/insufficient-funds purchases, coin/crystal ledgers, ownership-gated equip, card-renderer configuration, no-write preview selection, asset traversal and audited admin crystal grants. CI also runs `node --check dashboard/profile-store.js`.

Before deployment, verify the latest PR checks, deploy the web server and bot together, then manually smoke-test `/profile` → store link → catalog → free/paid purchase → equip → actual preview → run `/profile` again in a staging guild. No Rank code is part of this feature.
