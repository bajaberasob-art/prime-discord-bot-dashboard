"""Authenticated API for the interactive information panel builder."""
from __future__ import annotations

import logging
from aiohttp import web

import interactive_info_store as store

log = logging.getLogger("InteractiveInfoDashboard")


def _context():
    import web_server
    return web_server


async def api_get(req):
    ws = _context()
    _, guild = await ws.authorize(req, management_tier="admin")
    try:
        config = await store.get_panel(guild.id) or store.validate_config(guild.id, {})
        cog = ws.bot_ref.get_cog("InteractiveInfoPanel") if ws.bot_ref else None
        return web.json_response({
            "ok": True,
            "config": config,
            "channels": [
                {"id": str(ch.id), "name": ch.name}
                for ch in guild.text_channels
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
                return web.json_response({"error": "bot_unavailable", "message": "وحدة البوت غير جاهزة؛ تم حفظ الإعدادات دون نشر."}, status=503)
            try:
                config = await cog.publish(guild, config)
            except ValueError as error:
                return web.json_response({"error": "publish_failed", "message": str(error), "config": config}, status=400)
            except Exception:
                log.exception("Could not publish interactive panel guild=%s", guild.id)
                return web.json_response({"error": "publish_failed", "message": "تعذر نشر الرسالة. تحقق من صلاحيات البوت والقناة."}, status=502)
        return web.json_response({"ok": True, "config": config})
    except ValueError as error:
        return web.json_response({"error": "invalid_settings", "message": str(error)}, status=400)
    except Exception:
        log.exception("Cannot save interactive panel guild=%s", guild.id)
        return web.json_response({"error": "unavailable", "message": "تعذر حفظ إعدادات اللوحة."}, status=503)


def register_routes(routes):
    routes.get("/api/guild/{guild_id}/interactive-info-panel")(api_get)
    routes.post("/api/guild/{guild_id}/interactive-info-panel")(api_save)
