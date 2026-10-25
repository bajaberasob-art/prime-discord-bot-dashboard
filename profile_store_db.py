"""Database schema for PRIME's profile-card customization store.

This module deliberately uses PRIME's existing aiosqlite connection manager.
The profile store lives in the same SQLite database as the bot's wallet and
leveling data; it must never silently create a separate database.

Prices are integer currency units. A price of zero means the item is free.
Temporary browser tokens are stored as SHA-256 digests, never in plaintext.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

import aiosqlite
import database

PROFILE_STORE_SCHEMA_VERSION = 2
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

DEFAULT_PROFILE_TEMPLATES = (
    {
        "slug": "profile-neon-wide", "name": "نيون عريض",
        "description": "قالب أفقي عريض بإطار نيون بنفسجي وتوزيع إحصائيات سفلي.",
        "asset_path": "assets/profile_templates/neon_wide_violet.svg",
        "price": 0, "currency": "moon", "rarity": "common",
        "style": {"visualStyle": "cyberpunk", "accent": "#B34CFF", "deep": "#090611", "glow": "#D66BFF"},
        "metadata": {
            "layout": "wide", "width": 1280, "height": 460, "orientation": "landscape",
            "avatar": {"x": 1048, "y": 56, "size": 170},
            "name": {"x": 900, "y": 245, "anchor": "ra"},
            "handle": {"x": 900, "y": 278, "anchor": "ra"},
            "level": {"x": 82, "y": 214, "anchor": "la"},
            "xp_bar": {"x": 82, "y": 260, "width": 760, "height": 13},
            "badges": {"x": 84, "y": 300, "size": 34, "limit": 5},
            "flag": {"x": 70, "y": 42, "width": 62, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 64, "y": 350, "w": 174, "h": 72},
                {"key": "messages", "label": "الرسائل", "x": 254, "y": 350, "w": 174, "h": 72},
                {"key": "rank", "label": "الترتيب", "x": 444, "y": 350, "w": 174, "h": 72},
                {"key": "voice", "label": "الصوت", "x": 634, "y": 350, "w": 174, "h": 72},
            ],
        },
    },
    {
        "slug": "profile-inferno-wide", "name": "جحيم أحمر",
        "description": "قالب أفقي أحمر بتفاصيل حادة وإضاءة قتالية.",
        "asset_path": "assets/profile_templates/inferno_wide_red.svg",
        "price": 18000, "currency": "moon", "rarity": "gold",
        "style": {"visualStyle": "inferno", "accent": "#FF304B", "deep": "#15070B", "glow": "#FF5266"},
        "metadata": {
            "layout": "wide", "width": 1400, "height": 480, "orientation": "landscape",
            "avatar": {"x": 1152, "y": 56, "size": 178},
            "name": {"x": 990, "y": 250, "anchor": "ra"},
            "handle": {"x": 990, "y": 284, "anchor": "ra"},
            "level": {"x": 84, "y": 220, "anchor": "la"},
            "xp_bar": {"x": 84, "y": 266, "width": 850, "height": 14},
            "badges": {"x": 86, "y": 306, "size": 36, "limit": 5},
            "flag": {"x": 72, "y": 42, "width": 64, "height": 42},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 70, "y": 372, "w": 190, "h": 76},
                {"key": "messages", "label": "الرسائل", "x": 274, "y": 372, "w": 190, "h": 76},
                {"key": "rank", "label": "الترتيب", "x": 478, "y": 372, "w": 190, "h": 76},
                {"key": "voice", "label": "الصوت", "x": 682, "y": 372, "w": 190, "h": 76},
            ],
        },
    },
    {
        "slug": "profile-ocean-wide", "name": "موج أزرق",
        "description": "قالب أفقي بلون سماوي مع خطوط متدفقة.",
        "asset_path": "assets/profile_templates/ocean_wide_blue.svg",
        "price": 15000, "currency": "moon", "rarity": "silver",
        "style": {"visualStyle": "ocean", "accent": "#17D9FF", "deep": "#04121F", "glow": "#55E8FF"},
        "metadata": {
            "layout": "wide", "width": 1280, "height": 460, "orientation": "landscape",
            "avatar": {"x": 1048, "y": 56, "size": 170},
            "name": {"x": 900, "y": 245, "anchor": "ra"},
            "handle": {"x": 900, "y": 278, "anchor": "ra"},
            "level": {"x": 82, "y": 214, "anchor": "la"},
            "xp_bar": {"x": 82, "y": 260, "width": 760, "height": 13},
            "badges": {"x": 84, "y": 300, "size": 34, "limit": 5},
            "flag": {"x": 70, "y": 42, "width": 62, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 64, "y": 350, "w": 174, "h": 72},
                {"key": "messages", "label": "الرسائل", "x": 254, "y": 350, "w": 174, "h": 72},
                {"key": "rank", "label": "الترتيب", "x": 444, "y": 350, "w": 174, "h": 72},
                {"key": "voice", "label": "الصوت", "x": 634, "y": 350, "w": 174, "h": 72},
            ],
        },
    },
    {
        "slug": "profile-rose-portrait", "name": "وردي طولي",
        "description": "قالب طولي بإطار وردي وتوزيع إحصائيات على عمودين.",
        "asset_path": "assets/profile_templates/rose_portrait.svg",
        "price": 12000, "currency": "moon", "rarity": "silver",
        "style": {"visualStyle": "rose", "accent": "#FF78B7", "deep": "#170B17", "glow": "#FF9CCC"},
        "metadata": {
            "layout": "portrait", "width": 760, "height": 1120, "orientation": "portrait",
            "avatar": {"x": 526, "y": 382, "size": 154},
            "name": {"x": 686, "y": 580, "anchor": "ra"},
            "handle": {"x": 686, "y": 616, "anchor": "ra"},
            "level": {"x": 60, "y": 660, "anchor": "la"},
            "xp_bar": {"x": 60, "y": 708, "width": 640, "height": 14},
            "badges": {"x": 62, "y": 750, "size": 38, "limit": 5},
            "flag": {"x": 54, "y": 40, "width": 60, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 52, "y": 805, "w": 310, "h": 92},
                {"key": "messages", "label": "الرسائل", "x": 396, "y": 805, "w": 310, "h": 92},
                {"key": "rank", "label": "الترتيب", "x": 52, "y": 920, "w": 310, "h": 92},
                {"key": "voice", "label": "الصوت", "x": 396, "y": 920, "w": 310, "h": 92},
            ],
        },
    },
    {
        "slug": "profile-royal-portrait", "name": "ملكي طولي",
        "description": "قالب طولي ذهبي بزخارف ملكية.",
        "asset_path": "assets/profile_templates/royal_portrait.svg",
        "price": 300, "currency": "gem", "rarity": "legendary",
        "style": {"visualStyle": "royal", "accent": "#FFC75A", "deep": "#1C1205", "glow": "#FFE19A"},
        "metadata": {
            "layout": "portrait", "width": 780, "height": 1160, "orientation": "portrait",
            "avatar": {"x": 540, "y": 398, "size": 160},
            "name": {"x": 710, "y": 596, "anchor": "ra"},
            "handle": {"x": 710, "y": 632, "anchor": "ra"},
            "level": {"x": 62, "y": 682, "anchor": "la"},
            "xp_bar": {"x": 62, "y": 730, "width": 656, "height": 14},
            "badges": {"x": 64, "y": 772, "size": 38, "limit": 5},
            "flag": {"x": 58, "y": 42, "width": 60, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 52, "y": 840, "w": 320, "h": 92},
                {"key": "messages", "label": "الرسائل", "x": 408, "y": 840, "w": 320, "h": 92},
                {"key": "rank", "label": "الترتيب", "x": 52, "y": 954, "w": 320, "h": 92},
                {"key": "voice", "label": "الصوت", "x": 408, "y": 954, "w": 320, "h": 92},
            ],
        },
    },
    {
        "slug": "profile-emerald-square", "name": "زمرد مربع",
        "description": "قالب مربع بإضاءة خضراء وإحصائيات شبكية.",
        "asset_path": "assets/profile_templates/emerald_square.svg",
        "price": 9000, "currency": "moon", "rarity": "silver",
        "style": {"visualStyle": "emerald", "accent": "#24F0A5", "deep": "#06170F", "glow": "#70FFC8"},
        "metadata": {
            "layout": "square", "width": 900, "height": 900, "orientation": "square",
            "avatar": {"x": 648, "y": 94, "size": 164},
            "name": {"x": 600, "y": 320, "anchor": "ra"},
            "handle": {"x": 600, "y": 354, "anchor": "ra"},
            "level": {"x": 72, "y": 382, "anchor": "la"},
            "xp_bar": {"x": 72, "y": 426, "width": 756, "height": 14},
            "badges": {"x": 74, "y": 466, "size": 34, "limit": 5},
            "flag": {"x": 62, "y": 42, "width": 62, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 60, "y": 540, "w": 372, "h": 110},
                {"key": "messages", "label": "الرسائل", "x": 468, "y": 540, "w": 372, "h": 110},
                {"key": "rank", "label": "الترتيب", "x": 60, "y": 678, "w": 372, "h": 110},
                {"key": "voice", "label": "الصوت", "x": 468, "y": 678, "w": 372, "h": 110},
            ],
        },
    },
    {
        "slug": "profile-frost-compact", "name": "جليد عريض",
        "description": "قالب أفقي قصير للمعلومات المختصرة.",
        "asset_path": "assets/profile_templates/frost_compact.svg",
        "price": 10000, "currency": "moon", "rarity": "common",
        "style": {"visualStyle": "frost", "accent": "#B8E8FF", "deep": "#081522", "glow": "#F0FBFF"},
        "metadata": {
            "layout": "compact", "width": 1320, "height": 360, "orientation": "landscape",
            "avatar": {"x": 1150, "y": 36, "size": 126},
            "name": {"x": 1000, "y": 168, "anchor": "ra"},
            "handle": {"x": 1000, "y": 198, "anchor": "ra"},
            "level": {"x": 64, "y": 152, "anchor": "la"},
            "xp_bar": {"x": 64, "y": 197, "width": 820, "height": 12},
            "badges": {"x": 66, "y": 220, "size": 28, "limit": 5},
            "flag": {"x": 58, "y": 30, "width": 56, "height": 36},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 52, "y": 270, "w": 190, "h": 68},
                {"key": "messages", "label": "الرسائل", "x": 258, "y": 270, "w": 190, "h": 68},
                {"key": "rank", "label": "الترتيب", "x": 464, "y": 270, "w": 190, "h": 68},
                {"key": "voice", "label": "الصوت", "x": 670, "y": 270, "w": 190, "h": 68},
            ],
        },
    },
    {
        "slug": "profile-violet-split", "name": "بنفسجي سينمائي",
        "description": "قالب عرضي مقسوم بإطار بنفسجي عميق.",
        "asset_path": "assets/profile_templates/violet_split.svg",
        "price": 25000, "currency": "moon", "rarity": "gold",
        "style": {"visualStyle": "violet", "accent": "#9C67FF", "deep": "#0C061C", "glow": "#CFADFF"},
        "metadata": {
            "layout": "split", "width": 1200, "height": 600, "orientation": "landscape",
            "avatar": {"x": 925, "y": 74, "size": 178},
            "name": {"x": 1090, "y": 300, "anchor": "ra"},
            "handle": {"x": 1090, "y": 336, "anchor": "ra"},
            "level": {"x": 84, "y": 292, "anchor": "la"},
            "xp_bar": {"x": 84, "y": 338, "width": 720, "height": 14},
            "badges": {"x": 86, "y": 390, "size": 36, "limit": 5},
            "flag": {"x": 70, "y": 44, "width": 62, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 62, "y": 496, "w": 178, "h": 76},
                {"key": "messages", "label": "الرسائل", "x": 256, "y": 496, "w": 178, "h": 76},
                {"key": "rank", "label": "الترتيب", "x": 450, "y": 496, "w": 178, "h": 76},
                {"key": "voice", "label": "الصوت", "x": 644, "y": 496, "w": 178, "h": 76},
            ],
        },
    },
    {
        "slug": "profile-rose-wide", "name": "وردي مزهر",
        "description": "قالب أفقي وردي بزخارف ناعمة وحواف مضيئة.",
        "asset_path": "assets/profile_templates/rose_wide.svg",
        "price": 20000, "currency": "moon", "rarity": "gold",
        "style": {"visualStyle": "rose", "accent": "#FF4DA6", "deep": "#190714", "glow": "#FFC4E2"},
        "metadata": {
            "layout": "wide", "width": 1280, "height": 460, "orientation": "landscape",
            "avatar": {"x": 1048, "y": 56, "size": 170},
            "name": {"x": 900, "y": 245, "anchor": "ra"},
            "handle": {"x": 900, "y": 278, "anchor": "ra"},
            "level": {"x": 82, "y": 214, "anchor": "la"},
            "xp_bar": {"x": 82, "y": 260, "width": 760, "height": 13},
            "badges": {"x": 84, "y": 300, "size": 34, "limit": 5},
            "flag": {"x": 70, "y": 42, "width": 62, "height": 40},
            "stats": [
                {"key": "balance", "label": "الرصيد", "x": 64, "y": 350, "w": 174, "h": 72},
                {"key": "messages", "label": "الرسائل", "x": 254, "y": 350, "w": 174, "h": 72},
                {"key": "rank", "label": "الترتيب", "x": 444, "y": 350, "w": 174, "h": 72},
                {"key": "voice", "label": "الصوت", "x": 634, "y": 350, "w": 174, "h": 72},
            ],
        },
    },
)

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
                (1, "Initial profile card store schema"),
            )
            await db.executemany(
                """
                INSERT OR IGNORE INTO profile_store_items
                    (slug, name, description, item_type, category, subcategory,
                     rarity, price, currency, asset_path, style_json,
                     metadata_json, is_active, is_featured)
                VALUES (
                    :slug, :name, :description, 'card_template',
                    'templates', 'profile', :rarity, :price, :currency,
                    :asset_path, :style_json, :metadata_json, 1, 0
                )
                """,
                [
                    {
                        "slug": item["slug"],
                        "name": item["name"],
                        "description": item["description"],
                        "rarity": item["rarity"],
                        "price": item["price"],
                        "currency": item["currency"],
                        "asset_path": item["asset_path"],
                        "style_json": json.dumps(item["style"], ensure_ascii=False),
                        "metadata_json": json.dumps(item["metadata"], ensure_ascii=False),
                    }
                    for item in DEFAULT_PROFILE_TEMPLATES
                ],
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO profile_store_migrations (version, description)
                VALUES (?, ?)
                """,
                (PROFILE_STORE_SCHEMA_VERSION, "Seed multiple independent profile card layouts"),
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


