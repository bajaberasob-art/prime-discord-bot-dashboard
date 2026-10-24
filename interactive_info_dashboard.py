"""Authenticated API for the interactive information panel builder."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import discord
from aiohttp import web
from PIL import Image

import interactive_info_store as store

log = logging.getLogger("InteractiveInfoDashboard")


def _context():
    import web_server
    return web_server


async def api_get(req):
    ws = _context()
    _, guild = await ws.authorize(req, management_tier="admin")
    try:
        config = await store.get_panel(guild.id) or {
            "guild_id": str(guild.id), "enabled": False, "channel_id": None,
            "message_id": None, "pin_message": False, "pinned": False,
            "title": "دليل السيرفر",
            "description": "اختر أحد الأزرار بالأسفل لعرض المعلومات المطلوبة.",
            "color": "#6366F1", "banner_url": "", "auto_delete_seconds": 300,
            "buttons": [
                {"id": "a1b2c3d4e5f6", "label": "القوانين", "emoji": "📜",
                 "style": "primary", "response": "يرجى الالتزام بقوانين السيرفر واحترام جميع الأعضاء."},
                {"id": "b1c2d3e4f5a6", "label": "من نحن؟", "emoji": "✨",
                 "style": "secondary", "response": "أهلًا بك في مجتمعنا!"},
            ],
        }
        cog = ws.bot_ref.get_cog("InteractiveInfoPanel") if ws.bot_ref else None
        return web.json_response({
            "ok": True,
            "config": config,
            "channels": [
                {"id": str(ch.id), "name": ch.name}
                for ch in guild.text_channels
            ],
            # Use gateway-cached guild emojis; never make one Discord REST call
            # per dashboard refresh just to populate the emoji selector.
            "emojis": [
                {
                    "id": str(emoji.id),
                    "name": emoji.name,
                    "animated": bool(getattr(emoji, "animated", False)),
                }
                for emoji in sorted(guild.emojis, key=lambda item: item.name.casefold())
            ],
            "bot_ready": bool(cog),
        })
    except Exception:
        log.exception("Cannot load interactive panel guild=%s", guild.id)
        return web.json_response({"error": "unavailable", "message": "تعذر تحميل إعدادات اللوحة."}, status=503)


async def api_save(req):
    ws = _context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    body = await ws.read_json_body(req)
    try:
        old = await store.get_panel(guild.id)
        config = store.validate_config(guild.id, body, old)
        # Do not lose the currently published message reference while saving a draft.
        await store.save_panel(guild.id, config)
        if body.get("publish"):
            cog = ws.bot_ref.get_cog("InteractiveInfoPanel") if ws.bot_ref else None
            if cog is None:
                return web.json_response({
                    "error": "bot_unavailable",
                    "message": "تم حفظ الإعدادات، لكن وحدة البوت غير جاهزة للنشر. حاول النشر بعد اتصال البوت.",
                    "saved": True,
                    "config": config,
                }, status=503)
            try:
                config = await cog.publish(guild, config)
            except ValueError as error:
                return web.json_response({
                    "error": "publish_failed",
                    "message": f"تم حفظ الإعدادات، لكن تعذر النشر: {error}",
                    "saved": True,
                    "config": config,
                }, status=400)
            except discord.HTTPException as error:
                log.exception("Discord rejected interactive panel guild=%s code=%s", guild.id, getattr(error, "code", None))
                if getattr(error, "code", None) == 50035:
                    return web.json_response({
                        "error": "invalid_component",
                        "message": "تم حفظ الإعدادات، لكن Discord رفض أحد مكونات الرسالة. اختر إيموجيًا متاحًا من قائمة السيرفر أو أدخل إيموجيًا Unicode صحيحًا ثم أعد النشر.",
                        "saved": True,
                        "config": config,
                    }, status=400)
                return web.json_response({
                    "error": "publish_failed",
                    "message": "تم حفظ الإعدادات، لكن تعذر نشر الرسالة عبر Discord. تحقق من صلاحيات البوت والقناة ثم أعد المحاولة.",
                    "saved": True,
                    "config": config,
                }, status=502)
            except Exception:
                log.exception("Could not publish interactive panel guild=%s", guild.id)
                return web.json_response({
                    "error": "publish_failed",
                    "message": "تم حفظ الإعدادات، لكن حدث خطأ أثناء النشر. راجع سجل البوت ثم أعد المحاولة.",
                    "saved": True,
                    "config": config,
                }, status=502)
        return web.json_response({"ok": True, "config": config})
    except ValueError as error:
        return web.json_response({"error": "invalid_settings", "message": str(error)}, status=400)
    except Exception:
        log.exception("Cannot save interactive panel guild=%s", guild.id)
        return web.json_response({"error": "unavailable", "message": "تعذر حفظ إعدادات اللوحة."}, status=503)


async def api_upload_banner(req):
    """Store a validated banner image for the authorized guild; it is sent as a Discord attachment on publish."""
    ws = _context()
    _, guild = await ws.authorize(req, write=True, management_tier="admin")
    try:
        reader = await req.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            raise ValueError("اختر ملف صورة أولًا.")
        content_type = (field.headers.get("Content-Type") or "").lower()
        allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
        if content_type not in allowed:
            raise ValueError("الصيغ المدعومة: PNG وJPG وWEBP وGIF.")
        data = await field.read(decode=False)
        if not data or len(data) > 8 * 1024 * 1024:
            raise ValueError("حجم الصورة يجب ألا يتجاوز 8 ميغابايت.")
        try:
            import io
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as error:
            raise ValueError("الملف المحدد ليس صورة صالحة.") from error
        root = Path(os.getenv("PRIME_PANEL_UPLOAD_DIR") or "data/interactive_info_banners").resolve()
        root.mkdir(parents=True, exist_ok=True)
        # Keep the filename random and non-user-controlled; each guild's image is isolated.
        name = f"{int(guild.id)}-{uuid.uuid4().hex}{allowed[content_type]}"
        path = root / name
        path.write_bytes(data)
        return web.json_response({"ok": True, "banner_file": str(path), "banner_url": "attachment://prime-banner" + allowed[content_type], "filename": "prime-banner" + allowed[content_type]})
    except ValueError as error:
        return web.json_response({"error": "invalid_image", "message": str(error)}, status=400)
    except Exception:
        log.exception("Cannot upload interactive panel banner guild=%s", guild.id)
        return web.json_response({"error": "upload_failed", "message": "تعذر حفظ الصورة."}, status=500)


def register_routes(routes):
    routes.get("/api/guild/{guild_id}/interactive-info-panel")(api_get)
    routes.post("/api/guild/{guild_id}/interactive-info-panel")(api_save)
    routes.post("/api/guild/{guild_id}/interactive-info-panel/banner")(api_upload_banner)
