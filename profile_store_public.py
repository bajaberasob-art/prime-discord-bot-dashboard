"""Authenticated public storefront for PRIME profile-card cosmetics."""
from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import aiosqlite
from aiohttp import web

import database
import profile_store_db
from profile_card_service import build_profile_card_data

logger = logging.getLogger("PrimeProfileStore")
ROOT = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT / "dashboard"
SESSION_COOKIE = "prime_profile_store"
CSRF_COOKIE = "prime_profile_csrf"
MAX_REQUEST_BODY = 16 * 1024

_registered = False
_preview_last_render: dict[str, float] = {}
_SINGLE_SLOT_COLUMNS = {
    "background": "background_item_id",
    "card_color": "color_item_id",
    "card_template": "template_item_id",
    "flag": "flag_item_id",
}
_BADGE_SLOTS = tuple(f"badge_{index}" for index in range(1, 6))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(value: str) -> str:
    return profile_store_db.hash_session_token(value)


def _json_object(value) -> dict:
    try:
        parsed = json.loads(value or "{}") if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_https_url(value) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return value.strip()


def _public_item(row, *, owned: bool, equipped: bool) -> dict:
    item = dict(row)
    style = _json_object(item.pop("style_json", "{}"))
    metadata = _json_object(item.pop("metadata_json", "{}"))
    asset_path = item.pop("asset_path", None)
    asset_url = _safe_https_url(item.pop("asset_url", None))
    item.pop("created_by", None)
    item.pop("created_at", None)
    item.pop("updated_at", None)
    item.pop("deleted_at", None)
    item["item_id"] = int(item["item_id"])
    item["price"] = max(0, int(item.get("price") or 0))
    item["is_featured"] = bool(item.get("is_featured"))
    item["owned"] = bool(owned)
    item["equipped"] = bool(equipped)
    item["style"] = style
    item["metadata"] = metadata
    item["asset"] = f"/api/profile-store/assets/{item['item_id']}" if asset_path else asset_url
    item["has_asset"] = bool(asset_path or asset_url)
    return item


