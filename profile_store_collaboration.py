"""Temporary, transferable collaboration links for the PRIME Profile Store.

Tokens are high-entropy bearer credentials. Only SHA-256 digests are persisted.
The link is deliberately reusable by anyone who possesses it until expiry/revocation.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
from aiohttp import web

import database

PERMISSIONS = {
    "upload_templates": "إضافة ورفع القوالب والخلفيات",
    "edit_designs": "تعديل التصاميم",
    "manage_pricing": "إدارة الأسعار والعروض",
    "manage_products": "إدارة المنتجات والأقسام",
    "publish_changes": "نشر التعديلات",
}
DEFAULT_TTL_SECONDS = 24 * 60 * 60
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
COOKIE_PREFIX = "prime_profile_store_collab_"
_REGISTERED = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_permissions(raw) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("permissions_required")
    if any(not isinstance(item, str) or item not in PERMISSIONS for item in raw):
        raise ValueError("invalid_collaboration_permission")
    return sorted(set(raw))


def validate_ttl(raw) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw not in (2 * 3600, DEFAULT_TTL_SECONDS, 7 * 24 * 3600):
        raise ValueError("invalid_collaboration_duration")
    return min(MAX_TTL_SECONDS, max(3600, raw))


async def _ensure_schema(db) -> None:
    await db.execute("""
        CREATE TABLE IF NOT EXISTS profile_store_collaboration_links (
            link_id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL CHECK (guild_id > 0),
            token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            permissions_json TEXT NOT NULL,
            created_by INTEGER NOT NULL CHECK (created_by > 0),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            CHECK (expires_at > created_at)
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_profile_store_collaboration_expiry
        ON profile_store_collaboration_links(guild_id, expires_at, revoked_at)
    """)


async def get_valid_link(token: str, guild_id: int):
    if not isinstance(token, str) or len(token) < 32 or len(token) > 128:
        return None
    now = _iso(_utcnow())
    async with database.connect(aiosqlite.Row) as db:
        await _ensure_schema(db)
        async with db.execute(
            """SELECT link_id, guild_id, token_hash, permissions_json, created_by,
                      created_at, expires_at, revoked_at
               FROM profile_store_collaboration_links
               WHERE token_hash = ? AND guild_id = ? AND revoked_at IS NULL AND expires_at > ?""",
            (_hash_token(token), int(guild_id), now),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        permissions = json.loads(result["permissions_json"])
        result["permissions"] = validate_permissions(permissions)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
    return result


async def resolve_request_collaborator(request):
    """Return scoped synthetic session/guild/link for a valid guild-specific cookie."""
    raw_guild = str(request.match_info.get("guild_id", ""))
    if not raw_guild.isdigit() or int(raw_guild) <= 0:
        return None
    token = request.cookies.get(f"{COOKIE_PREFIX}{int(raw_guild)}", "")
    if not token:
        return None
    link = await get_valid_link(token, int(raw_guild))
    if not link:
        return None
    # Same-origin guard for state-changing requests made with the bearer cookie.
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        fetch_site = str(request.headers.get("Sec-Fetch-Site", "")).lower()
        if fetch_site and fetch_site != "same-origin":
            raise web.HTTPForbidden(
                text=json.dumps({"error": "collaboration_same_origin_required"}),
                content_type="application/json",
            )
        origin = str(request.headers.get("Origin", "")).strip()
        if origin:
            from urllib.parse import urlsplit
            try:
                origin_host = (urlsplit(origin).netloc or "").lower()
            except ValueError:
                origin_host = ""
            if not origin_host or origin_host != str(request.host).lower():
                raise web.HTTPForbidden(
                    text=json.dumps({"error": "collaboration_same_origin_required"}),
                    content_type="application/json",
                )
    perms = set(link["permissions"])
    permission = permission_required_for_request(request, perms)
    if permission is False:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "profile_store_collaboration_permission_denied"}),
            content_type="application/json",
        )
    if isinstance(permission, str) and permission not in perms:
        raise web.HTTPForbidden(
            text=json.dumps({"error": "profile_store_collaboration_permission_denied", "required": permission}),
            content_type="application/json",
        )
    bot = request.app.get("bot")
    guild = bot.get_guild(int(raw_guild)) if bot and hasattr(bot, "get_guild") else None
    if guild is None:
        # Existing admin endpoints only need guild.id; the guild was already selected
        # when the owner created the link, and the token is bound to that guild ID.
        guild = SimpleNamespace(id=int(raw_guild))
    session = {
        # Shared links are anonymous bearer access; do not falsely attribute every
        # collaborator's catalog changes to the owner who created the link.
        "id": 0,
        "csrf": "",
        "_profile_store_collaboration": True,
        "_profile_store_collaboration_permissions": sorted(perms),
        "_profile_store_collaboration_link_id": int(link["link_id"]),
    }
    return session, guild, link


def permission_required_for_request(request, permissions: set[str]):
    """Map each existing admin API operation to explicit collaboration capabilities.

    Item updates are checked by comparing the incoming values to saved values so the
    visual editor can update only the fields the collaborator was granted.
    """
    path = request.path
    method = request.method.upper()
    if path.endswith("/settings") or "/users/" in path or path.endswith("/collaboration") or "/collaboration/" in path:
        return False
    if method == "GET":
        return None
    if path.endswith("/asset") and method in {"POST", "PUT"}:
        return "upload_templates" if "upload_templates" in permissions else ("edit_designs" if "edit_designs" in permissions else "upload_templates")
    if "/categories" in path or path.endswith("/items/reorder") or ("/items/" in path and method == "DELETE"):
        return "manage_products"
    if path.endswith("/items") and method == "POST":
        return "manage_products" if "manage_products" in permissions else "upload_templates"
    if "/items/" in path and method == "PUT":
        return None  # field-level changes are verified before the mutation handler runs
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    return None


async def _read_body_cached(request) -> dict:
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _decode_json(value):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


async def authorize_item_create_fields(request, link: dict) -> bool:
    """Guard new item fields using the same per-capability policy as item updates."""
    body = await _read_body_cached(request)
    allowed = set(link.get("permissions", []))
    item_type = body.get("item_type")
    asset_types = {"background", "card_template", "flag", "badge", "shape", "club_logo"}
    if item_type in asset_types:
        if not ({"manage_products", "upload_templates"} & allowed):
            return False
    elif "manage_products" not in allowed:
        return False
    price = body.get("price", 0)
    currency = body.get("currency", "moon")
    sale_values = (body.get("sale_price"), body.get("sale_starts_at"), body.get("sale_ends_at"))
    if (price not in (0, "0", None) or currency not in ("moon", None) or any(v not in (None, "") for v in sale_values)):
        if "manage_pricing" not in allowed:
            return False
    if body.get("is_active") is True and "publish_changes" not in allowed:
        return False
    if body.get("is_featured") is True and "publish_changes" not in allowed:
        return False
    if body.get("style") not in (None, {}) or body.get("metadata") not in (None, {}):
        if "edit_designs" not in allowed:
            return False
    return True


async def authorize_item_update_fields(request, link: dict) -> bool:
    """Allow only changed field groups for a collaborator-only PUT item request."""
    if not link:
        return False
    body = await _read_body_cached(request)
    raw_id = str(request.match_info.get("item_id", ""))
    if not raw_id.isdigit() or int(raw_id) <= 0:
        return False
    async with database.connect(aiosqlite.Row) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT * FROM profile_store_items WHERE item_id = ? AND deleted_at IS NULL",
            (int(raw_id),),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return False
    existing = dict(row)
    aliases = {
        "style": ("style_json", "edit_designs"),
        "metadata": ("metadata_json", "edit_designs"),
        "price": ("price", "manage_pricing"),
        "currency": ("currency", "manage_pricing"),
        "sale_price": ("sale_price", "manage_pricing"),
        "sale_starts_at": ("sale_starts_at", "manage_pricing"),
        "sale_ends_at": ("sale_ends_at", "manage_pricing"),
        "is_active": ("is_active", "publish_changes"),
        "is_featured": ("is_featured", "publish_changes"),
        "slug": ("slug", "manage_products"),
        "name": ("name", "manage_products"),
        "description": ("description", "manage_products"),
        "item_type": ("item_type", "manage_products"),
        "category": ("category", "manage_products"),
        "subcategory": ("subcategory", "manage_products"),
        "rarity": ("rarity", "manage_products"),
        "sort_order": ("sort_order", "manage_products"),
    }
    changed = set()
    # The legacy editor submits a full item document. Reject sparse PUT bodies:
    # downstream validators apply defaults to omitted fields, which could otherwise
    # reset protected prices or product metadata without those fields being authorized.
    if not set(aliases).issubset(body):
        return False
    for supplied_key, (db_key, required_permission) in aliases.items():
        if supplied_key not in body:
            continue
        new_value = body[supplied_key]
        old_value = existing.get(db_key)
        if db_key in ("style_json", "metadata_json"):
            new_value = json.dumps(new_value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            old_value = json.dumps(_decode_json(old_value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        elif db_key in ("is_active", "is_featured"):
            new_value = int(bool(new_value)) if isinstance(new_value, bool) else new_value
            old_value = int(old_value or 0)
        elif db_key in ("price", "sale_price", "sort_order"):
            if new_value is not None and not isinstance(new_value, bool):
                try:
                    new_value = int(new_value)
                except (TypeError, ValueError):
                    pass
        if new_value != old_value:
            changed.add(required_permission)
    allowed = set(link.get("permissions", []))
    return changed.issubset(allowed)


async def require_store_owner(request, authorize):
    session, guild = await authorize(request, write=True, management_tier="admin")
    from profile_store_admin import is_profile_store_owner
    if not is_profile_store_owner(request, session):
        raise web.HTTPForbidden(
            text=json.dumps({"error": "profile_store_owner_required"}),
            content_type="application/json",
        )
    return session, guild


def _json_error(status: int, error: str):
    return web.json_response({"error": error}, status=status, headers={"Cache-Control": "no-store"})


def _private_html(html: str, guild_id: int, *, collaborator: bool) -> web.Response:
    safe_guild = json.dumps(str(guild_id), ensure_ascii=True)
    safe_csrf = json.dumps("", ensure_ascii=True)
    html = html.replace('data-guild-id="" data-csrf=""',
                        f'data-guild-id={safe_guild} data-csrf={safe_csrf}')
    html = html.replace('<main class="wrap" id="app" data-guild-id=', '<main class="wrap" id="app" data-collaborator="' + ("true" if collaborator else "false") + '" data-guild-id=')
    return web.Response(text=html, content_type="text/html", charset="utf-8", headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


def register_profile_store_collaboration_routes(routes, authorize, read_json_body=None):
    global _REGISTERED
    if _REGISTERED:
        return

    @routes.post("/api/guild/{guild_id}/profile-store/collaboration")
    async def create_collaboration_link(request):
        session, guild = await require_store_owner(request, authorize)
        try:
            body = await _read_body_cached(request)
            permissions = validate_permissions(body.get("permissions"))
            ttl = validate_ttl(body.get("ttl_seconds", DEFAULT_TTL_SECONDS))
            raw = secrets.token_urlsafe(32)
            now = _utcnow()
            expires = now + timedelta(seconds=ttl)
            async with database.connect(aiosqlite.Row) as db:
                await _ensure_schema(db)
                await db.execute("BEGIN IMMEDIATE")
                try:
                    cursor = await db.execute(
                        """INSERT INTO profile_store_collaboration_links
                           (guild_id, token_hash, permissions_json, created_by, created_at, expires_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (int(guild.id), _hash_token(raw), json.dumps(permissions), int(session.get("id", 0)), _iso(now), _iso(expires)),
                    )
                    link_id = int(cursor.lastrowid)
                    await db.commit()
                except BaseException:
                    await db.rollback()
                    raise
            return web.json_response({
                "link_id": link_id,
                "share_path": f"/profile-store-collab/{int(guild.id)}/{raw}",
                "permissions": permissions,
                "expires_at": _iso(expires),
                "reusable": True,
            }, status=201, headers={"Cache-Control": "no-store"})
        except ValueError as error:
            return _json_error(400, str(error))

    @routes.get("/api/guild/{guild_id}/profile-store/collaboration")
    async def list_collaboration_links(request):
        _session, guild = await require_store_owner(request, authorize)
        async with database.connect(aiosqlite.Row) as db:
            await _ensure_schema(db)
            async with db.execute(
                """SELECT link_id, permissions_json, created_by, created_at, expires_at, revoked_at
                   FROM profile_store_collaboration_links WHERE guild_id = ?
                   ORDER BY link_id DESC LIMIT 100""",
                (int(guild.id),),
            ) as cursor:
                rows = await cursor.fetchall()
        links = []
        now = _iso(_utcnow())
        for row in rows:
            item = dict(row)
            item["permissions"] = _decode_json(item.pop("permissions_json"))
            item["active"] = not item["revoked_at"] and item["expires_at"] > now
            links.append(item)
        return web.json_response({"links": links}, headers={"Cache-Control": "no-store"})

    @routes.delete("/api/guild/{guild_id}/profile-store/collaboration/{link_id}")
    async def revoke_collaboration_link(request):
        _session, guild = await require_store_owner(request, authorize)
        raw_id = str(request.match_info.get("link_id", ""))
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return _json_error(404, "collaboration_link_not_found")
        async with database.connect(aiosqlite.Row) as db:
            await _ensure_schema(db)
            cursor = await db.execute(
                """UPDATE profile_store_collaboration_links SET revoked_at = ?
                   WHERE link_id = ? AND guild_id = ? AND revoked_at IS NULL""",
                (_iso(_utcnow()), int(raw_id), int(guild.id)),
            )
            await db.commit()
        if not cursor.rowcount:
            return _json_error(404, "collaboration_link_not_found_or_revoked")
        return web.json_response({"link_id": int(raw_id), "revoked": True}, headers={"Cache-Control": "no-store"})

    @routes.get("/profile-store-collab/{guild_id}/{token}")
    async def collaboration_link_entry(request):
        raw_guild = str(request.match_info.get("guild_id", ""))
        token = str(request.match_info.get("token", ""))
        if not raw_guild.isdigit() or int(raw_guild) <= 0:
            return _json_error(404, "collaboration_link_not_found")
        link = await get_valid_link(token, int(raw_guild))
        if not link:
            return web.Response(
                text="<html lang='ar' dir='rtl'><meta charset='utf-8'><title>رابط منتهي</title><p>رابط التعاون غير صالح أو منتهي أو ألغاه مالك المتجر.</p></html>",
                content_type="text/html",
                status=403,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        seconds = max(1, int((datetime.fromisoformat(link["expires_at"].replace("Z", "+00:00")) - _utcnow()).total_seconds()))
        response = web.HTTPFound(location=f"/profile-store-collab/{int(raw_guild)}")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        forwarded_proto = str(request.headers.get("X-Forwarded-Proto", "")).split(",", 1)[0].strip().lower()
        response.set_cookie(
            f"{COOKIE_PREFIX}{int(raw_guild)}", token,
            max_age=seconds, httponly=True,
            secure=request.scheme == "https" or forwarded_proto == "https",
            samesite="Strict", path="/",
        )
        return response

    @routes.get("/profile-store-collab/{guild_id}")
    async def collaboration_workspace(request):
        raw_guild = str(request.match_info.get("guild_id", ""))
        if not raw_guild.isdigit() or int(raw_guild) <= 0:
            return _json_error(404, "collaboration_link_not_found")
        token = request.cookies.get(f"{COOKIE_PREFIX}{int(raw_guild)}", "")
        link = await get_valid_link(token, int(raw_guild))
        if not link:
            return web.Response(
                text="<html lang='ar' dir='rtl'><meta charset='utf-8'><title>رابط منتهي</title><p>رابط التعاون غير صالح أو منتهي أو ألغاه مالك المتجر.</p></html>",
                content_type="text/html",
                status=403,
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        page_path = Path(__file__).resolve().parent / "dashboard" / "profile-store-admin.html"
        try:
            html = await page_path.read_text("utf-8")
        except OSError:
            return _json_error(503, "store_admin_ui_unavailable")
        response = _private_html(html, int(raw_guild), collaborator=True)
        # Enforcement lives on every API request; the browser never receives the raw token.
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    _REGISTERED = True
