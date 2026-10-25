"""Database schema for PRIME's profile-card customization store.

This module deliberately uses PRIME's existing aiosqlite connection manager.
The profile store lives in the same SQLite database as the bot's wallet and
leveling data; it must never silently create a separate database.

Prices are integer currency units. A price of zero means the item is free.
Temporary browser tokens are stored as SHA-256 digests, never in plaintext.
"""

from __future__ import annotations

import hashlib
import database

PROFILE_STORE_SCHEMA_VERSION = 1
DEFAULT_SESSION_TTL_SECONDS = 15 * 60
DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024

STORE_CURRENCIES = ("moon", "gem")
STORE_ITEM_TYPES = (
    "background",
    "card_color",
    "card_template",
    "flag",
    "badge",
    "shape",
    "club_logo",
)
STORE_RARITIES = ("common", "silver", "gold", "legendary")

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS profile_store_migrations (
        version INTEGER PRIMARY KEY CHECK (version > 0),
        description TEXT NOT NULL,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_store_settings (
        settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
        custom_upload_enabled INTEGER NOT NULL DEFAULT 0
            CHECK (custom_upload_enabled IN (0, 1)),
        custom_upload_price INTEGER NOT NULL DEFAULT 0
            CHECK (custom_upload_price >= 0),
        custom_upload_currency TEXT NOT NULL DEFAULT 'moon'
            CHECK (custom_upload_currency IN ('moon', 'gem')),
        max_custom_upload_bytes INTEGER NOT NULL DEFAULT 5242880
            CHECK (max_custom_upload_bytes BETWEEN 1024 AND 20971520),
        session_ttl_seconds INTEGER NOT NULL DEFAULT 900
            CHECK (session_ttl_seconds BETWEEN 60 AND 900),
        updated_by INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_store_items (
        item_id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 100),
        description TEXT NOT NULL DEFAULT '',
        item_type TEXT NOT NULL CHECK (item_type IN (
            'background', 'card_color', 'card_template',
            'flag', 'badge', 'shape', 'club_logo'
        )),
        category TEXT NOT NULL DEFAULT 'all',
        subcategory TEXT NOT NULL DEFAULT '',
        rarity TEXT NOT NULL DEFAULT 'common'
            CHECK (rarity IN ('common', 'silver', 'gold', 'legendary')),
        price INTEGER NOT NULL DEFAULT 0 CHECK (price >= 0),
        currency TEXT NOT NULL DEFAULT 'moon'
            CHECK (currency IN ('moon', 'gem')),
        asset_path TEXT,
        asset_url TEXT,
        style_json TEXT NOT NULL DEFAULT '{}',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
        is_featured INTEGER NOT NULL DEFAULT 0 CHECK (is_featured IN (0, 1)),
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        deleted_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profile_inventory (
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        item_id INTEGER NOT NULL,
        price_paid INTEGER NOT NULL DEFAULT 0 CHECK (price_paid >= 0),
        currency TEXT NOT NULL DEFAULT 'moon'
            CHECK (currency IN ('moon', 'gem')),
        acquisition_source TEXT NOT NULL DEFAULT 'purchase'
            CHECK (acquisition_source IN (
                'purchase', 'free', 'admin', 'event', 'migration'
            )),
        acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id, item_id),
        FOREIGN KEY (item_id) REFERENCES profile_store_items(item_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profile_card_settings (
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        background_item_id INTEGER,
        template_item_id INTEGER,
        color_item_id INTEGER,
        flag_item_id INTEGER,
        accent_color TEXT NOT NULL DEFAULT '#F58FB2',
        divider_color TEXT NOT NULL DEFAULT '#F58FB2',
        xp_color TEXT NOT NULL DEFAULT '#F58FB2',
        font_color TEXT NOT NULL DEFAULT '#FFFFFF',
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id),
        FOREIGN KEY (guild_id, user_id, background_item_id)
            REFERENCES user_profile_inventory(guild_id, user_id, item_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (guild_id, user_id, template_item_id)
            REFERENCES user_profile_inventory(guild_id, user_id, item_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (guild_id, user_id, color_item_id)
            REFERENCES user_profile_inventory(guild_id, user_id, item_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (guild_id, user_id, flag_item_id)
            REFERENCES user_profile_inventory(guild_id, user_id, item_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profile_equipped_items (
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        slot_key TEXT NOT NULL CHECK (length(trim(slot_key)) BETWEEN 1 AND 32),
        item_id INTEGER NOT NULL,
        equipped_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id, slot_key),
        UNIQUE (guild_id, user_id, item_id),
        FOREIGN KEY (guild_id, user_id, item_id)
            REFERENCES user_profile_inventory(guild_id, user_id, item_id)
            ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_store_sessions (
        session_id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE
            CHECK (
                length(token_hash) = 64
                AND token_hash NOT GLOB '*[^0-9a-f]*'
            ),
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT,
        revoked_at TEXT,
        CHECK (expires_at > created_at),
        CHECK (consumed_at IS NULL OR consumed_at >= created_at),
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_store_items_catalog
        ON profile_store_items(item_type, category, is_active, rarity, price)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_store_items_deleted
        ON profile_store_items(deleted_at, is_active)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_inventory_user
        ON user_profile_inventory(guild_id, user_id, acquired_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_inventory_item
        ON user_profile_inventory(item_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_equipped_user
        ON user_profile_equipped_items(guild_id, user_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_store_sessions_expiry
        ON profile_store_sessions(expires_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_store_sessions_owner
        ON profile_store_sessions(guild_id, user_id, expires_at)
    """,
)


async def ensure_profile_store_schema() -> None:
    """Create the profile-store tables atomically; safe to run at every startup."""
    async with database.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA_STATEMENTS:
                await db.execute(statement)

            # Singleton row contains global feature flags and safe upload defaults.
            await db.execute(
                "INSERT OR IGNORE INTO profile_store_settings (settings_id) VALUES (1)"
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO profile_store_migrations (version, description)
                VALUES (?, ?)
                """,
                (PROFILE_STORE_SCHEMA_VERSION, "Initial profile card store schema"),
            )
            await db.commit()
        except BaseException:
            await db.rollback()
            raise


def hash_session_token(raw_token: str) -> str:
    """Return the lowercase SHA-256 digest to persist instead of a raw token."""
    if not isinstance(raw_token, str) or not raw_token or len(raw_token) > 512:
        raise ValueError("session token must be a non-empty string of at most 512 characters")
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