def _origin_matches(request) -> bool:
    origin = request.headers.get("Origin", "")
    if not origin:
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    forwarded_host = request.headers.get("X-Forwarded-Host", "")
    host = (forwarded_host.split(",", 1)[0].strip() if forwarded_host else request.host).lower()
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    scheme = (forwarded_proto.split(",", 1)[0].strip().lower() if forwarded_proto else request.scheme.lower())
    return (
        parsed.scheme.lower() == scheme
        and parsed.netloc.lower() == host
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


def _is_secure_request(request) -> bool:
    forwarded = request.headers.get("X-Forwarded-Proto", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip().lower() == "https"
    return bool(request.secure)


def _error(status: int, code: str, message: str | None = None) -> web.Response:
    payload = {"error": code}
    if message:
        payload["message"] = message
    return web.json_response(payload, status=status, headers={"Cache-Control": "no-store"})


async def _load_session(request):
    raw = request.cookies.get(SESSION_COOKIE, "")
    if not isinstance(raw, str) or not raw or len(raw) > 512:
        return None
    now = _now_iso()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT session_hash, csrf_hash, guild_id, user_id, created_at, expires_at
            FROM profile_store_web_sessions
            WHERE session_hash = ? AND expires_at > ? AND revoked_at IS NULL
            """,
            (_digest(raw), now),
        ) as cursor:
            row = await cursor.fetchone()
    return dict(row) if row else None


async def _authorize_request(request, *, write: bool = False):
    session = await _load_session(request)
    if not session:
        return None, _error(401, "store_session_expired", "افتح المتجر من أمر /profile داخل Discord لإنشاء رابط جديد.")

    if write:
        csrf_cookie = request.cookies.get(CSRF_COOKIE, "")
        csrf_header = request.headers.get("X-CSRF-Token", "")
        if (
            not csrf_cookie
            or not csrf_header
            or not secrets.compare_digest(csrf_cookie, csrf_header)
            or not secrets.compare_digest(_digest(csrf_cookie), session["csrf_hash"])
            or not _origin_matches(request)
        ):
            return None, _error(403, "csrf", "تعذر تأكيد الطلب الآمن. حدّث الصفحة ثم حاول مجدداً.")

    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT enabled FROM profile_store_settings WHERE settings_id = 1"
        ) as cursor:
            settings = await cursor.fetchone()
    if settings and not int(settings["enabled"]):
        return None, _error(423, "store_disabled", "متجر PRIME متوقف مؤقتاً.")
    return session, None


async def _exchange_link_token(request, raw_token: str):
    if not raw_token or len(raw_token) > 512:
        return None
    now = _now_iso()
    cookie_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    token_hash = _digest(raw_token)
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT session_id, guild_id, user_id, expires_at
                FROM profile_store_sessions
                WHERE token_hash = ? AND consumed_at IS NULL
                  AND revoked_at IS NULL AND expires_at > ?
                """,
                (token_hash, now),
            ) as cursor:
                source = await cursor.fetchone()
            if not source:
                await db.rollback()
                return None

            async with db.execute(
                "SELECT enabled FROM profile_store_settings WHERE settings_id = 1"
            ) as cursor:
                settings = await cursor.fetchone()
            if settings and not int(settings["enabled"]):
                await db.rollback()
                return "disabled"

            cursor = await db.execute(
                """
                UPDATE profile_store_sessions SET consumed_at = ?
                WHERE session_id = ? AND consumed_at IS NULL AND revoked_at IS NULL
                """,
                (now, int(source["session_id"])),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                return None

            await db.execute(
                "DELETE FROM profile_store_web_sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL",
                (now,),
            )
            await db.execute(
                """
                INSERT INTO profile_store_web_sessions
                    (session_hash, csrf_hash, guild_id, user_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _digest(cookie_token), _digest(csrf_token),
                    int(source["guild_id"]), int(source["user_id"]),
                    now, source["expires_at"],
                ),
            )
            await db.commit()
            expires_at = datetime.fromisoformat(str(source["expires_at"]).replace("Z", "+00:00"))
            seconds = max(1, min(900, int((expires_at - datetime.now(timezone.utc)).total_seconds())))
            return cookie_token, csrf_token, seconds
        except BaseException:
            await db.rollback()
            raise


async def _store_state(request, session) -> dict:
    guild_id, user_id = int(session["guild_id"]), int(session["user_id"])
    async with database.connect(aiosqlite.Row) as db:
        # Never create an economy user while merely opening the store: the legacy
        # users table defaults new wallets to 100 coins, which would mint currency.
        await db.execute(
            """
            INSERT OR IGNORE INTO user_profile_gem_wallets (guild_id, user_id, balance)
            VALUES (?, ?, 0)
            """,
            (guild_id, user_id),
        )
        async with db.execute(
            "SELECT balance, bank FROM users WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cursor:
            wallet_row = await cursor.fetchone()
        wallet_data = dict(wallet_row) if wallet_row else {"balance": 0, "bank": 0}
        async with db.execute(
            "SELECT balance FROM user_profile_gem_wallets WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as cursor:
            gem_row = await cursor.fetchone()
        gem_data = dict(gem_row) if gem_row else {"balance": 0}

        async with db.execute(
            """
            SELECT i.*,
                   CASE WHEN inv.item_id IS NULL THEN 0 ELSE 1 END AS is_owned
            FROM profile_store_items AS i
            LEFT JOIN user_profile_inventory AS inv
              ON inv.guild_id = ? AND inv.user_id = ? AND inv.item_id = i.item_id
            WHERE i.is_active = 1 AND i.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM profile_store_categories AS parent
                  WHERE parent.parent_id IS NULL
                    AND parent.name = i.category COLLATE NOCASE
                    AND parent.is_active = 0
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM profile_store_categories AS child
                  JOIN profile_store_categories AS parent
                    ON parent.category_id = child.parent_id
                  WHERE child.name = i.subcategory COLLATE NOCASE
                    AND parent.name = i.category COLLATE NOCASE
                    AND child.is_active = 0
              )
            ORDER BY i.is_featured DESC, i.item_type ASC, i.price ASC, i.item_id ASC
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()

        async with db.execute(
            """
            SELECT background_item_id, template_item_id, color_item_id, flag_item_id
            FROM user_profile_card_settings WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ) as cursor:
            settings_row = await cursor.fetchone()
        async with db.execute(
            """
            SELECT category_id, name, parent_id, sort_order
            FROM profile_store_categories
            WHERE is_active = 1
            ORDER BY COALESCE(parent_id, 0), sort_order, category_id
            """
        ) as cursor:
            category_rows = await cursor.fetchall()
        settings = dict(settings_row) if settings_row else {}
        async with db.execute(
            """
            SELECT item_id, slot_key FROM user_profile_equipped_items
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ) as cursor:
            equipped_rows = await cursor.fetchall()
        equipped_ids = {int(row["item_id"]) for row in equipped_rows}
        equipped_slots = {str(row["slot_key"]): int(row["item_id"]) for row in equipped_rows}
        await db.commit()

    for column in ("background_item_id", "template_item_id", "color_item_id", "flag_item_id"):
        if settings.get(column):
            equipped_ids.add(int(settings[column]))

    catalog = []
    for row in rows:
        item = dict(row)
        owned = bool(item.pop("is_owned", 0))
        catalog.append(_public_item(item, owned=owned, equipped=int(item["item_id"]) in equipped_ids))

    bot = request.app.get("bot") if hasattr(request, "app") else None
    guild = None
    member = None
    user = None
    try:
        guild = bot.get_guild(guild_id) if bot else None
        member = guild.get_member(user_id) if guild else None
        user = member or (bot.get_user(user_id) if bot else None)
    except Exception:
        logger.debug("Could not resolve cached Discord identity for the PRIME storefront")
    avatar_url = None
    if user:
        try:
            avatar_url = str(user.display_avatar.url)
        except Exception:
            avatar_url = None
    if not _safe_https_url(avatar_url) or not urlsplit(avatar_url).hostname in {"cdn.discordapp.com", "media.discordapp.net"}:
        avatar_url = "https://cdn.discordapp.com/embed/avatars/0.png"

    return {
        "brand": "PRIME",
        "guild": {"id": str(guild_id), "name": str(getattr(guild, "name", None) or "سيرفر Discord")},
        "user": {
            "id": str(user_id),
            "name": str(getattr(member, "display_name", None) or getattr(user, "name", None) or "عضو PRIME"),
            "username": str(getattr(user, "name", None) or user_id),
            "avatar_url": avatar_url,
            "expires_at": session["expires_at"],
        },
        "wallet": {
            "coins": int(wallet_data.get("balance") or 0),
            "bank": int(wallet_data.get("bank") or 0),
            "gems": int(gem_data.get("balance") or 0),
        },
        "equipped": {
            "background_item_id": settings.get("background_item_id"),
            "template_item_id": settings.get("template_item_id"),
            "color_item_id": settings.get("color_item_id"),
            "flag_item_id": settings.get("flag_item_id"),
            "slots": equipped_slots,
        },
        "categories": [
            {
                "category_id": int(row["category_id"]),
                "name": str(row["name"]),
                "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
                "sort_order": int(row["sort_order"]),
            }
            for row in category_rows
        ],
        "items": catalog,
    }


async def _read_json(request) -> dict:
    if request.content_type != "application/json":
        raise ValueError("invalid_content_type")
    if request.content_length is not None and request.content_length > MAX_REQUEST_BODY:
        raise ValueError("request_too_large")
    try:
        try:
            raw = await request.content.readexactly(MAX_REQUEST_BODY + 1)
        except asyncio.IncompleteReadError as error:
            raw = error.partial
        if len(raw) > MAX_REQUEST_BODY:
            raise ValueError("request_too_large")
        body = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("invalid_json") from error
    if not isinstance(body, dict):
        raise ValueError("request_body_must_be_object")
    return body


def _resolve_asset_path(raw_path) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip() or "\\" in raw_path:
        return None
    relative = Path(raw_path.strip())
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        return None
    if raw_path.startswith("assets/profile_templates/"):
        approved_root = (ROOT / "assets" / "profile_templates").resolve()
        candidate = (ROOT / relative).resolve()
        trusted_root = approved_root
    else:
        try:
            from profile_store_admin import profile_asset_root
            trusted_root = profile_asset_root().resolve()
        except (ImportError, OSError, ValueError):
            return None
        candidate = (trusted_root / relative).resolve()

    if trusted_root not in candidate.parents or candidate.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return None
    return candidate if candidate.is_file() else None


def _apply_preview_item(configuration: dict, item: dict) -> dict:
    """Apply a cosmetic to a copy of the current card configuration without saving it."""
    result = dict(configuration or {})
    item_type = str(item.get("item_type") or "")
    if item_type == "card_template":
        result["template"] = item
    elif item_type == "background":
        result["background"] = item
    elif item_type == "card_color":
        result["color_style"] = dict(item.get("style") or {})
    elif item_type == "flag":
        result["flag"] = item
    elif item_type in {"badge", "shape", "club_logo"}:
        equipped = list(result.get("badges") or [])
        equipped = [existing for existing in equipped if int(existing.get("item_id") or 0) != int(item.get("item_id") or 0)]
        result["badges"] = (equipped[:4] + [item]) if len(equipped) >= 5 else equipped + [item]
    else:
        raise StoreActionError(400, "unsupported_item_type", "هذا العنصر لا يدعم معاينة بطاقة البروفايل.")

    result["preview_item_id"] = int(item.get("item_id") or 0)
    return result


async def _render_profile_preview(request, session, item_id: int) -> bytes:
    """Render the selected cosmetic through PRIME's real profile-card renderer without persisting it."""
    bot = request.app.get("bot") if hasattr(request, "app") else None
    if bot is None:
        raise StoreActionError(503, "bot_unavailable", "البوت غير متصل حالياً؛ جرّب المعاينة لاحقاً.")

    guild_id, user_id = int(session["guild_id"]), int(session["user_id"])
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise StoreActionError(503, "guild_unavailable", "تعذر الوصول إلى السيرفر من البوت. تأكد أن البوت متصل.")

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except Exception as error:
            logger.info("Profile store preview could not fetch guild member: %s", type(error).__name__)
            raise StoreActionError(404, "member_unavailable", "تعذر العثور على حسابك داخل السيرفر.") from error
    if getattr(member, "bot", False):
        raise StoreActionError(400, "member_unavailable", "معاينة البروفايل غير متاحة لحسابات البوتات.")

    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT * FROM profile_store_items
            WHERE item_id = ? AND is_active = 1 AND deleted_at IS NULL
            """,
            (item_id,),
        ) as cursor:
            item_row = await cursor.fetchone()
    if not item_row:
        raise StoreActionError(404, "item_not_found", "العنصر لم يعد متاحاً في المتجر.")

    item = dict(item_row)
    item["style"] = _json_object(item.pop("style_json", "{}"))
    item["metadata"] = _json_object(item.pop("metadata_json", "{}"))

    profile, configuration = await build_profile_card_data(guild, member)
    configuration = _apply_preview_item(configuration, item)


    from profile_card_generator import generate_profile_card

    image = await generate_profile_card(member, profile, configuration)
    try:
        return image.getvalue()
    finally:
        image.close()


def _valid_item_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        item_id = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return item_id if item_id > 0 else None


async def _purchase_item(session, item_id: int) -> dict:
    guild_id, user_id = int(session["guild_id"]), int(session["user_id"])
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT item_id, slug, name, item_type, price, currency
                FROM profile_store_items AS i
                WHERE i.item_id = ? AND i.is_active = 1 AND i.deleted_at IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM profile_store_categories AS parent
                      WHERE parent.parent_id IS NULL
                        AND parent.name = i.category COLLATE NOCASE
                        AND parent.is_active = 0
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM profile_store_categories AS child
                      JOIN profile_store_categories AS parent
                        ON parent.category_id = child.parent_id
                      WHERE child.name = i.subcategory COLLATE NOCASE
                        AND parent.name = i.category COLLATE NOCASE
                        AND child.is_active = 0
                  )
                """,
                (item_id,),
            ) as cursor:
                item = await cursor.fetchone()
            if not item:
                await db.rollback()
                raise StoreActionError(404, "item_not_found", "هذا العنصر لم يعد متاحاً.")

            async with db.execute(
                """
                SELECT 1 FROM user_profile_inventory
                WHERE guild_id = ? AND user_id = ? AND item_id = ?
                """,
                (guild_id, user_id, item_id),
            ) as cursor:
                if await cursor.fetchone():
                    await db.rollback()
                    raise StoreActionError(409, "already_owned", "هذا العنصر موجود في مقتنياتك بالفعل.")

            currency = str(item["currency"])
            price = int(item["price"])
            if price < 0 or currency not in profile_store_db.STORE_CURRENCIES:
                await db.rollback()
                raise StoreActionError(409, "invalid_catalog_item", "بيانات سعر العنصر غير صالحة.")

            if currency == "moon":
                async with db.execute(
                    "SELECT balance FROM users WHERE user_id = ? AND guild_id = ?",
                    (user_id, guild_id),
                ) as cursor:
                    wallet = await cursor.fetchone()
                balance = int(wallet["balance"] or 0) if wallet else 0
                if balance < price:
                    await db.rollback()
                    raise StoreActionError(402, "insufficient_coins", "رصيد عملات PRIME لا يكفي لهذا العنصر.")
                # A free item must not create a legacy economy row or grant its
                # 100-coin schema default just because a member opened the shop.
                if price > 0:
                    cursor = await db.execute(
                        """
                        UPDATE users SET balance = balance - ?
                        WHERE user_id = ? AND guild_id = ? AND balance >= ?
                        """,
                        (price, user_id, guild_id, price),
                    )
                    if cursor.rowcount != 1:
                        await db.rollback()
                        raise StoreActionError(409, "wallet_changed", "تغيّر رصيدك أثناء الشراء. حدّث الصفحة وحاول مجدداً.")
                balance_after = balance - price
            else:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO user_profile_gem_wallets (guild_id, user_id, balance)
                    VALUES (?, ?, 0)
                    """,
                    (guild_id, user_id),
                )
                async with db.execute(
                    "SELECT balance FROM user_profile_gem_wallets WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                ) as cursor:
                    wallet = await cursor.fetchone()
                balance = int(wallet["balance"] or 0) if wallet else 0
                if balance < price:
                    await db.rollback()
                    raise StoreActionError(402, "insufficient_gems", "رصيد بلورات PRIME لا يكفي لهذا العنصر.")
                cursor = await db.execute(
                    """
                    UPDATE user_profile_gem_wallets
                    SET balance = balance - ?, updated_at = CURRENT_TIMESTAMP
                    WHERE guild_id = ? AND user_id = ? AND balance >= ?
                    """,
                    (price, guild_id, user_id, price),
                )
                if cursor.rowcount != 1:
                    await db.rollback()
                    raise StoreActionError(409, "wallet_changed", "تغيّر رصيدك أثناء الشراء. حدّث الصفحة وحاول مجدداً.")
                balance_after = balance - price

            await db.execute(
                """
                INSERT INTO user_profile_inventory
                    (guild_id, user_id, item_id, price_paid, currency, acquisition_source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (guild_id, user_id, item_id, price, currency, "free" if price == 0 else "purchase"),
            )
            await db.execute(
                """
                INSERT INTO profile_store_transactions
                    (guild_id, user_id, item_id, transaction_type, currency, delta, balance_after, reason)
                VALUES (?, ?, ?, 'purchase', ?, ?, ?, ?)
                """,
                (
                    guild_id, user_id, item_id, currency, -price, balance_after,
                    f"purchase:{str(item['slug'])[:80]}",
                ),
            )
            await db.commit()
            return {"item_id": item_id, "name": str(item["name"]), "price": price, "currency": currency, "balance": balance_after}
        except StoreActionError:
            raise
        except BaseException:
            await db.rollback()
            raise


class StoreActionError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def _equip_item(session, item_id: int, requested_slot: str | None = None) -> dict:
    guild_id, user_id = int(session["guild_id"]), int(session["user_id"])
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                """
                SELECT item_id, name, item_type FROM profile_store_items
                WHERE item_id = ? AND is_active = 1 AND deleted_at IS NULL
                """,
                (item_id,),
            ) as cursor:
                item = await cursor.fetchone()
            if not item:
                await db.rollback()
                raise StoreActionError(404, "item_not_found", "هذا العنصر لم يعد متاحاً.")
            async with db.execute(
                """
                SELECT 1 FROM user_profile_inventory
                WHERE guild_id = ? AND user_id = ? AND item_id = ?
                """,
                (guild_id, user_id, item_id),
            ) as cursor:
                if not await cursor.fetchone():
                    await db.rollback()
                    raise StoreActionError(403, "item_not_owned", "اشترِ العنصر أولاً قبل تجهيزه.")

            item_type = str(item["item_type"])
            if item_type in _SINGLE_SLOT_COLUMNS:
                column = _SINGLE_SLOT_COLUMNS[item_type]
                await db.execute(
                    """
                    INSERT OR IGNORE INTO user_profile_card_settings (guild_id, user_id)
                    VALUES (?, ?)
                    """,
                    (guild_id, user_id),
                )
                await db.execute(
                    f"UPDATE user_profile_card_settings SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ? AND user_id = ?",
                    (item_id, guild_id, user_id),
                )
                slot_key = column
            elif item_type in {"badge", "shape", "club_logo"}:
                if item_type == "badge":
                    slot_key = requested_slot if requested_slot in _BADGE_SLOTS else None
                    async with db.execute(
                        """
                        SELECT slot_key FROM user_profile_equipped_items
                        WHERE guild_id = ? AND user_id = ? AND slot_key IN ('badge_1','badge_2','badge_3','badge_4','badge_5')
                        """,
                        (guild_id, user_id),
                    ) as cursor:
                        occupied = {str(row["slot_key"]) for row in await cursor.fetchall()}
                    if slot_key is None:
                        slot_key = next((candidate for candidate in _BADGE_SLOTS if candidate not in occupied), None)
                    if slot_key is None:
                        await db.rollback()
                        raise StoreActionError(
                            409, "badge_slots_full",
                            "خانات الشارات الخمس ممتلئة. أزل شارة موجودة ثم جهّز الجديدة.",
                        )
                else:
                    slot_key = item_type
                await db.execute(
                    """
                    DELETE FROM user_profile_equipped_items
                    WHERE guild_id = ? AND user_id = ? AND item_id = ?
                    """,
                    (guild_id, user_id, item_id),
                )
                await db.execute(
                    """
                    INSERT INTO user_profile_equipped_items
                        (guild_id, user_id, slot_key, item_id)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(guild_id, user_id, slot_key)
                    DO UPDATE SET item_id = excluded.item_id, equipped_at = CURRENT_TIMESTAMP
                    """,
                    (guild_id, user_id, slot_key, item_id),
                )
            else:
                await db.rollback()
                raise StoreActionError(400, "unsupported_item_type", "هذا النوع لا يدعم التجهيز حالياً.")
            await db.commit()
            return {"item_id": item_id, "name": str(item["name"]), "slot_key": slot_key}
        except StoreActionError:
            raise
        except aiosqlite.IntegrityError as error:
            await db.rollback()
            raise StoreActionError(409, "equip_conflict", "تعذر تجهيز العنصر بسبب تعارض مع تجهيز موجود.") from error
        except BaseException:
            await db.rollback()
            raise


