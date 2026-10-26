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
MAX_PROFILE_UPLOAD_FRAMES = 60
MAX_PROFILE_UPLOAD_TOTAL_PIXELS = 32_000_000
MAX_STYLE_JSON_BYTES = 12 * 1024
MAX_CATALOG_ITEMS = 500
ALLOWED_UPLOAD_MIME = {
    "PNG": {"image/png"},
    "JPEG": {"image/jpeg", "image/jpg"},
    "WEBP": {"image/webp"},
    "GIF": {"image/gif"},
}
ALLOWED_UPLOAD_EXT = {
    "PNG": {".png"},
    "JPEG": {".jpg", ".jpeg"},
    "WEBP": {".webp"},
    "GIF": {".gif"},
}
MIME_TO_FORMAT = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/webp": "WEBP",
    "image/gif": "GIF",
}
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,78}[a-z0-9])?$")
COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_CATEGORY_RE = re.compile(r"^[\w -]{1,80}$", re.UNICODE)
_ASSET_ITEM_TYPES = {"background", "card_template", "flag", "badge", "shape", "club_logo"}
_registered = False


def validate_static_image(
    data: bytes,
    *,
    declared_mime: str,
    filename: str,
    max_bytes: int = MAX_PROFILE_UPLOAD_BYTES,
) -> dict[str, Any]:
    """Validate and sanitize PNG/JPEG/WebP images and bounded animated GIFs.

    GIF frames are decoded and re-encoded without source metadata. Animation is
    retained only within strict frame, aggregate-pixel, and output-size limits.
    Animated PNG and animated WebP remain rejected.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise ValueError("empty_image")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError("invalid_size_limit")
    size_limit = min(max_bytes, MAX_PROFILE_UPLOAD_BYTES)
    if len(data) > size_limit:
        raise ValueError("image_too_large")

    safe_name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError("unsupported_image_extension")

    mime = str(declared_mime or "").split(";", 1)[0].strip().lower()
    actual_format = None
    frame_count = 1
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                actual_format = str(image.format or "").upper()
                frame_count = int(getattr(image, "n_frames", 1) or 1)
                if actual_format not in ALLOWED_UPLOAD_MIME:
                    raise ValueError("unsupported_image_format")
                if frame_count > 1 and actual_format != "GIF":
                    raise ValueError("animated_images_not_allowed")
                if frame_count > MAX_PROFILE_UPLOAD_FRAMES:
                    raise ValueError("animated_image_frame_limit")
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("invalid_image_dimensions")
                frame_pixels = int(image.width) * int(image.height)
                if frame_pixels > MAX_PROFILE_UPLOAD_PIXELS:
                    raise ValueError("image_pixel_limit")
                if actual_format == "GIF" and frame_pixels * frame_count > MAX_PROFILE_UPLOAD_TOTAL_PIXELS:
                    raise ValueError("animated_image_pixel_limit")
                if suffix not in ALLOWED_UPLOAD_EXT[actual_format]:
                    raise ValueError("image_extension_mismatch")
                if mime not in ALLOWED_UPLOAD_MIME[actual_format]:
                    raise ValueError("image_mime_mismatch")
                image.verify()

            with Image.open(io.BytesIO(data)) as image:
                output = io.BytesIO()
                normalized_frames = 1
                animated = False
                if actual_format == "GIF":
                    normalized_frames = int(getattr(image, "n_frames", 1) or 1)
                    if normalized_frames > MAX_PROFILE_UPLOAD_FRAMES:
                        raise ValueError("animated_image_frame_limit")
                    frames: list[Image.Image] = []
                    durations: list[int] = []
                    total_pixels = 0
                    loop_count = max(0, min(65535, int(image.info.get("loop", 0) or 0)))
                    for frame_index in range(normalized_frames):
                        image.seek(frame_index)
                        frame = image.convert("RGBA").copy()
                        total_pixels += int(frame.width) * int(frame.height)
                        if total_pixels > MAX_PROFILE_UPLOAD_TOTAL_PIXELS:
                            raise ValueError("animated_image_pixel_limit")
                        frames.append(frame)
                        try:
                            duration = int(image.info.get("duration", 100) or 100)
                        except (TypeError, ValueError, OverflowError):
                            duration = 100
                        durations.append(max(20, min(10000, duration)))
                    frames[0].save(
                        output, format="GIF", save_all=True,
                        append_images=frames[1:], duration=durations,
                        loop=loop_count, disposal=2, optimize=False,
                    )
                    clean_width, clean_height = frames[0].size
                    animated = normalized_frames > 1
                else:
                    if frame_count != 1:
                        raise ValueError("animated_images_not_allowed")
                    image.load()
                    if actual_format == "JPEG":
                        clean = ImageOps.exif_transpose(image).convert("RGB")
                    elif actual_format == "PNG":
                        clean = ImageOps.exif_transpose(image)
                        has_alpha = "A" in clean.getbands() or "transparency" in clean.info
                        clean = clean.convert("RGBA" if has_alpha else "RGB")
                    else:
                        clean = ImageOps.exif_transpose(image)
                        has_alpha = "A" in clean.getbands() or "transparency" in clean.info
                        clean = clean.convert("RGBA" if has_alpha else "RGB")

                    save_options: dict[str, Any] = {}
                    if actual_format == "PNG":
                        save_options["optimize"] = True
                    elif actual_format == "JPEG":
                        save_options.update(quality=90, optimize=True, progressive=True)
                    elif actual_format == "WEBP":
                        save_options.update(quality=90, method=4)
                    clean.save(output, format=actual_format, **save_options)
                    clean_width, clean_height = clean.size

                sanitized = output.getvalue()
                if not sanitized or len(sanitized) > size_limit:
                    raise ValueError("sanitized_image_too_large")
                return {
                    "data": sanitized,
                    "format": actual_format,
                    "mime": {
                        "PNG": "image/png",
                        "JPEG": "image/jpeg",
                        "WEBP": "image/webp",
                        "GIF": "image/gif",
                    }[actual_format],
                    "extension": {
                        "PNG": ".png",
                        "JPEG": ".jpg",
                        "WEBP": ".webp",
                        "GIF": ".gif",
                    }[actual_format],
                    "width": int(clean_width),
                    "height": int(clean_height),
                    "size": len(sanitized),
                    "frames": int(normalized_frames),
                    "animated": bool(animated),
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
        root = Path(configured).expanduser().resolve()
    else:
        db_path = (os.environ.get("DB_PATH") or "").strip()
        if not db_path:
            db_path = getattr(database, "DB_NAME", "data/bot.db")
        root = (Path(db_path).expanduser().resolve().parent / "profile_store_assets").resolve()

    public_root = (Path(__file__).resolve().parent / "dashboard").resolve()
    if root == public_root or public_root in root.parents:
        raise ValueError("profile assets must be stored outside the public dashboard root")
    return root


def default_uploaded_template_metadata(width: int, height: int) -> dict[str, Any]:
    """Give a newly uploaded PNG template a safe, useful default composition."""
    if (
        isinstance(width, bool) or isinstance(height, bool)
        or not isinstance(width, int) or not isinstance(height, int)
        or width < 640 or height < 320 or width > 16000 or height > 16000
    ):
        raise ValueError("template_dimensions_too_small_or_large")
    ratio = width / height
    if ratio < 0.4 or ratio > 4.0:
        raise ValueError("template_aspect_ratio_not_supported")

    # The renderer caps card canvases at 2.5 MP and 2048x1600. Scale geometry
    # and canvas metadata together so larger uploads are reduced without distortion.
    scale = min(1.0, 2048 / width, 1600 / height, (2_500_000 / (width * height)) ** 0.5)
    w, h = int(width * scale), int(height * scale)
    layout = "portrait" if h > w * 1.08 else ("square" if w <= h * 1.08 else "wide")
    stats = []
    keys = (("balance", "الرصيد"), ("messages", "الرسائل"),
            ("rank", "الترتيب"), ("voice", "الصوت"))
    if layout == "wide":
        stat_boxes = [(0.025, 0.755, 0.220, 0.145), (0.265, 0.755, 0.220, 0.145),
                      (0.505, 0.755, 0.220, 0.145), (0.745, 0.755, 0.220, 0.145)]
        avatar = {"x": int(w*.785), "y": int(h*.075), "size": int(min(w,h)*.185)}
        name = {"x": int(w*.755), "y": int(h*.335), "max_width": int(w*.405), "anchor": "ra"}
        handle = {"x": int(w*.755), "y": int(h*.385), "max_width": int(w*.405), "anchor": "ra"}
        level = {"x": int(w*.055), "y": int(h*.455), "max_width": int(w*.405), "anchor": "la"}
        xp = {"x": int(w*.055), "y": int(h*.520), "width": int(w*.685), "height": max(8,int(h*.014))}
        badges = {"x": int(w*.055), "y": int(h*.580), "size": max(24,int(h*.030)), "limit": 5}
    else:
        stat_boxes = [(0.055, 0.655, 0.410, 0.115), (0.535, 0.655, 0.410, 0.115),
                      (0.055, 0.805, 0.410, 0.115), (0.535, 0.805, 0.410, 0.115)]
        avatar = {"x": int(w*.675), "y": int(h*.075), "size": int(min(w,h)*.165)}
        name = {"x": int(w*.925), "y": int(h*.295), "max_width": int(w*.425), "anchor": "ra"}
        handle = {"x": int(w*.925), "y": int(h*.340), "max_width": int(w*.425), "anchor": "ra"}
        level = {"x": int(w*.065), "y": int(h*.405), "max_width": int(w*.430), "anchor": "la"}
        xp = {"x": int(w*.065), "y": int(h*.470), "width": int(w*.865), "height": max(8,int(h*.012))}
        badges = {"x": int(w*.065), "y": int(h*.535), "size": max(24,int(h*.028)), "limit": 5}

    for (key, label), (x, y, box_w, box_h) in zip(keys, stat_boxes):
        stats.append({"key": key, "label": label, "x": int(w*x), "y": int(h*y),
                      "w": int(w*box_w), "h": int(h*box_h)})
    return {
        "layout": layout, "width": w, "height": h,
        "orientation": "portrait" if layout == "portrait" else ("square" if layout == "square" else "landscape"),
        "avatar": avatar, "name": name, "handle": handle, "level": level,
        "xp_bar": xp, "badges": badges,
        "flag": {"x": int(w*.045), "y": int(h*.035), "width": max(24,int(w*.055)), "height": max(18,int(h*.035))},
        "stats": stats,
    }


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


def validate_category_payload(payload: dict, *, allow_parent: bool = True) -> dict:
    """Validate a top-level store section or a child section without trusting the UI."""
    if not isinstance(payload, dict):
        raise ValueError("category_must_be_object")
    name = _payload_text(payload, "name", "", 80, required=True)
    if not _CATEGORY_RE.fullmatch(name) or name.strip().lower() == "all":
        raise ValueError("invalid_category_name")
    parent_id = payload.get("parent_id")
    if not allow_parent and parent_id is not None:
        raise ValueError("nested_subcategories_not_supported")
    if parent_id in ("", 0, "0"):
        parent_id = None
    if parent_id is not None:
        if isinstance(parent_id, bool) or not str(parent_id).isdigit() or int(parent_id) <= 0:
            raise ValueError("invalid_parent_category")
        parent_id = int(parent_id)
    sort_order = payload.get("sort_order", 0)
    if isinstance(sort_order, bool) or not isinstance(sort_order, int) or not 0 <= sort_order <= 1_000_000:
        raise ValueError("invalid_category_order")
    is_active = payload.get("is_active", True)
    if not isinstance(is_active, bool):
        raise ValueError("category_active_must_be_boolean")
    return {"name": name.strip(), "parent_id": parent_id,
            "sort_order": sort_order, "is_active": int(is_active)}


async def _record_catalog_audit(
    db, *, guild_id: int, actor_id: int | None, action: str,
    entity_type: str, entity_id: int | None, before: dict | None, after: dict | None,
) -> None:
    """Persist an audit event in the same transaction as the catalog mutation."""
    await db.execute(
        """
        INSERT INTO profile_store_catalog_audit
            (guild_id, actor_user_id, action, entity_type, entity_id, before_json, after_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (int(guild_id), actor_id if actor_id and actor_id > 0 else None,
         action[:48], entity_type, entity_id,
         json.dumps(before or {}, ensure_ascii=False, separators=(",", ":")),
         json.dumps(after or {}, ensure_ascii=False, separators=(",", ":"))),
    )


