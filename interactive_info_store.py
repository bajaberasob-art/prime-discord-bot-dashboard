"""SQLite persistence for interactive info panels."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiosqlite

DB_PATH = (os.environ.get("DB_PATH") or "").strip() or "data/bot.db"
DB_NAME = (
    "bot_database.db"
    if "DB_PATH" not in os.environ and DB_PATH == "data/bot.db"
    and os.path.exists("bot_database.db") and not os.path.exists(DB_PATH)
    else DB_PATH
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactive_info_panels (
    guild_id INTEGER PRIMARY KEY,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

async def ensure_schema() -> None:
    parent = os.path.dirname(DB_NAME)
    if parent:
        os.makedirs(parent, exist_ok=True)
    async with aiosqlite.connect(DB_NAME, timeout=30) as db:
        await db.execute(_SCHEMA)
        await db.commit()

async def get_panel(guild_id: int) -> dict[str, Any] | None:
    await ensure_schema()
    async with aiosqlite.connect(DB_NAME, timeout=30) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT config_json FROM interactive_info_panels WHERE guild_id=?", (int(guild_id),))
        row = await cursor.fetchone()
    if not row:
        return None
    try:
        config = json.loads(row["config_json"])
        return config if isinstance(config, dict) else None
    except (ValueError, TypeError):
        return None

async def save_panel(guild_id: int, config: dict[str, Any]) -> dict[str, Any]:
    await ensure_schema()
    payload = dict(config)
    payload["guild_id"] = str(int(guild_id))
    async with aiosqlite.connect(DB_NAME, timeout=30) as db:
        await db.execute(
            """INSERT INTO interactive_info_panels(guild_id, config_json, updated_at)
               VALUES(?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(guild_id) DO UPDATE SET
                 config_json=excluded.config_json, updated_at=CURRENT_TIMESTAMP""",
            (int(guild_id), json.dumps(payload, ensure_ascii=False, separators=(",", ":"))),
        )
        await db.commit()
    return payload

async def list_enabled_panels() -> list[dict[str, Any]]:
    await ensure_schema()
    async with aiosqlite.connect(DB_NAME, timeout=30) as db:
        cursor = await db.execute("SELECT config_json FROM interactive_info_panels")
        rows = await cursor.fetchall()
    result = []
    for row in rows:
        try:
            config = json.loads(row[0])
            if isinstance(config, dict) and config.get("enabled") and config.get("message_id"):
                result.append(config)
        except (ValueError, TypeError):
            continue
    return result

def validate_config(guild_id: int, body: dict[str, Any], old: dict[str, Any] | None = None) -> dict[str, Any]:
    old = old or {}
    if not isinstance(body, dict):
        raise ValueError("صيغة الإعدادات غير صحيحة.")
    title = str(body.get("title", old.get("title", "دليل السيرفر"))).strip()
    description = str(body.get("description", old.get("description", ""))).strip()
    color = str(body.get("color", old.get("color", "#6366F1"))).strip()
    banner = str(body.get("banner_url", old.get("banner_url", ""))).strip()
    banner_file = str(body.get("banner_file", old.get("banner_file", "")) or "").strip()
    if banner_file:
        upload_root = Path(os.getenv("PRIME_PANEL_UPLOAD_DIR") or "data/interactive_info_banners").resolve()
        resolved_file = Path(banner_file).resolve()
        if resolved_file.parent != upload_root or not resolved_file.name.startswith(f"{int(guild_id)}-") or resolved_file.suffix.lower() not in {".png", ".jpg", ".webp", ".gif"}:
            raise ValueError("ملف البانر المرفوع غير صالح لهذا السيرفر؛ ارفع الصورة مرة أخرى.")
        banner_file = str(resolved_file)
    if len(title) > 256 or len(description) > 4096:
        raise ValueError("العنوان أو الوصف يتجاوز حد Discord.")
    if color and not __import__("re").fullmatch(r"#?[0-9a-fA-F]{6}", color):
        raise ValueError("لون الإيمبد يجب أن يكون HEX مثل #6366F1.")
    if banner and not banner.startswith(("https://", "http://")):
        valid_attachment = bool(banner_file) and __import__("re").fullmatch(r"attachment://prime-banner\.(png|jpg|webp|gif)", banner)
        if not valid_attachment:
            raise ValueError("رابط البانر يجب أن يبدأ بـ https:// أو http://، أو أن يكون صورة مرفوعة صالحة.")
    seconds = body.get("auto_delete_seconds", old.get("auto_delete_seconds", 300))
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        raise ValueError("مدة الرد غير صحيحة.")
    if not 5 <= seconds <= 840:
        raise ValueError("مدة الرد يجب أن تكون بين 5 و840 ثانية (14 دقيقة كحد أقصى).")
    buttons = body.get("buttons", old.get("buttons", []))
    if not isinstance(buttons, list) or not 1 <= len(buttons) <= 25:
        raise ValueError("يجب إضافة 1 إلى 25 زرًا.")
    clean_buttons = []
    used = set()
    styles = {"primary", "secondary", "success", "danger"}
    for raw in buttons:
        if not isinstance(raw, dict):
            raise ValueError("تعريف أحد الأزرار غير صالح.")
        label = str(raw.get("label") or "").strip()
        response = str(raw.get("response") or "").strip()
        style = str(raw.get("style") or "primary").lower()
        button_id = str(raw.get("id") or __import__("uuid").uuid4().hex[:12])
        if not label or len(label) > 80:
            raise ValueError("اسم كل زر مطلوب وبحد أقصى 80 حرفًا.")
        if len(response) > 2000:
            raise ValueError(f"رد الزر «{label}» يتجاوز 2000 حرف.")
        if style not in styles:
            raise ValueError("نمط زر غير مدعوم.")
        if not __import__("re").fullmatch(r"[a-f0-9]{12}", button_id) or button_id in used:
            button_id = __import__("uuid").uuid4().hex[:12]
        used.add(button_id)
        emoji = str(raw.get("emoji") or "").strip()[:100] or None
        if emoji and emoji.startswith("<") and not __import__("re").fullmatch(r"<a?:[A-Za-z0-9_]{2,32}:\d{15,22}>", emoji):
            raise ValueError(f"صيغة الإيموجي للزر «{label}» غير صحيحة. اختر إيموجيًا من قائمة السيرفر أو استخدم إيموجي Unicode.")
        clean_buttons.append({"id": button_id, "label": label, "emoji": emoji, "style": style, "response": response})
    channel_id = str(body.get("channel_id", old.get("channel_id", "")) or "").strip()
    if channel_id and not channel_id.isdigit():
        raise ValueError("معرف القناة غير صالح.")
    return {
        "guild_id": str(int(guild_id)),
        "enabled": bool(body.get("enabled", old.get("enabled", False))),
        "channel_id": channel_id or None,
        "message_id": str(old.get("message_id")) if old.get("message_id") else None,
        "pin_message": bool(body.get("pin_message", old.get("pin_message", False))),
        "pinned": bool(old.get("pinned", False)),
        "title": title,
        "description": description,
        "color": color if color.startswith("#") else f"#{color}",
        "banner_url": banner,
        "banner_file": banner_file or None,
        "auto_delete_seconds": seconds,
        "buttons": clean_buttons,
        "last_published_at": old.get("last_published_at"),
    }
