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

PROFILE_STORE_SCHEMA_VERSION = 3
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

def _profile_template_metadata(width: int, height: int, layout: str) -> dict:
    """Build safe element coordinates for the approved raster template's native canvas."""
    w, h = width, height
    avatar_size = int(min(w, h) * (0.15 if layout == "portrait" else 0.17))
    if layout == "portrait":
        avatar = {"x": int(w * .66), "y": int(h * .10), "size": avatar_size}
        name = {"x": int(w * .91), "y": int(h * .36), "anchor": "ra"}
        handle = {"x": int(w * .91), "y": int(h * .395), "anchor": "ra"}
        level = {"x": int(w * .07), "y": int(h * .47), "anchor": "la"}
        xp_bar = {"x": int(w * .07), "y": int(h * .525), "width": int(w * .86), "height": max(10, int(h * .009))}
        badges = {"x": int(w * .07), "y": int(h * .575), "size": max(28, int(h * .025)), "limit": 5}
        stat_y1, stat_y2 = int(h * .67), int(h * .82)
        stat_w, stat_h = int(w * .41), int(h * .105)
        stats = [
            {"key": "balance", "label": "الرصيد", "x": int(w * .06), "y": stat_y1, "w": stat_w, "h": stat_h},
            {"key": "messages", "label": "الرسائل", "x": int(w * .53), "y": stat_y1, "w": stat_w, "h": stat_h},
            {"key": "rank", "label": "الترتيب", "x": int(w * .06), "y": stat_y2, "w": stat_w, "h": stat_h},
            {"key": "voice", "label": "الصوت", "x": int(w * .53), "y": stat_y2, "w": stat_w, "h": stat_h},
        ]
    elif layout == "square":
        avatar = {"x": int(w * .68), "y": int(h * .07), "size": avatar_size}
        name = {"x": int(w * .91), "y": int(h * .32), "anchor": "ra"}
        handle = {"x": int(w * .91), "y": int(h * .355), "anchor": "ra"}
        level = {"x": int(w * .07), "y": int(h * .39), "anchor": "la"}
        xp_bar = {"x": int(w * .07), "y": int(h * .45), "width": int(w * .86), "height": max(10, int(h * .012))}
        badges = {"x": int(w * .07), "y": int(h * .515), "size": max(26, int(h * .03)), "limit": 5}
        stat_y1, stat_y2 = int(h * .63), int(h * .79)
        stat_w, stat_h = int(w * .41), int(h * .12)
        stats = [
            {"key": "balance", "label": "الرصيد", "x": int(w * .06), "y": stat_y1, "w": stat_w, "h": stat_h},
            {"key": "messages", "label": "الرسائل", "x": int(w * .53), "y": stat_y1, "w": stat_w, "h": stat_h},
            {"key": "rank", "label": "الترتيب", "x": int(w * .06), "y": stat_y2, "w": stat_w, "h": stat_h},
            {"key": "voice", "label": "الصوت", "x": int(w * .53), "y": stat_y2, "w": stat_w, "h": stat_h},
        ]
    else:
        avatar = {"x": int(w * .78), "y": int(h * .08), "size": avatar_size}
        name = {"x": int(w * .73), "y": int(h * .39), "anchor": "ra"}
        handle = {"x": int(w * .73), "y": int(h * .43), "anchor": "ra"}
        level = {"x": int(w * .055), "y": int(h * .43), "anchor": "la"}
        xp_bar = {"x": int(w * .055), "y": int(h * .52), "width": int(w * .64), "height": max(9, int(h * .014))}
        badges = {"x": int(w * .055), "y": int(h * .60), "size": max(24, int(h * .035)), "limit": 5}
        stat_y1, stat_y2 = int(h * .76), int(h * .76)
        stat_w, stat_h = int(w * .19), int(h * .15)
        stats = [
            {"key": "balance", "label": "الرصيد", "x": int(w * .04), "y": stat_y1, "w": stat_w, "h": stat_h},
            {"key": "messages", "label": "الرسائل", "x": int(w * .27), "y": stat_y2, "w": stat_w, "h": stat_h},
            {"key": "rank", "label": "الترتيب", "x": int(w * .50), "y": stat_y2, "w": stat_w, "h": stat_h},
            {"key": "voice", "label": "الصوت", "x": int(w * .73), "y": stat_y2, "w": stat_w, "h": stat_h},
        ]
    return {
        "layout": layout, "width": w, "height": h,
        "orientation": "portrait" if layout == "portrait" else ("square" if layout == "square" else "landscape"),
        "avatar": avatar, "name": name, "handle": handle, "level": level,
        "xp_bar": xp_bar, "badges": badges,
        "flag": {"x": int(w * .045), "y": int(h * .035), "width": max(42, int(w * .055)), "height": max(28, int(h * .035))},
        "stats": stats,
    }