async def _ensure_category_rows(db, category: str, subcategory: str, actor_id: int | None = None) -> None:
    """Keep the legacy item category strings backed by canonical manageable rows."""
    category = str(category or "").strip()
    subcategory = str(subcategory or "").strip()
    if not category or category.lower() == "all":
        return
    await db.execute(
        """
        INSERT OR IGNORE INTO profile_store_categories (name, parent_id, sort_order, is_active, created_by)
        VALUES (?, NULL, 0, 1, ?)
        """,
        (category, actor_id),
    )
    async with db.execute(
        "SELECT category_id FROM profile_store_categories WHERE parent_id IS NULL AND name = ? COLLATE NOCASE",
        (category,),
    ) as cursor:
        parent = await cursor.fetchone()
    if parent and subcategory:
        await db.execute(
            """
            INSERT OR IGNORE INTO profile_store_categories (name, parent_id, sort_order, is_active, created_by)
            VALUES (?, ?, 0, 1, ?)
            """,
            (subcategory, int(parent["category_id"]), actor_id),
        )


def _public_category(row) -> dict:
    return {
        "category_id": int(row["category_id"]),
        "name": str(row["name"]),
        "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
        "sort_order": int(row["sort_order"]),
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


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


def _catalog_asset_exists(raw_path: Any) -> bool:
    """Return true only for a safe catalog asset that exists on disk."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    try:
        from profile_store_public import _resolve_asset_path
        return _resolve_asset_path(raw_path) is not None
    except (ImportError, OSError, ValueError):
        return False


def _public_item(row) -> dict:
    item = dict(row)
    item["style"] = _parse_json_column(item.pop("style_json", "{}"))
    item["metadata"] = _parse_json_column(item.pop("metadata_json", "{}"))
    path = item.pop("asset_path", None)
    item["has_asset"] = _catalog_asset_exists(path)
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


async def _read_item_payload(request, read_json_body=None) -> dict:
    try:
        body = await read_json_body(request) if read_json_body else await request.json(loads=json.loads)
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
        item_row = await _catalog_item_exists(db, item_id)
        item = dict(item_row) if item_row is not None else None
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
            declared_mime = part.headers.get("Content-Type") or ""
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

    generated_metadata_json = None
    if str(item["item_type"]) == "card_template":
        existing_metadata = _parse_json_column(item.get("metadata_json"))
        if not (
            isinstance(existing_metadata.get("stats"), list)
            and len(existing_metadata.get("stats")) == 4
            and isinstance(existing_metadata.get("avatar"), dict)
        ):
            try:
                generated_metadata = default_uploaded_template_metadata(
                    int(normalized["width"]), int(normalized["height"]),
                )
            except ValueError as error:
                return _json_error(400, str(error))
            generated_metadata_json = json.dumps(
                generated_metadata, ensure_ascii=False, separators=(",", ":"),
            )

    filename = f"{uuid.uuid4().hex}{normalized['extension']}"
    relative_path = Path("catalog") / str(int(guild.id)) / filename
    root = profile_asset_root()
    destination = (root / relative_path).resolve()
    if root not in destination.parents:
        return _json_error(400, "invalid_asset_path")

    try:
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True, mode=0o700)
        await asyncio.to_thread(os.chmod, root, 0o700)
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True, mode=0o700)
        await asyncio.to_thread(os.chmod, destination.parent, 0o700)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        await asyncio.to_thread(temporary.write_bytes, normalized["data"])
        await asyncio.to_thread(os.chmod, temporary, 0o600)
        await asyncio.to_thread(os.replace, temporary, destination)
        async with database.connect(aiosqlite.Row) as db:
            await db.execute("BEGIN IMMEDIATE")
            cursor = await db.execute(
                """
                UPDATE profile_store_items
                SET asset_path = ?, asset_url = NULL,
                    metadata_json = COALESCE(?, metadata_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ? AND deleted_at IS NULL
                """,
                (relative_path.as_posix(), generated_metadata_json, item_id),
            )
            if cursor.rowcount != 1:
                await db.rollback()
                await asyncio.to_thread(destination.unlink, missing_ok=True)
                return _json_error(404, "item_not_found")
            await _record_catalog_audit(
                db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                action="asset_replaced" if item.get("asset_path") else "asset_uploaded",
                entity_type="asset", entity_id=item_id,
                before={"asset_path": item.get("asset_path"), "has_asset": bool(item.get("asset_path"))},
                after={"asset_path": relative_path.as_posix(), "format": normalized["format"],
                       "width": normalized["width"], "height": normalized["height"],
                       "size": normalized["size"], "frames": normalized.get("frames", 1),
                       "animated": normalized.get("animated", False)},
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
        "frames": normalized.get("frames", 1),
        "animated": normalized.get("animated", False),
    }, status=201)


def register_profile_store_admin_routes(routes, authorize, read_json_body=None) -> None:
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

    @routes.get("/profile-store-admin/{guild_id}")
    async def profile_store_admin_page(request):
        session, _guild = await store_authorize(request)
        page_path = Path(__file__).resolve().parent / "dashboard" / "profile-store-admin.html"
        try:
            html = await asyncio.to_thread(page_path.read_text, "utf-8")
        except OSError:
            return _json_error(503, "store_admin_ui_unavailable")
        # The token comes from the authenticated server session, not a client parameter.
        csrf_token = str(session.get("csrf", ""))
        safe_csrf = json.dumps(csrf_token, ensure_ascii=True)
        safe_guild_id = json.dumps(str(_guild.id), ensure_ascii=True)
        html = html.replace('data-guild-id="" data-csrf=""',
                            f'data-guild-id={safe_guild_id} data-csrf={safe_csrf}')
        return web.Response(
            text=html,
            content_type="text/html",
            charset="utf-8",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
            },
        )

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
                WHERE deleted_at IS NULL
                ORDER BY is_active DESC, item_id DESC
                LIMIT ?
                """,
                (MAX_CATALOG_ITEMS,),
            ) as cursor:
                rows = await cursor.fetchall()
            async with db.execute(
                """
                SELECT category_id, name, parent_id, sort_order, is_active, created_at, updated_at
                FROM profile_store_categories
                ORDER BY COALESCE(parent_id, 0), sort_order, category_id
                """
            ) as cursor:
                category_rows = await cursor.fetchall()

        settings = dict(settings_row) if settings_row else {
            "enabled": 1,
            "custom_upload_enabled": 0,
            "custom_upload_price": 0,
            "custom_upload_currency": "moon",
            "max_custom_upload_bytes": MAX_PROFILE_UPLOAD_BYTES,
            "session_ttl_seconds": profile_store_db.DEFAULT_SESSION_TTL_SECONDS,
        }
        items = [_public_item(row) for row in rows]
        categories = [_public_category(row) for row in category_rows]
        return web.json_response({
            "guild_id": str(guild.id), "settings": settings,
            "items": items, "categories": categories,
        })

    @routes.post("/api/guild/{guild_id}/profile-store/settings")
    async def profile_store_admin_settings_save(request):
        _session, guild = await store_authorize(request, write=True)
        try:
            body = await _read_item_payload(request, read_json_body)
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
                await _record_catalog_audit(
                    db, guild_id=int(guild.id), actor_id=int(_session.get("id", 0) or 0),
                    action="settings_updated", entity_type="settings", entity_id=1,
                    before=current, after=updated,
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
            if item["is_active"] and item["item_type"] in _ASSET_ITEM_TYPES:
                return _json_error(400, "asset_required_before_publish")
            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
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
                await _ensure_category_rows(db, item["category"], item["subcategory"],
                                            int(session.get("id", 0) or 0))
                await _record_catalog_audit(
                    db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                    action="item_created", entity_type="item", entity_id=item_id,
                    before=None, after={k: item[k] for k in (
                        "slug", "name", "item_type", "category", "subcategory",
                        "price", "currency", "is_active", "is_featured",
                    )},
                )
                await db.commit()
                async with db.execute("SELECT * FROM profile_store_items WHERE item_id = ?", (item_id,)) as cursor:
                    row = await cursor.fetchone()
            return web.json_response({"item": _public_item(row)}, status=201)
        except ValueError as error:
            return _validation_response(error)
        except aiosqlite.IntegrityError:
            return _json_error(409, "duplicate_slug")

    @routes.put("/api/guild/{guild_id}/profile-store/items/{item_id}")
    async def profile_store_item_update(request):
        session, guild = await store_authorize(request, write=True)
        raw_id = str(request.match_info.get("item_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "item_not_found")
        item_id = int(raw_id)
        try:
            body = await _read_item_payload(request)
            item = validate_catalog_item_payload(body)
            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT * FROM profile_store_items WHERE item_id = ? AND deleted_at IS NULL",
                    (item_id,),
                ) as cursor:
                    before_row = await cursor.fetchone()
                if not before_row:
                    await db.rollback()
                    return _json_error(404, "item_not_found")
                before_item = _public_item(before_row)
                if item["is_active"] and item["item_type"] in _ASSET_ITEM_TYPES and not before_item["has_asset"]:
                    await db.rollback()
                    return _json_error(409, "asset_required_before_publish")
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
                await _ensure_category_rows(db, item["category"], item["subcategory"],
                                            int(session.get("id", 0) or 0))
                await _record_catalog_audit(
                    db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                    action="item_updated", entity_type="item", entity_id=item_id,
                    before=before_item, after={k: item[k] for k in (
                        "slug", "name", "item_type", "category", "subcategory",
                        "price", "currency", "is_active", "is_featured",
                    )},
                )
                await db.commit()
                async with db.execute("SELECT * FROM profile_store_items WHERE item_id = ?", (item_id,)) as cursor:
                    row = await cursor.fetchone()
            return web.json_response({"item": _public_item(row)})
        except ValueError as error:
            return _validation_response(error)
        except aiosqlite.IntegrityError:
            return _json_error(409, "duplicate_slug")

    @routes.delete("/api/guild/{guild_id}/profile-store/items/{item_id}")
    async def profile_store_item_delete(request):
        session, guild = await store_authorize(request, write=True)
        raw_id = str(request.match_info.get("item_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "item_not_found")
        item_id = int(raw_id)
        async with database.connect(aiosqlite.Row) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT * FROM profile_store_items WHERE item_id = ? AND deleted_at IS NULL",
                (item_id,),
            ) as cursor:
                before_row = await cursor.fetchone()
            if not before_row:
                await db.rollback()
                return _json_error(404, "item_not_found")
            before_item = _public_item(before_row)
            cursor = await db.execute(
                """
                UPDATE profile_store_items
                SET is_active = 0, deleted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE item_id = ? AND deleted_at IS NULL
                """,
                (item_id,),
            )
            await _record_catalog_audit(
                db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                action="item_deleted", entity_type="item", entity_id=item_id,
                before=before_item, after={"is_active": 0, "deleted": True},
            )
            await db.commit()
        if cursor.rowcount <= 0:
            return _json_error(404, "item_not_found")
        return web.json_response({"item_id": item_id, "deleted": True})

    @routes.post("/api/guild/{guild_id}/profile-store/users/{user_id}/gems")
    async def profile_store_gem_adjust(request):
        _session, guild = await store_authorize(request, write=True)
        raw_guild_id = str(request.match_info.get("guild_id", ""))
        if not raw_guild_id.isdigit() or int(raw_guild_id) != int(guild.id):
            return _json_error(403, "guild_scope_mismatch")
        raw_user_id = str(request.match_info.get("user_id", ""))
        if not raw_user_id.isdigit() or int(raw_user_id) <= 0:
            return _json_error(400, "invalid_user_id")
        user_id = int(raw_user_id)
        try:
            body = await _read_item_payload(request, read_json_body)
            delta = body.get("delta")
            if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0 or abs(delta) > 1_000_000:
                raise ValueError("invalid_gem_delta")
            reason = body.get("reason", "")
            if not isinstance(reason, str) or len(reason.strip()) > 200:
                raise ValueError("invalid_gem_reason")
            reason = reason.strip() or "administrator_adjustment"

            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """
                    INSERT OR IGNORE INTO user_profile_gem_wallets
                        (guild_id, user_id, balance)
                    VALUES (?, ?, 0)
                    """,
                    (int(guild.id), user_id),
                )
                async with db.execute(
                    """
                    SELECT balance FROM user_profile_gem_wallets
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (int(guild.id), user_id),
                ) as cursor:
                    wallet = await cursor.fetchone()
                current = int(wallet["balance"]) if wallet else 0
                updated = current + delta
                if updated < 0:
                    await db.rollback()
                    return _json_error(409, "insufficient_gems")
                await db.execute(
                    """
                    UPDATE user_profile_gem_wallets
                    SET balance = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE guild_id = ? AND user_id = ?
                    """,
                    (updated, int(guild.id), user_id),
                )
                action = "gem_grant" if delta > 0 else "gem_adjustment"
                await db.execute(
                    """
                    INSERT INTO profile_store_transactions
                        (guild_id, user_id, item_id, transaction_type, currency,
                         delta, balance_after, reason)
                    VALUES (?, ?, NULL, ?, 'gem', ?, ?, ?)
                    """,
                    (int(guild.id), user_id, action, delta, updated, reason[:200]),
                )
                try:
                    actor_value = int(_session.get("id", 0) or 0)
                except (AttributeError, TypeError, ValueError, OverflowError):
                    actor_value = 0
                await db.execute(
                    """
                    INSERT INTO profile_store_admin_audit
                        (guild_id, actor_user_id, target_user_id, action,
                         delta, balance_after, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(guild.id), actor_value if actor_value > 0 else None,
                        user_id, action, delta, updated, reason[:200],
                    ),
                )
                await db.commit()
            return web.json_response({
                "guild_id": str(guild.id),
                "user_id": str(user_id),
                "currency": "gem",
                "delta": delta,
                "balance": updated,
            })
        except ValueError as error:
            return _validation_response(error)
        except Exception:
            logger.exception("PRIME gem wallet adjustment failed guild=%s user=%s", guild.id, user_id)
            return _json_error(500, "gem_adjustment_failed")

    @routes.get("/api/guild/{guild_id}/profile-store/items/{item_id}/asset")
    async def profile_store_item_asset_read(request):
        _session, _guild = await store_authorize(request)
        raw_id = str(request.match_info.get("item_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "item_not_found")
        async with database.connect(aiosqlite.Row) as db:
            async with db.execute(
                "SELECT asset_path FROM profile_store_items WHERE item_id = ? AND deleted_at IS NULL",
                (int(raw_id),),
            ) as cursor:
                row = await cursor.fetchone()
        if not row or not row["asset_path"]:
            return _json_error(404, "asset_not_found")
        # Resolve both repository-approved default template PNGs and private uploaded
        # catalog assets through the same traversal-safe resolver used by the store.
        from profile_store_public import _resolve_asset_path
        path = _resolve_asset_path(row["asset_path"])
        if path is None:
            return _json_error(404, "asset_not_found")
        return web.FileResponse(path, headers={
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        })

    @routes.post("/api/guild/{guild_id}/profile-store/categories")
    async def profile_store_category_create(request):
        session, guild = await store_authorize(request, write=True)
        try:
            body = await _read_item_payload(request)
            item = validate_category_payload(body)
            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
                if item["parent_id"] is not None:
                    async with db.execute(
                        "SELECT category_id FROM profile_store_categories WHERE category_id = ? AND parent_id IS NULL",
                        (item["parent_id"],),
                    ) as cursor:
                        parent = await cursor.fetchone()
                    if not parent:
                        await db.rollback()
                        return _json_error(400, "invalid_parent_category")
                cursor = await db.execute(
                    """
                    INSERT INTO profile_store_categories (name, parent_id, sort_order, is_active, created_by)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item["name"], item["parent_id"], item["sort_order"], item["is_active"],
                     int(session.get("id", 0) or 0)),
                )
                category_id = int(cursor.lastrowid)
                async with db.execute(
                    "SELECT category_id, name, parent_id, sort_order, is_active, created_at, updated_at FROM profile_store_categories WHERE category_id = ?",
                    (category_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                category = _public_category(row)
                await _record_catalog_audit(
                    db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                    action="category_created", entity_type="category", entity_id=category_id,
                    before=None, after=category,
                )
                await db.commit()
            return web.json_response({"category": category}, status=201)
        except ValueError as error:
            return _validation_response(error)
        except aiosqlite.IntegrityError:
            return _json_error(409, "duplicate_category")

    @routes.put("/api/guild/{guild_id}/profile-store/categories/reorder")
    async def profile_store_category_reorder(request):
        session, guild = await store_authorize(request, write=True)
        try:
            body = await _read_item_payload(request)
            ids = body.get("category_ids")
            if not isinstance(ids, list) or len(ids) > 500 or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in ids
            ) or len(ids) != len(set(ids)):
                raise ValueError("invalid_category_order")
            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT category_id, name, parent_id, sort_order, is_active, created_at, updated_at FROM profile_store_categories"
                ) as cursor:
                    rows = await cursor.fetchall()
                existing = {int(row["category_id"]): dict(row) for row in rows}
                if set(ids) != set(existing):
                    await db.rollback()
                    return _json_error(409, "category_order_stale")
                before = [{"category_id": key, "sort_order": row["sort_order"]} for key, row in existing.items()]
                for order, category_id in enumerate(ids):
                    await db.execute(
                        "UPDATE profile_store_categories SET sort_order = ?, updated_at = CURRENT_TIMESTAMP WHERE category_id = ?",
                        (order, category_id),
                    )
                await _record_catalog_audit(
                    db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                    action="categories_reordered", entity_type="category", entity_id=None,
                    before={"categories": before},
                    after={"category_ids": ids},
                )
                await db.commit()
            return web.json_response({"ok": True, "category_ids": ids})
        except ValueError as error:
            return _validation_response(error)

    @routes.put("/api/guild/{guild_id}/profile-store/categories/{category_id}")
    async def profile_store_category_update(request):
        session, guild = await store_authorize(request, write=True)
        raw_id = str(request.match_info.get("category_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "category_not_found")
        category_id = int(raw_id)
        try:
            body = await _read_item_payload(request)
            item = validate_category_payload(body)
            async with database.connect(aiosqlite.Row) as db:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute(
                    "SELECT category_id, name, parent_id, sort_order, is_active, created_at, updated_at FROM profile_store_categories WHERE category_id = ?",
                    (category_id,),
                ) as cursor:
                    before_row = await cursor.fetchone()
                if not before_row:
                    await db.rollback()
                    return _json_error(404, "category_not_found")
                if item["parent_id"] == category_id:
                    await db.rollback()
                    return _json_error(400, "invalid_parent_category")
                if item["parent_id"] is not None:
                    async with db.execute(
                        "SELECT category_id FROM profile_store_categories WHERE category_id = ? AND parent_id IS NULL",
                        (item["parent_id"],),
                    ) as cursor:
                        parent = await cursor.fetchone()
                    if not parent:
                        await db.rollback()
                        return _json_error(400, "invalid_parent_category")
                if before_row["parent_id"] is None and item["parent_id"] is not None:
                    async with db.execute(
                        "SELECT 1 FROM profile_store_categories WHERE parent_id = ? LIMIT 1",
                        (category_id,),
                    ) as cursor:
                        has_children = await cursor.fetchone()
                    if has_children:
                        await db.rollback()
                        return _json_error(409, "category_has_children")
                before = dict(before_row)
                await db.execute(
                    """
                    UPDATE profile_store_categories
                    SET name = ?, parent_id = ?, sort_order = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE category_id = ?
                    """,
                    (item["name"], item["parent_id"], item["sort_order"], item["is_active"], category_id),
                )
                # Keep existing item text fields aligned when a category is renamed.
                old_parent_name = None
                new_parent_name = None
                if before["parent_id"] is not None:
                    async with db.execute(
                        "SELECT name FROM profile_store_categories WHERE category_id = ?",
                        (before["parent_id"],),
                    ) as cursor:
                        old_parent_row = await cursor.fetchone()
                    old_parent_name = str(old_parent_row["name"]) if old_parent_row else None
                if item["parent_id"] is not None:
                    async with db.execute(
                        "SELECT name FROM profile_store_categories WHERE category_id = ?",
                        (item["parent_id"],),
                    ) as cursor:
                        new_parent_row = await cursor.fetchone()
                    new_parent_name = str(new_parent_row["name"]) if new_parent_row else None

                if before["parent_id"] is None and item["parent_id"] is None:
                    await db.execute(
                        "UPDATE profile_store_items SET category = ?, updated_at = CURRENT_TIMESTAMP WHERE category = ? COLLATE NOCASE",
                        (item["name"], before["name"]),
                    )
                elif before["parent_id"] is None and item["parent_id"] is not None:
                    await db.execute(
                        "UPDATE profile_store_items SET category = ?, subcategory = ?, updated_at = CURRENT_TIMESTAMP WHERE category = ? COLLATE NOCASE",
                        (new_parent_name, item["name"], before["name"]),
                    )
                elif before["parent_id"] is not None and item["parent_id"] is None:
                    await db.execute(
                        "UPDATE profile_store_items SET category = ?, subcategory = '', updated_at = CURRENT_TIMESTAMP WHERE subcategory = ? COLLATE NOCASE AND category = ? COLLATE NOCASE",
                        (item["name"], before["name"], old_parent_name),
                    )
                elif old_parent_name and new_parent_name:
                    await db.execute(
                        "UPDATE profile_store_items SET category = ?, subcategory = ?, updated_at = CURRENT_TIMESTAMP WHERE subcategory = ? COLLATE NOCASE AND category = ? COLLATE NOCASE",
                        (new_parent_name, item["name"], before["name"], old_parent_name),
                    )
                async with db.execute(
                    "SELECT category_id, name, parent_id, sort_order, is_active, created_at, updated_at FROM profile_store_categories WHERE category_id = ?",
                    (category_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                category = _public_category(row)
                await _record_catalog_audit(
                    db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                    action="category_updated", entity_type="category", entity_id=category_id,
                    before=before, after=category,
                )
                await db.commit()
            return web.json_response({"category": category})
        except ValueError as error:
            return _validation_response(error)
        except aiosqlite.IntegrityError:
            return _json_error(409, "duplicate_category")

    @routes.delete("/api/guild/{guild_id}/profile-store/categories/{category_id}")
    async def profile_store_category_delete(request):
        session, guild = await store_authorize(request, write=True)
        raw_id = str(request.match_info.get("category_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "category_not_found")
        category_id = int(raw_id)
        async with database.connect(aiosqlite.Row) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT category_id, name, parent_id, sort_order, is_active, created_at, updated_at FROM profile_store_categories WHERE category_id = ?",
                (category_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                await db.rollback()
                return _json_error(404, "category_not_found")
            before = dict(row)
            # Categories are archived rather than hard-deleted to preserve product references.
            await db.execute(
                "UPDATE profile_store_categories SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE category_id = ?",
                (category_id,),
            )
            await _record_catalog_audit(
                db, guild_id=int(guild.id), actor_id=int(session.get("id", 0) or 0),
                action="category_hidden", entity_type="category", entity_id=category_id,
                before=before, after={**before, "is_active": False},
            )
            await db.commit()
        return web.json_response({"category_id": category_id, "is_active": False})

    @routes.post("/api/guild/{guild_id}/profile-store/items/{item_id}/asset")
    async def profile_store_item_upload(request):
        return await _write_item_asset(request, authorize=store_authorize)

    _registered = True
