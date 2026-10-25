"""Admin API for PRIME profile-store items, upload settings, and static image validation.

The app remains on PRIME's existing aiohttp server and session/CSRF authorization.
Catalog assets are stored outside the web root, next to the configured SQLite DB.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import uuid
import warnings
from pathlib import Path
from typing import Any

import aiosqlite
from aiohttp import web
from PIL import Image, ImageOps, UnidentifiedImageError

import database
import profile_store_db

logger = logging.getLogger("ProfileStoreAdminAPI")

MAX_PROFILE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PROFILE_UPLOAD_PIXELS = 16_000_000
MAX_STYLE_JSON_BYTES = 12 * 1024
MAX_CATALOG_ITEMS = 500
ALLOWED_UPLOAD_MIME = {
    "PNG": {"image/png"},
    "JPEG": {"image/jpeg", "image/jpg"},
    "WEBP": {"image/webp"},
}
ALLOWED_UPLOAD_EXT = {
    "PNG": {".png"},
    "JPEG": {".jpg", ".jpeg"},
    "WEBP": {".webp"},
}
MIME_TO_FORMAT = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/webp": "WEBP",
}
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,78}[a-z0-9])?$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CATEGORY_RE = re.compile(r"^[\w -]{1,80}$", re.UNICODE)
_ASSET_ITEM_TYPES = {"background", "flag", "badge", "shape", "club_logo"}
_registered = False


def validate_static_image(
    data: bytes,
    *,
    declared_mime: str,
    filename: str,
    max_bytes: int = MAX_PROFILE_UPLOAD_BYTES,
) -> dict[str, Any]:
    """Validate and sanitize a single-frame PNG/JPEG/WebP image.

    Never trust filename or browser MIME alone. Pillow verifies the real format,
    frame count, animation flag and pixel dimensions. The returned bytes are
    re-encoded to the detected static format, stripping metadata.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("empty_image")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("invalid_size_limit")
    if len(data) > min(max_bytes, MAX_PROFILE_UPLOAD_BYTES):
        raise ValueError("image_too_large")

    safe_name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("unsupported_image_extension")

    mime = str(declared_mime or "").split(";", 1)[0].strip().lower()
    actual_format = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_format = str(image.format or "").upper()
                if actual_format not in ALLOWED_UPLOAD_MIME:
                    raise ValueError("unsupported_image_format")
                if bool(getattr(image, "is_animated", False)) or int(
                    getattr(image, "n_frames", 1) or 1
                ) != 1:
                    raise ValueError("animated_images_not_allowed")
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("invalid_image_dimensions")
                if image.width * image.height > MAX_PROFILE_UPLOAD_PIXELS:
                    raise ValueError("image_pixel_limit")
                if suffix not in ALLOWED_UPLOAD_EXT[actual_format]:
                    raise ValueError("image_extension_mismatch")
                if mime not in ALLOWED_UPLOAD_MIME[actual_format]:
                    raise ValueError("image_mime_mismatch")
                image.verify()

            with Image.open(io.BytesIO(data)) as image:
                if bool(getattr(image, "is_animated", False)) or int(
                    getattr(image, "n_frames", 1) or 1
                ) != 1:
                    raise ValueError("animated_images_not_allowed")
                image.load()
                if actual_format == "JPEG":
                    clean = ImageOps.exif_transpose(image).convert("RGB")
                elif actual_format == "PNG":
                    clean = ImageOps.exif_transpose(image)
                    clean = clean.convert("RGBA" if "A" in clean.getbands() else "RGB")
                else:
                    clean = ImageOps.exif_transpose(image)
                    clean = clean.convert("RGBA" if "A" in clean.getbands() else "RGB")

                output = io.BytesIO()
                save_options: dict[str, Any] = {}
                if actual_format == "PNG":
                    save_options["optimize"] = True
                elif actual_format == "JPEG":
                    save_options.update(quality=90, optimize=True, progressive=True)
                elif actual_format == "WEBP":
                    save_options.update(quality=90, method=4)
                clean.save(output, format=actual_format, **save_options)
                sanitized = output.getvalue()
                if not sanitized or len(sanitized) > min(max_bytes, MAX_PROFILE_UPLOAD_BYTES):
                    raise ValueError("sanitized_image_too_large")
                return {
                    "data": sanitized,
                    "format": actual_format,
                    "mime": {
                        "PNG": "image/png",
                        "JPEG": "image/jpeg",
                        "WEBP": "image/webp",
                    }[actual_format],
                    "extension": {
                        "PNG": ".png",
                        "JPEG": ".jpg",
                        "WEBP": ".webp",
                    }[actual_format],
                    "width": int(clean.width),
                    "height": int(clean.height),
                    "size": len(sanitized),
                }
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("image_pixel_limit") from error
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("invalid_image_file") from error