async def _unequip_item(session, item_id: int) -> dict:
    guild_id, user_id = int(session["guild_id"]), int(session["user_id"])
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            async with db.execute(
                "SELECT item_id, name, item_type FROM profile_store_items WHERE item_id = ?",
                (item_id,),
            ) as cursor:
                item = await cursor.fetchone()
            if not item:
                await db.rollback()
                raise StoreActionError(404, "item_not_found", "العنصر غير موجود.")
            item_type = str(item["item_type"])
            column = _SINGLE_SLOT_COLUMNS.get(item_type)
            if column:
                await db.execute(
                    f"UPDATE user_profile_card_settings SET {column} = NULL, updated_at = CURRENT_TIMESTAMP WHERE guild_id = ? AND user_id = ? AND {column} = ?",
                    (guild_id, user_id, item_id),
                )
            else:
                await db.execute(
                    "DELETE FROM user_profile_equipped_items WHERE guild_id = ? AND user_id = ? AND item_id = ?",
                    (guild_id, user_id, item_id),
                )
            await db.commit()
            return {"item_id": item_id, "name": str(item["name"])}
        except StoreActionError:
            raise
        except BaseException:
            await db.rollback()
            raise


def register_profile_store_public_routes(routes) -> None:
    """Register the public shop, one-time session exchange, and guarded purchase APIs."""
    global _registered
    if _registered:
        return

    @routes.get("/profile-store")
    async def profile_store_page(request):
        raw_token = request.query.get("token", "")
        if raw_token:
            try:
                exchange = await _exchange_link_token(request, raw_token)
            except Exception:
                logger.exception("Profile store link exchange failed")
                exchange = None
            if exchange == "disabled":
                raise web.HTTPFound("/profile-store?link=disabled")
            if not exchange:
                raise web.HTTPFound("/profile-store?link=invalid")
            cookie_token, csrf_token, max_age = exchange
            response = web.HTTPFound("/profile-store")
            response.set_cookie(
                SESSION_COOKIE, cookie_token, max_age=max_age, path="/",
                httponly=True, secure=_is_secure_request(request), samesite="Lax",
            )
            response.set_cookie(
                CSRF_COOKIE, csrf_token, max_age=max_age, path="/",
                httponly=False, secure=_is_secure_request(request), samesite="Lax",
            )
            return response
        page = DASHBOARD_DIR / "profile-store.html"
        if not page.is_file():
            raise web.HTTPServiceUnavailable(text="PRIME storefront is not installed")
        return web.FileResponse(
            page,
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            },
        )

    @routes.get("/profile-store/")
    async def profile_store_trailing_slash(request):
        raise web.HTTPFound("/profile-store")

    @routes.get("/profile-store/app.css")
    async def profile_store_css(request):
        path = DASHBOARD_DIR / "profile-store.css"
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=300"})

    @routes.get("/profile-store/app.js")
    async def profile_store_js(request):
        path = DASHBOARD_DIR / "profile-store.js"
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=300"})

    @routes.get("/api/profile-store/state")
    async def profile_store_state(request):
        session, failure = await _authorize_request(request)
        if failure:
            return failure
        try:
            return web.json_response(await _store_state(request, session), headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Could not load PRIME profile storefront state")
            return _error(500, "store_state_failed", "تعذر تحميل المتجر. حاول تحديث الصفحة.")

    @routes.get("/api/profile-store/preview/{item_id}")
    async def profile_store_preview(request):
        session, failure = await _authorize_request(request)
        if failure:
            return failure
        item_id = _valid_item_id(request.match_info.get("item_id"))
        if not item_id:
            raise web.HTTPNotFound()
        session_key = str(session["session_hash"])
        now_mono = time.monotonic()
        last_render = _preview_last_render.get(session_key, 0.0)
        if now_mono - last_render < 1.5:
            return _error(429, "preview_rate_limited", "انتظر لحظة قصيرة قبل إنشاء معاينة أخرى.")
        _preview_last_render[session_key] = now_mono
        if len(_preview_last_render) > 2048:
            cutoff = now_mono - 60.0
            for key, stamped in list(_preview_last_render.items()):
                if stamped < cutoff:
                    _preview_last_render.pop(key, None)
        try:
            image_bytes = await _render_profile_preview(request, session, item_id)
            return web.Response(
                body=image_bytes,
                content_type="image/png",
                headers={
                    "Cache-Control": "no-store, private",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "default-src 'none'; sandbox",
                },
            )
        except StoreActionError as error:
            return _error(error.status, error.code, error.message)
        except Exception:
            logger.exception("PRIME real profile-card preview failed")
            return _error(500, "preview_failed", "تعذر إنشاء المعاينة الحقيقية. لم يتم حفظ أي تغيير.")

    @routes.get("/api/profile-store/assets/{item_id}")
    async def profile_store_asset(request):
        session, failure = await _authorize_request(request)
        if failure:
            return failure
        item_id = _valid_item_id(request.match_info.get("item_id"))
        if not item_id:
            raise web.HTTPNotFound()
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                """
                SELECT asset_path FROM profile_store_items
                WHERE item_id = ? AND is_active = 1 AND deleted_at IS NULL
                """,
                (item_id,),
            ) as cursor:
                row = await cursor.fetchone()
        candidate = _resolve_asset_path(row["asset_path"]) if row and row["asset_path"] else None
        if not candidate:
            raise web.HTTPNotFound()
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return web.FileResponse(candidate, headers={"Cache-Control": "private, max-age=300", "Content-Type": mime})

    @routes.post("/api/profile-store/purchase")
    async def profile_store_purchase(request):
        session, failure = await _authorize_request(request, write=True)
        if failure:
            return failure
        try:
            body = await _read_json(request)
            item_id = _valid_item_id(body.get("item_id"))
            if not item_id:
                return _error(400, "invalid_item_id", "العنصر المحدد غير صالح.")
            result = await _purchase_item(session, item_id)
            return web.json_response({"ok": True, "purchase": result}, headers={"Cache-Control": "no-store"})
        except StoreActionError as error:
            return _error(error.status, error.code, error.message)
        except ValueError as error:
            return _error(400, str(error))
        except Exception:
            logger.exception("PRIME profile store purchase failed")
            return _error(500, "purchase_failed", "تعذر إتمام الشراء؛ لم نعرض نجاحاً غير مؤكد.")

    @routes.post("/api/profile-store/equip")
    async def profile_store_equip(request):
        session, failure = await _authorize_request(request, write=True)
        if failure:
            return failure
        try:
            body = await _read_json(request)
            item_id = _valid_item_id(body.get("item_id"))
            if not item_id:
                return _error(400, "invalid_item_id", "العنصر المحدد غير صالح.")
            requested_slot = body.get("slot_key")
            if requested_slot is not None and not isinstance(requested_slot, str):
                return _error(400, "invalid_slot", "موضع التجهيز غير صالح.")
            result = await _equip_item(session, item_id, requested_slot)
            return web.json_response({"ok": True, "equipped": result}, headers={"Cache-Control": "no-store"})
        except StoreActionError as error:
            return _error(error.status, error.code, error.message)
        except ValueError as error:
            return _error(400, str(error))
        except Exception:
            logger.exception("PRIME profile store equip failed")
            return _error(500, "equip_failed", "تعذر تجهيز العنصر. جرّب مرة أخرى.")

    @routes.post("/api/profile-store/unequip")
    async def profile_store_unequip(request):
        session, failure = await _authorize_request(request, write=True)
        if failure:
            return failure
        try:
            body = await _read_json(request)
            item_id = _valid_item_id(body.get("item_id"))
            if not item_id:
                return _error(400, "invalid_item_id", "العنصر المحدد غير صالح.")
            result = await _unequip_item(session, item_id)
            return web.json_response({"ok": True, "unequipped": result}, headers={"Cache-Control": "no-store"})
        except StoreActionError as error:
            return _error(error.status, error.code, error.message)
        except ValueError as error:
            return _error(400, str(error))
        except Exception:
            logger.exception("PRIME profile store unequip failed")
            return _error(500, "unequip_failed", "تعذر إزالة التجهيز. جرّب مرة أخرى.")

    @routes.post("/api/profile-store/logout")
    async def profile_store_logout(request):
        session, failure = await _authorize_request(request, write=True)
        if failure:
            response = failure
        else:
            raw = request.cookies.get(SESSION_COOKIE, "")
            async with database.connect() as db:
                await db.execute(
                    "UPDATE profile_store_web_sessions SET revoked_at = ? WHERE session_hash = ?",
                    (_now_iso(), _digest(raw)),
                )
                await db.commit()
            response = web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})
        response.del_cookie(SESSION_COOKIE, path="/")
        response.del_cookie(CSRF_COOKIE, path="/")
        return response

    _registered = True