DEFAULT_PROFILE_TEMPLATES = (
    {
        "slug": "profile-neon-wide", "name": "جليد أزرق عريض",
        "description": "قالب PNG عريض أزرق جليدي مع عناصر الملف الشخصي.",
        "asset_path": "assets/profile_templates/02_wide_icy_blue.png",
        "price": 0, "currency": "moon", "rarity": "common",
        "style": {"visualStyle": "icy-blue", "accent": "#6CDFFF", "accent2": "#D9F8FF", "deep": "#071725", "glow": "#8AEAFF"},
        "metadata": _profile_template_metadata(1672, 941, "wide"),
    },
    {
        "slug": "profile-inferno-wide", "name": "ياقوت وذهب",
        "description": "قالب PNG فائق العرض بألوان الياقوت والذهب.",
        "asset_path": "assets/profile_templates/05_ultrawide_ruby_gold.png",
        "price": 18000, "currency": "moon", "rarity": "gold",
        "style": {"visualStyle": "ruby-gold", "accent": "#FF4A61", "accent2": "#FFD27A", "deep": "#17070C", "glow": "#FFB45C"},
        "metadata": _profile_template_metadata(1860, 845, "wide"),
    },
    {
        "slug": "profile-ocean-wide", "name": "زمرد أخضر",
        "description": "قالب PNG أفقي بإطار زمردي وشفافية أصلية.",
        "asset_path": "assets/profile_templates/04_landscape_emerald.png",
        "price": 15000, "currency": "moon", "rarity": "silver",
        "style": {"visualStyle": "emerald", "accent": "#24F0A5", "accent2": "#A6FFD9", "deep": "#06170F", "glow": "#70FFC8"},
        "metadata": _profile_template_metadata(1536, 1024, "wide"),
    },
    {
        "slug": "profile-rose-portrait", "name": "نيون وردي طولي",
        "description": "قالب PNG طولي وردي مع إطار نيون شفاف.",
        "asset_path": "assets/profile_templates/01_portrait_neon_pink.png",
        "price": 12000, "currency": "moon", "rarity": "silver",
        "style": {"visualStyle": "rose-neon", "accent": "#FF78B7", "accent2": "#FFC7E0", "deep": "#170B17", "glow": "#FF9CCC"},
        "metadata": _profile_template_metadata(1024, 1536, "portrait"),
    },
    {
        "slug": "profile-royal-portrait", "name": "أزرق قمري طولي",
        "description": "قالب PNG طولي أزرق بتصميم قمري.",
        "asset_path": "assets/profile_templates/file_00000000ecd0820ab92f03cf4eae4acd.png",
        "price": 300, "currency": "gem", "rarity": "legendary",
        "style": {"visualStyle": "moonlit-blue", "accent": "#6DAEFF", "accent2": "#D6E8FF", "deep": "#071226", "glow": "#8AC7FF"},
        "metadata": _profile_template_metadata(1024, 1536, "portrait"),
    },
    {
        "slug": "profile-emerald-square", "name": "بلور بنفسجي مربع",
        "description": "قالب PNG مربع بإطار بلوري بنفسجي شفاف.",
        "asset_path": "assets/profile_templates/03_square_violet_crystal.png",
        "price": 9000, "currency": "moon", "rarity": "silver",
        "style": {"visualStyle": "violet-crystal", "accent": "#9C67FF", "accent2": "#CFADFF", "deep": "#0C061C", "glow": "#CFADFF"},
        "metadata": _profile_template_metadata(1254, 1254, "square"),
    },
    {
        "slug": "profile-frost-compact", "name": "أماثيست مربع",
        "description": "قالب PNG مربع بصورة رمزية وإطار أماثيست.",
        "asset_path": "assets/profile_templates/file_00000000c03481f4963ba7074882a700.png",
        "price": 10000, "currency": "moon", "rarity": "common",
        "style": {"visualStyle": "amethyst", "accent": "#B68AFF", "accent2": "#E6D4FF", "deep": "#120A22", "glow": "#D0A9FF"},
        "metadata": _profile_template_metadata(1254, 1254, "square"),
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
    CREATE TABLE IF NOT EXISTS profile_store_web_sessions (
        session_hash TEXT PRIMARY KEY
            CHECK (length(session_hash) = 64 AND session_hash NOT GLOB '*[^0-9a-f]*'),
        csrf_hash TEXT NOT NULL
            CHECK (length(csrf_hash) = 64 AND csrf_hash NOT GLOB '*[^0-9a-f]*'),
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        CHECK (expires_at > created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profile_gem_wallets (
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (guild_id, user_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS profile_store_transactions (
        transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL CHECK (guild_id > 0),
        user_id INTEGER NOT NULL CHECK (user_id > 0),
        item_id INTEGER,
        transaction_type TEXT NOT NULL CHECK (
            transaction_type IN ('purchase', 'gem_grant', 'gem_adjustment')
        ),
        currency TEXT NOT NULL CHECK (currency IN ('moon', 'gem')),
        delta INTEGER NOT NULL,
        balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
        reason TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (item_id) REFERENCES profile_store_items(item_id) ON DELETE RESTRICT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_store_web_sessions_expiry
        ON profile_store_web_sessions(expires_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_profile_store_transactions_owner
        ON profile_store_transactions(guild_id, user_id, created_at DESC)
    """,
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
            # Keep existing item IDs/inventory valid while migrating legacy SVG entries to PNG assets.
            await db.executemany(
                """
                UPDATE profile_store_items
                SET name = :name, description = :description, rarity = :rarity,
                    price = :price, currency = :currency, asset_path = :asset_path,
                    style_json = :style_json, metadata_json = :metadata_json,
                    is_active = 1, deleted_at = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE slug = :slug AND item_type = 'card_template'
                """,
                [
                    {
                        "slug": item["slug"], "name": item["name"],
                        "description": item["description"], "rarity": item["rarity"],
                        "price": item["price"], "currency": item["currency"],
                        "asset_path": item["asset_path"],
                        "style_json": json.dumps(item["style"], ensure_ascii=False),
                        "metadata_json": json.dumps(item["metadata"], ensure_ascii=False),
                    }
                    for item in DEFAULT_PROFILE_TEMPLATES
                ],
            )
            await db.execute(
                """
                UPDATE profile_store_items
                SET is_active = 0, updated_at = CURRENT_TIMESTAMP
                WHERE slug IN ('profile-violet-split', 'profile-rose-wide')
                  AND item_type = 'card_template'
                """
            )
            await db.execute(
                """
                INSERT OR IGNORE INTO profile_store_migrations (version, description)
                VALUES (?, ?)
                """,
                (PROFILE_STORE_SCHEMA_VERSION, "Add secure PRIME storefront sessions, wallet ledger, and gem balances"),
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