def profile_asset_root() -> Path:
    """Use a private storage directory outside the public dashboard/static tree."""
    configured = (os.environ.get("PROFILE_STORE_ASSET_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    db_path = (os.environ.get("DB_PATH") or "").strip()
    if not db_path:
        db_path = getattr(database, "DB_NAME", "data/bot.db")
    return (Path(db_path).expanduser().resolve().parent / "profile_store_assets").resolve()


def _json_object(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field}_must_be_object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field}_invalid_json") from error
    if len(encoded.encode("utf-8")) > MAX_STYLE_JSON_BYTES:
        raise ValueError(f"{field}_too_large")
    return value


def _payload_text(payload: dict, key: str, default: str, max_length: int, *, required=False) -> str:
    raw = payload.get(key, default)
    if raw is None:
        raw = default
    if not isinstance(raw, str):
        raise ValueError(f"{key}_must_be_string")
    value = raw.strip()
    if required and not value:
        raise ValueError(f"{key}_required")
    if len(value) > max_length:
        raise ValueError(f"{key}_too_long")
    return value


def validate_catalog_item_payload(payload: dict) -> dict:
    """Validate all admin-supplied item metadata; asset_path is server-controlled."""
    if not isinstance(payload, dict):
        raise ValueError("item_must_be_object")

    slug = _payload_text(payload, "slug", "", 80, required=True).lower()
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("invalid_slug")
    name = _payload_text(payload, "name", "", 100, required=True)
    description = _payload_text(payload, "description", "", 1000)
    item_type = _payload_text(payload, "item_type", "", 24, required=True)
    if item_type not in profile_store_db.STORE_ITEM_TYPES:
        raise ValueError("invalid_item_type")

    category = _payload_text(payload, "category", "all", 80)
    subcategory = _payload_text(payload, "subcategory", "", 80)
    if category and not _CATEGORY_RE.fullmatch(category):
        raise ValueError("invalid_category")
    if subcategory and not _CATEGORY_RE.fullmatch(subcategory):
        raise ValueError("invalid_subcategory")

    rarity = _payload_text(payload, "rarity", "common", 16)
    if rarity not in profile_store_db.STORE_RARITIES:
        raise ValueError("invalid_rarity")

    price = payload.get("price", 0)
    if isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= 1_000_000_000:
        raise ValueError("invalid_price")
    currency = _payload_text(payload, "currency", "moon", 8)
    if currency not in profile_store_db.STORE_CURRENCIES:
        raise ValueError("invalid_currency")

    style = _json_object(payload.get("style", {}), "style")
    metadata = _json_object(payload.get("metadata", {}), "metadata")
    for key in ("accent", "accent2", "divider", "xp", "deep", "glow"):
        if key in style and (not isinstance(style[key], str) or not COLOR_RE.fullmatch(style[key])):
            raise ValueError(f"invalid_style_{key}")

    is_active = payload.get("is_active", True)
    is_featured = payload.get("is_featured", False)
    if not isinstance(is_active, bool) or not isinstance(is_featured, bool):
        raise ValueError("boolean_flags_required")

    return {
        "slug": slug,
        "name": name,
        "description": description,
        "item_type": item_type,
        "category": category or "all",
        "subcategory": subcategory,
        "rarity": rarity,
        "price": price,
        "currency": currency,
        "style_json": json.dumps(style, ensure_ascii=False, separators=(",", ":")),
        "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        "is_active": int(is_active),
        "is_featured": int(is_featured),
    }


def _parse_json_column(value: Any) -> dict:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _public_item(row) -> dict:
    item = dict(row)
    item["style"] = _parse_json_column(item.pop("style_json", "{}"))
    item["metadata"] = _parse_json_column(item.pop("metadata_json", "{}"))
    path = item.pop("asset_path", None)
    item["has_asset"] = bool(path)
    item["asset_name"] = Path(path).name if path else None
    item.pop("created_by", None)
    return item


def _configured_profile_store_admin_ids() -> set[int]:
    """Read explicit bot-wide store administrators; never infer them from a guild role."""
    result: set[int] = set()
    for key in ("PROFILE_STORE_ADMIN_IDS", "BOT_OWNER_IDS", "BOT_OWNER_ID"):
        raw = (os.environ.get(key) or "").strip()
        for value in raw.replace(",", " ").split():
            if value.isdigit():
                result.add(int(value))
    return result


def is_profile_store_owner(request, session: dict) -> bool:
    """Gate the bot-wide catalog to the bot owner or explicitly configured store admins."""
    if session.get("_local_dev"):
        return True
    try:
        actor_id = int(session.get("id", 0))
    except (TypeError, ValueError, OverflowError):
        return False
    if actor_id <= 0:
        return False

    allowed_ids = _configured_profile_store_admin_ids()
    bot = request.app.get("bot") if hasattr(request, "app") else None
    for candidate in (
        getattr(bot, "owner_id", None),
        *(getattr(bot, "owner_ids", None) or ()),
    ):
        try:
            if candidate is not None:
                allowed_ids.add(int(candidate))
        except (TypeError, ValueError, OverflowError):
            continue

    application = getattr(bot, "application", None)
    owner = getattr(application, "owner", None)
    for candidate in (
        getattr(owner, "id", None),
        getattr(owner, "owner_id", None),
    ):
        try:
            if candidate is not None:
                allowed_ids.add(int(candidate))
        except (TypeError, ValueError, OverflowError):
            continue
    # A Team owner may expose only its owner's user object.
    team_owner = getattr(owner, "owner", None)
    try:
        if getattr(team_owner, "id", None) is not None:
            allowed_ids.add(int(team_owner.id))
    except (TypeError, ValueError, OverflowError):
        pass
    return actor_id in allowed_ids


def _json_error(status: int, code: str, message: str | None = None) -> web.Response:
    data: dict[str, Any] = {"error": code}
    if message:
        data["message"] = message
    return web.json_response(data, status=status)


def _validation_response(error: ValueError) -> web.Response:
    return _json_error(400, str(error))


async def _read_item_payload(request) -> dict:
    try:
        body = await request.json(loads=json.loads)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as error:
        raise ValueError("invalid_json") from error
    if not isinstance(body, dict):
        raise ValueError("request_body_must_be_object")
    return body


async def _catalog_item_exists(db, item_id: int):
    async with db.execute(
        "SELECT * FROM profile_store_items WHERE item_id = ?",
        (int(item_id),),
    ) as cursor:
        return await cursor.fetchone()


async def _write_item_asset(request, *, authorize):
    session, guild = await authorize(request, write=True)
    raw_id = str(request.match_info.get("item_id", ""))
    if not raw_id.isdigit() or int(raw_id) <= 0:
        return _json_error(404, "item_not_found")
    item_id = int(raw_id)

    async with database.connect(aiosqlite.Row) as db:
        item = await _catalog_item_exists(db, item_id)
    if item is None or item["deleted_at"]:
        return _json_error(404, "item_not_found")
    if item["item_type"] not in _ASSET_ITEM_TYPES:
        return _json_error(400, "item_type_does_not_accept_image")

    try:
        if request.content_type.lower() != "multipart/form-data":
            return _json_error(415, "multipart_required")
        multipart = await request.multipart()
        image_data = None
        declared_mime = ""
        filename = ""
        while True:
            part = await multipart.next()
            if part is None:
                break
            if part.name != "file" or image_data is not None:
                return _json_error(400, "expected_one_file_field")
            declared_mime = part.headers.get("Content-Type", part.content_type or "")
            filename = part.filename or ""
            buffer = bytearray()
            while True:
                chunk = await part.read_chunk(size=64 * 1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > MAX_PROFILE_UPLOAD_BYTES:
                    return _json_error(413, "image_too_large")
            image_data = bytes(buffer)

        normalized = validate_static_image(
            image_data or b"",
            declared_mime=declared_mime,
            filename=filename,
            max_bytes=MAX_PROFILE_UPLOAD_BYTES,
        )
    except ValueError as error:
        status = 413 if str(error) in {"image_too_large", "sanitized_image_too_large"} else 400
        return _json_error(status, str(error))
    except (web.HTTPException, asyncio.CancelledError):
        raise
    except Exception:
        logger.exception("Profile catalog asset upload failed guild=%s item=%s", guild.id, item_id)
        return _json_error(500, "asset_upload_failed")

    filename = f"{uuid.uuid4().hex}{normalized['extension']}"
    relative_path = Path("catalog") / str(int(guild.id)) / filename
    root = profile_asset_root()
    destination = (root / relative_path).resolve()
    if root not in destination.parents:
        return _json_error(400, "invalid_asset_path")

    try:
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        await asyncio.to_thread(temporary.write_bytes, normalized["data"])
        await asyncio.to_thread(os.replace, temporary, destination)
        async with database.connect() as db:
            await db.execute(
                """
                UPDATE profile_store_items
                SET asset_path = ?, asset_url = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ? AND deleted_at IS NULL
                """,
                (relative_path.as_posix(), item_id),
            )
            await db.commit()
    except Exception:
        logger.exception("Could not persist profile catalog asset guild=%s item=%s", guild.id, item_id)
        try:
            await asyncio.to_thread(destination.unlink, missing_ok=True)
        except Exception:
            logger.warning("Could not clean failed profile asset", exc_info=True)
        return _json_error(500, "asset_storage_failed")

    return web.json_response({
        "item_id": item_id,
        "has_asset": True,
        "asset_name": filename,
        "format": normalized["format"],
        "width": normalized["width"],
        "height": normalized["height"],
        "size": normalized["size"],
    }, status=201)


def register_profile_store_admin_routes(routes, authorize) -> None:
    """Register these endpoints once on PRIME's existing aiohttp RouteTableDef."""
    global _registered
    if _registered:
        return

    async def store_authorize(request, *, write=False):
        session, guild = await authorize(
            request, write=write, management_tier="admin",
        )
        if not is_profile_store_owner(request, session):
            raise web.HTTPForbidden(
                text=json.dumps({"error": "profile_store_owner_required"}),
                content_type="application/json",
            )
        return session, guild

    @routes.get("/api/guild/{guild_id}/profile-store")
    async def profile_store_admin_get(request):
        _session, guild = await store_authorize(request)
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT * FROM profile_store_settings WHERE settings_id = 1"
            ) as cursor:
                settings_row = await cursor.fetchone()
            async with db.execute(
                """
                SELECT item_id, slug, name, description, item_type, category,
                       subcategory, rarity, price, currency, asset_path, asset_url,
                       style_json, metadata_json, is_active, is_featured,
                       created_at, updated_at, deleted_at
                FROM profile_store_items
                ORDER BY is_active DESC, item_id DESC
                LIMIT ?
                """,
                (MAX_CATALOG_ITEMS,),
            ) as cursor:
                rows = await cursor.fetchall()

        settings = dict(settings_row) if settings_row else {
            "enabled": 1,
            "custom_upload_enabled": 0,
            "custom_upload_price": 0,
            "custom_upload_currency": "moon",
            "max_custom_upload_bytes": MAX_PROFILE_UPLOAD_BYTES,
            "session_ttl_seconds": profile_store_db.DEFAULT_SESSION_TTL_SECONDS,
        }
        items = [_public_item(row) for row in rows]
        return web.json_response({"guild_id": str(guild.id), "settings": settings, "items": items})

    @routes.post("/api/guild/{guild_id}/profile-store/settings")
    async def profile_store_admin_settings_save(request):
        _session, guild = await store_authorize(request, write=True)
        try:
            body = await _read_item_payload(request)
            if not body:
                raise ValueError("settings_required")
            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT * FROM profile_store_settings WHERE settings_id = 1"
                ) as cursor:
                    current_row = await cursor.fetchone()
                current = dict(current_row) if current_row else {
                    "enabled": 1,
                    "custom_upload_enabled": 0,
                    "custom_upload_price": 0,
                    "custom_upload_currency": "moon",
                    "max_custom_upload_bytes": MAX_PROFILE_UPLOAD_BYTES,
                    "session_ttl_seconds": profile_store_db.DEFAULT_SESSION_TTL_SECONDS,
                }

                updated = {}
                for key in ("enabled", "custom_upload_enabled"):
                    if key in body:
                        if not isinstance(body[key], bool):
                            raise ValueError(f"{key}_must_be_boolean")
                        updated[key] = int(body[key])
                    else:
                        updated[key] = int(current[key])

                price = body.get("custom_upload_price", current["custom_upload_price"])
                if isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= 1_000_000_000:
                    raise ValueError("invalid_custom_upload_price")
                updated["custom_upload_price"] = price

                currency = body.get("custom_upload_currency", current["custom_upload_currency"])
                if not isinstance(currency, str) or currency not in profile_store_db.STORE_CURRENCIES:
                    raise ValueError("invalid_custom_upload_currency")
                updated["custom_upload_currency"] = currency

                max_bytes = body.get("max_custom_upload_bytes", current["max_custom_upload_bytes"])
                if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1024 <= max_bytes <= MAX_PROFILE_UPLOAD_BYTES:
                    raise ValueError("invalid_max_custom_upload_bytes")
                updated["max_custom_upload_bytes"] = max_bytes

                ttl = body.get("session_ttl_seconds", current["session_ttl_seconds"])
                if isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= profile_store_db.DEFAULT_SESSION_TTL_SECONDS:
                    raise ValueError("invalid_session_ttl_seconds")
                updated["session_ttl_seconds"] = ttl

                await db.execute(
                    """
                    INSERT OR IGNORE INTO profile_store_settings
                        (settings_id) VALUES (1)
                    """
                )
                await db.execute(
                    """
                    UPDATE profile_store_settings
                    SET enabled = ?, custom_upload_enabled = ?,
                        custom_upload_price = ?, custom_upload_currency = ?,
                        max_custom_upload_bytes = ?, session_ttl_seconds = ?,
                        updated_by = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE settings_id = 1
                    """,
                    (
                        updated["enabled"], updated["custom_upload_enabled"],
                        updated["custom_upload_price"], updated["custom_upload_currency"],
                        updated["max_custom_upload_bytes"], updated["session_ttl_seconds"],
                        int(_session.get("id", 0) or 0),
                    ),
                )
                await db.commit()
            return web.json_response({"guild_id": str(guild.id), "settings": updated})
        except ValueError as error:
            return _validation_response(error)

    @routes.post("/api/guild/{guild_id}/profile-store/items")
    async def profile_store_item_create(request):
        session, guild = await store_authorize(request, write=True)
        try:
            body = await _read_item_payload(request)
            item = validate_catalog_item_payload(body)
            async with database.connect(aiosqlite.Row) as db:
                cursor = await db.execute(
                    """
                    INSERT INTO profile_store_items
                        (slug, name, description, item_type, category, subcategory,
                         rarity, price, currency, style_json, metadata_json,
                         is_active, is_featured, created_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["slug"], item["name"], item["description"], item["item_type"],
                        item["category"], item["subcategory"], item["rarity"], item["price"],
                        item["currency"], item["style_json"], item["metadata_json"],
                        item["is_active"], item["is_featured"], int(session.get("id", 0) or 0),
                    ),
                )
                item_id = int(cursor.lastrowid)
                await db.commit()
                async with db.execute(
                    "SELECT * FROM profile_store_items WHERE item_id = ?", (item_id,)
                ) as cursor:
                    row = await cursor.fetchone()
            return web.json_response({"item": _public_item(row)}, status=201)
        except ValueError as error:
            return _validation_response(error)
        except aiosqlite.IntegrityError:
            return _json_error(409, "duplicate_slug")

    @routes.put("/api/guild/{guild_id}/profile-store/items/{item_id}")
    async def profile_store_item_update(request):
        _session, _guild = await store_authorize(request, write=True)
        raw_id = str(request.match_info.get("item_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "item_not_found")
        item_id = int(raw_id)
        try:
            body = await _read_item_payload(request)
            item = validate_catalog_item_payload(body)
            async with database.connect(aiosqlite.Row) as db:
                async with db.execute(
                    "SELECT item_id FROM profile_store_items WHERE item_id = ? AND deleted_at IS NULL",
                    (item_id,),
                ) as cursor:
                    exists = await cursor.fetchone()
                if not exists:
                    return _json_error(404, "item_not_found")
                await db.execute(
                    """
                    UPDATE profile_store_items SET
                        slug = ?, name = ?, description = ?, item_type = ?,
                        category = ?, subcategory = ?, rarity = ?, price = ?,
                        currency = ?, style_json = ?, metadata_json = ?,
                        is_active = ?, is_featured = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE item_id = ?
                    """,
                    (
                        item["slug"], item["name"], item["description"], item["item_type"],
                        item["category"], item["subcategory"], item["rarity"], item["price"],
                        item["currency"], item["style_json"], item["metadata_json"],
                        item["is_active"], item["is_featured"], item_id,
                    ),
                )
                await db.commit()
                async with db.execute(
                    "SELECT * FROM profile_store_items WHERE item_id = ?", (item_id,)
                ) as cursor:
                    row = await cursor.fetchone()
            return web.json_response({"item": _public_item(row)})
        except ValueError as error:
            return _validation_response(error)
        except aiosqlite.IntegrityError:
            return _json_error(409, "duplicate_slug")

    @routes.delete("/api/guild/{guild_id}/profile-store/items/{item_id}")
    async def profile_store_item_delete(request):
        _session, _guild = await authorize(
            request, write=True, management_tier="admin",
        )
        raw_id = str(request.match_info.get("item_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "item_not_found")
        item_id = int(raw_id)
        async with database.connect() as db:
            cursor = await db.execute(
                """
                UPDATE profile_store_items
                SET is_active = 0, deleted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ? AND deleted_at IS NULL
                """,
                (item_id,),
            )
            await db.commit()
        if cursor.rowcount <= 0:
            return _json_error(404, "item_not_found")
        return web.json_response({"item_id": item_id, "deleted": True})

    @routes.post("/api/guild/{guild_id}/profile-store/items/{item_id}/asset")
    async def profile_store_item_upload(request):
        return await _write_item_asset(request, authorize=store_authorize)

    _registered = True