class ProfileStoreDisabled(RuntimeError):
    """Raised when the shop has been disabled by an administrator."""


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def create_profile_store_session(guild_id: int, user_id: int) -> tuple[str, str]:
    """Create a short-lived, single-use-by-policy browser token and persist only its hash.

    The token's one-time consumption is enforced by the Phase 3/4 web endpoint.
    This function never logs or stores the plaintext token.
    """
    if int(guild_id) <= 0 or int(user_id) <= 0:
        raise ValueError("guild_id and user_id must be positive integers")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_session_token(raw_token)
    now = datetime.now(timezone.utc)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT enabled, session_ttl_seconds FROM profile_store_settings WHERE settings_id = 1"
            ) as cursor:
                settings = await cursor.fetchone()
            if settings and not int(settings["enabled"]):
                raise ProfileStoreDisabled("profile store is disabled")
            ttl = int(settings["session_ttl_seconds"]) if settings else DEFAULT_SESSION_TTL_SECONDS
            ttl = max(60, min(DEFAULT_SESSION_TTL_SECONDS, ttl))
            expires = now + timedelta(seconds=ttl)
            created_text, expires_text = _utc_iso(now), _utc_iso(expires)
            await db.execute(
                "DELETE FROM profile_store_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
                (created_text,),
            )
            await db.execute(
                """
                INSERT INTO profile_store_sessions
                    (token_hash, guild_id, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (token_hash, int(guild_id), int(user_id), created_text, expires_text),
            )
            await db.commit()
            return raw_token, expires_text
        except BaseException:
            await db.rollback()
            raise


async def get_profile_card_configuration(guild_id: int, user_id: int) -> dict:
    """Return the member's selected card assets and a safe default template."""
    guild_id, user_id = int(guild_id), int(user_id)
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM user_profile_card_settings WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            settings_row = await cursor.fetchone()
        settings = dict(settings_row) if settings_row else {
            "guild_id": guild_id,
            "user_id": user_id,
            "background_item_id": None,
            "template_item_id": None,
            "color_item_id": None,
            "flag_item_id": None,
            "accent_color": "#F58FB2",
            "divider_color": "#F58FB2",
            "xp_color": "#F58FB2",
            "font_color": "#FFFFFF",
        }

        async def fetch_item(item_id):
            if not item_id:
                return None
            async with db.execute(
                """
                SELECT item_id, slug, name, item_type, asset_path, asset_url,
                       style_json, metadata_json
                FROM profile_store_items
                WHERE item_id = ? AND is_active = 1 AND deleted_at IS NULL
                """,
                (int(item_id),),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            for key in ("style_json", "metadata_json"):
                try:
                    item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
                except (TypeError, ValueError):
                    item[key.replace("_json", "")] = {}
            return item

        template = await fetch_item(settings.get("template_item_id"))
        if not template:
            async with db.execute(
                """
                SELECT item_id FROM profile_store_items
                WHERE slug = 'profile-neon-wide' AND item_type = 'card_template'
                    AND is_active = 1 AND deleted_at IS NULL
                """
            ) as cursor:
                default_template = await cursor.fetchone()
            template = await fetch_item(default_template["item_id"]) if default_template else None

        background = await fetch_item(settings.get("background_item_id"))
        flag = await fetch_item(settings.get("flag_item_id"))
        badges = []
        async with db.execute(
            """
            SELECT i.item_id, i.slug, i.name, i.item_type, i.asset_path,
                   i.asset_url, i.style_json, i.metadata_json, e.slot_key
            FROM user_profile_equipped_items e
            JOIN user_profile_inventory inv
              ON inv.guild_id = e.guild_id AND inv.user_id = e.user_id
             AND inv.item_id = e.item_id
            JOIN profile_store_items i ON i.item_id = e.item_id
            WHERE e.guild_id = ? AND e.user_id = ?
              AND i.item_type IN ('badge', 'shape', 'club_logo')
              AND i.is_active = 1 AND i.deleted_at IS NULL
            ORDER BY e.slot_key
            LIMIT 5
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            item = dict(row)
            for key in ("style_json", "metadata_json"):
                try:
                    item[key.replace("_json", "")] = json.loads(item.get(key) or "{}")
                except (TypeError, ValueError):
                    item[key.replace("_json", "")] = {}
            badges.append(item)

    settings.update({
        "template": template,
        "background": background,
        "flag": flag,
        "badges": badges,
    })
    if settings.get("color_item_id"):
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT style_json FROM profile_store_items WHERE item_id = ? AND is_active = 1",
                (int(settings["color_item_id"]),),
            ) as cursor:
                color_row = await cursor.fetchone()
        if color_row:
            try:
                settings["color_style"] = json.loads(color_row["style_json"] or "{}")
            except (TypeError, ValueError):
                settings["color_style"] = {}
    else:
        settings["color_style"] = {}
    return settings


async def get_profile_wallet(guild_id: int, user_id: int) -> dict:
    """Read the existing PRIME wallet without creating or duplicating balances."""
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT balance, bank FROM users WHERE guild_id = ? AND user_id = ?",
            (int(guild_id), int(user_id)),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else {"balance": 0, "bank": 0}
