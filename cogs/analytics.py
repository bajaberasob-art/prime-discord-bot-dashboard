"""Per-guild Discord audit logging, routing and conservative invite tracking."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands, tasks

from database import (
    LOG_ROUTING_CACHE,
    claim_logging_audit_entry,
    get_invite_tracking_cache,
    get_logging_category_settings,
    get_logging_channels,
    get_cached_logging_channels,
    record_invite_tracking_join,
    replace_invite_tracking_cache,
)

logger = logging.getLogger("AnalyticsCog")

COLORS = {
    "log_sanctions": (0xDC2626, "⚔️ سجل العقوبات"),
    "log_violations": (0xEAB308, "⚠️ سجل المخالفات"),
    "log_automod": (0xF97316, "🛡️ سجل Auto-Mod"),
    "log_ticket": (0x14B8A6, "🎫 سجل التذاكر"),
    "log_channel": (0x10B981, "📁 سجل القنوات"),
    "log_server": (0x3B82F6, "🏰 سجل السيرفر"),
    "log_member": (0x22C55E, "👤 سجل الأعضاء"),
    "log_invites": (0x38BDF8, "🔗 سجل الدعوات"),
    "log_message": (0xEF4444, "💬 سجل الرسائل"),
    "log_react": (0xEC4899, "👍 سجل التفاعلات"),
    "log_roles": (0x8B5CF6, "🎭 سجل الرتب والصلاحيات"),
    "log_security": (0xF97316, "🛡️ سجل إدارة السيرفر والأمان"),
    "log_voice": (0x06B6D4, "🎙️ سجل النشاط الصوتي"),
    # Legacy names remain valid for existing listeners and integrations.
    "log_messages": (0xEF4444, "🗑️ حذف رسالة"),
    "log_channels": (0x10B981, "📁 نشاط القنوات"),
    "log_moderation": (0xDC2626, "⚔️ إجراء إداري"),
    "log_warnings": (0xEAB308, "⚠️ إنذار إداري"),
}

EVENT_OPTIONS = {
    "log_sanctions": [
        {"id": "ban", "label": "الحظر Ban"},
        {"id": "unban", "label": "فك الحظر Unban"},
        {"id": "kick", "label": "الطرد Kick"},
        {"id": "timeout_add", "label": "إضافة Timeout"},
        {"id": "timeout_remove", "label": "إزالة Timeout"},
        {"id": "temp_ban_expired", "label": "انتهاء الحظر المؤقت"},
        {"id": "command_action", "label": "إجراءات أوامر PRIME الإدارية"},
    ],
    "log_violations": [
        {"id": "warning", "label": "الإنذارات"},
        {"id": "command_action", "label": "إجراءات إدارة الدردشة من PRIME"},
    ],
    "log_automod": [
        {"id": "automod_action", "label": "إجراءات AutoMod"},
        {"id": "automod_rule_create", "label": "إنشاء قاعدة AutoMod"},
        {"id": "automod_rule_update", "label": "تعديل قاعدة AutoMod"},
        {"id": "automod_rule_delete", "label": "حذف قاعدة AutoMod"},
    ],
    "log_ticket": [{"id": "ticket_action", "label": "تغييرات التذاكر"}],
    "log_channel": [
        {"id": "channel_create", "label": "إنشاء قناة أو تصنيف"},
        {"id": "channel_update", "label": "تعديل القنوات وإعداداتها"},
        {"id": "channel_delete", "label": "حذف قناة أو تصنيف"},
        {"id": "overwrite_update", "label": "تغيير صلاحيات القناة"},
        {"id": "thread_create", "label": "إنشاء Thread"},
        {"id": "thread_update", "label": "تعديل Thread"},
        {"id": "thread_delete", "label": "حذف Thread"},
        {"id": "command_action", "label": "تغييرات القنوات من أوامر PRIME"},
    ],
    "log_server": [
        {"id": "guild_update", "label": "إعدادات السيرفر"},
        {"id": "emoji_update", "label": "إنشاء أو تعديل أو حذف Emoji"},
        {"id": "sticker_update", "label": "إنشاء أو تعديل أو حذف Sticker"},
        {"id": "webhook_update", "label": "تغيّر قائمة Webhook؛ التفاصيل من Audit Log"},
        {"id": "integration_update", "label": "تغييرات التكاملات"},
        {"id": "command_action", "label": "تغييرات السيرفر من أوامر PRIME"},
    ],
    "log_member": [
        {"id": "member_join", "label": "انضمام عضو"},
        {"id": "member_leave", "label": "انتهاء عضوية عضو"},
        {"id": "username_update", "label": "تغيير Username"},
        {"id": "display_name_update", "label": "تغيير Display Name"},
        {"id": "nickname_update", "label": "تغيير Nickname"},
        {"id": "avatar_update", "label": "تغيير صورة الحساب"},
        {"id": "guild_avatar_update", "label": "تغيير صورة العضو في السيرفر"},
        {"id": "verification_update", "label": "تحديث حالة التحقق"},
        {"id": "command_action", "label": "إجراءات الأعضاء من أوامر PRIME"},
    ],
    "log_invites": [
        {"id": "invite_create", "label": "إنشاء دعوة"},
        {"id": "invite_delete", "label": "حذف أو انتهاء دعوة"},
        {"id": "invite_used", "label": "استخدام دعوة محددة"},
        {"id": "invite_unknown", "label": "انضمام بمصدر غير معروف"},
    ],
    "log_message": [
        {"id": "message_delete", "label": "حذف رسالة"},
        {"id": "message_bulk_delete", "label": "حذف جماعي للرسائل"},
        {"id": "message_edit", "label": "تعديل رسالة"},
        {"id": "message_delete_content", "label": "تسجيل محتوى الرسالة المحذوفة"},
        {"id": "message_edit_content", "label": "تسجيل المحتوى السابق والجديد"},
        {"id": "pins_update", "label": "تثبيت أو إلغاء تثبيت"},
    ],
    "log_voice": [
        {"id": "voice_join", "label": "دخول روم صوتي"},
        {"id": "voice_leave", "label": "خروج من روم صوتي"},
        {"id": "voice_move", "label": "الانتقال بين الرومات"},
        {"id": "voice_mute", "label": "كتم أو إلغاء كتم المايك"},
        {"id": "voice_deafen", "label": "كتم أو إلغاء كتم السماعة"},
        {"id": "voice_disconnect", "label": "فصل إداري من الروم"},
        {"id": "command_action", "label": "إجراءات الصوت من أوامر PRIME"},
    ],
    "log_react": [
        {"id": "reaction_add", "label": "إضافة تفاعل"},
        {"id": "reaction_remove", "label": "إزالة تفاعل"},
        {"id": "reaction_clear", "label": "مسح التفاعلات"},
    ],
    "log_roles": [
        {"id": "role_create", "label": "إنشاء رتبة"},
        {"id": "role_update", "label": "تعديل الاسم أو اللون أو الترتيب"},
        {"id": "role_delete", "label": "حذف رتبة"},
        {"id": "role_permissions", "label": "تغيير صلاحيات رتبة"},
        {"id": "member_role_add", "label": "إعطاء رتبة لعضو"},
        {"id": "member_role_remove", "label": "سحب رتبة من عضو"},
        {"id": "command_action", "label": "إجراءات الرتب من أوامر PRIME"},
    ],
    "log_security": [
        {"id": "audit_action", "label": "إجراءات إدارية من Audit Log"},
        {"id": "security_permission_update", "label": "تغييرات الصلاحيات الحساسة"},
    ],
}
DEFAULT_OFF_EVENTS = {"message_delete_content", "message_edit_content"}


def default_event_types(category: str) -> list[str]:
    return [
        item["id"]
        for item in EVENT_OPTIONS[category]
        if item["id"] not in DEFAULT_OFF_EVENTS
    ]

CATEGORY_ALIASES = {
    "log_messages": "log_message",
    "log_channels": "log_channel",
    "log_moderation": "log_sanctions",
    "log_warnings": "log_violations",
}


def _avatar(user: Any) -> str | None:
    value = getattr(getattr(user, "display_avatar", None), "url", None)
    return str(value) if value else None


def _member_mention(member_or_id: Any) -> str:
    if member_or_id is None:
        return "غير معروف"
    mention = getattr(member_or_id, "mention", None)
    if mention and re.fullmatch(r"<@!?\d+>", str(mention)):
        return str(mention)
    member_id = getattr(member_or_id, "id", member_or_id)
    try:
        member_id = int(member_id)
    except (TypeError, ValueError):
        return _safe(member_or_id, 100)
    return f"<@{member_id}>" if member_id > 0 else "غير معروف"


def _channel_mention(channel_or_id: Any, guild: Any = None) -> str:
    if channel_or_id is None:
        return "قناة غير متاحة"
    channel = channel_or_id
    channel_id = getattr(channel, "id", channel)
    try:
        channel_id = int(channel_id)
    except (TypeError, ValueError):
        mention = getattr(channel, "mention", None)
        if mention and re.fullmatch(r"<#\d+>", str(mention)):
            return str(mention)
        name = getattr(channel, "name", None)
        return f"#{name}" if name else _safe(channel, 100)
    if guild is not None:
        resolver = getattr(guild, "get_channel_or_thread", None) or getattr(guild, "get_channel", None)
        resolved = resolver(channel_id) if resolver else None
        if resolved is not None:
            channel = resolved
    mention = getattr(channel, "mention", None)
    if mention and re.fullmatch(r"<#\d+>", str(mention)):
        return str(mention)
    return f"<#{channel_id}>" if channel_id > 0 else "قناة غير متاحة"


def _format_log_fields(guild: Any, category: str, fields: Any) -> tuple[list[Any], str | None]:
    """Replace raw member/channel IDs in audit fields with Discord mentions."""
    if not fields:
        return [], None

    canonical = CATEGORY_ALIASES.get(category, category)
    member_labels = (
        "العضو", "كاتب", "الحساب", "المنفذ", "المسؤول", "المنشئ",
        "صاحب الدعوة", "صاحب التذكرة", "المشرف", "المستخدم", "الهدف",
    )
    channel_labels = ("القناة", "الروم", "thread")
    numeric_suffix = re.compile(r"^\s*\(\s*`?\d+`?\s*\)")
    member_thumbnail = None
    formatted = []

    for item in fields:
        if isinstance(item, dict):
            name = item.get("name", "")
            value = item.get("value", "")
            inline = item.get("inline", True)
            output = dict(item)
        else:
            name, value, *rest = item
            inline = rest[0] if rest else True
            output = None

        label = str(name).casefold()
        text = str(value if value is not None else "—")
        has_member_label = any(token in label for token in member_labels)
        has_channel_label = any(token in label for token in channel_labels)

        if label.strip().endswith("المعرف") or "المعرّف" in label:
            if canonical == "log_member" and text.strip().isdigit():
                has_member_label = True
            elif canonical == "log_channel" and text.strip().isdigit():
                has_channel_label = True
            elif canonical == "log_roles" and text.strip().isdigit():
                text = f"<@&{int(text.strip())}>"

        if has_member_label:
            match = re.search(r"<@!?(\d+)>", text)
            raw_id = match.group(1) if match else (text.strip() if text.strip().isdigit() else None)
            if raw_id:
                mention = _member_mention(raw_id)
                if member_thumbnail is None and guild is not None:
                    resolver = getattr(guild, "get_member", None)
                    member = resolver(int(raw_id)) if resolver else None
                    member_thumbnail = _avatar(member)
                tail = text[match.end():] if match else ""
                text = mention + numeric_suffix.sub("", tail, count=1)

        if has_channel_label:
            match = re.search(r"<#(\d+)>", text)
            raw_id = match.group(1) if match else (text.strip() if text.strip().isdigit() else None)
            if raw_id:
                mention = _channel_mention(raw_id, guild)
                tail = text[match.end():] if match else ""
                tail = re.sub(r"\s+\(`#[^`]*`\)", "", tail, count=1)
                tail = numeric_suffix.sub("", tail, count=1)
                text = mention + tail

        if output is not None:
            output["value"] = text
            formatted.append(output)
        else:
            formatted.append((name, text, inline))

    return formatted, member_thumbnail


def _safe(value: Any, limit: int = 1024) -> str:
    text = str(value if value is not None else "—")
    return text.replace("@everyone", "@\u200beveryone").replace("@here", "@\u200bhere")[:limit]


def _code(value: Any) -> str:
    cleaned = _safe(value, 850).replace("```", "'''")
    return f"```{cleaned}```"


def create_elite_log_embed(
    title: str,
    description: str,
    color_hex: str,
    author_user=None,
    fields=None,
    thumbnail_url=None,
) -> discord.Embed:
    """Build the shared high-contrast audit card used by every category."""
    try:
        color = int(str(color_hex).replace("#", ""), 16)
    except (TypeError, ValueError):
        color = 0x5865F2
    embed = discord.Embed(
        title=_safe(title, 256),
        description=_safe(description, 4096),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if author_user:
        name = getattr(author_user, "display_name", None) or getattr(author_user, "name", "System")
        icon = _avatar(author_user)
        if icon:
            embed.set_author(name=_safe(name, 256), icon_url=icon)
        else:
            embed.set_author(name=_safe(name, 256))
    if thumbnail_url:
        embed.set_thumbnail(url=str(thumbnail_url))
    for item in fields or []:
        if isinstance(item, dict):
            name, value, inline = item.get("name"), item.get("value"), item.get("inline", True)
        else:
            name, value, *rest = item
            inline = rest[0] if rest else True
        embed.add_field(name=_safe(name, 256), value=_safe(value), inline=bool(inline))
    return embed


class Analytics(commands.Cog):
    """Guild-scoped audit dispatcher with event filters and durable invite tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._delivery_status: dict[tuple[int, str], dict[str, Any]] = {}
        self._invite_cache: dict[int, dict[str, dict[str, Any]]] = {}
        self._invite_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._invite_ready: set[int] = set()
        self._last_pin_at: dict[tuple[int, int], Any] = {}
        self._raw_event_seen: dict[tuple[str, int], float] = {}
        self._recent_event_keys: dict[tuple[int, str, int], float] = {}

    def _claim_recent_event(self, guild_id: int, event_type: str, target_id: int) -> bool:
        now = time.monotonic()
        key = (int(guild_id), str(event_type), int(target_id))
        previous = self._recent_event_keys.get(key)
        if previous is not None and now - previous < 5:
            return False
        self._recent_event_keys[key] = now
        if len(self._recent_event_keys) > 500:
            self._recent_event_keys = {
                item: timestamp
                for item, timestamp in self._recent_event_keys.items()
                if now - timestamp < 5
            }
        return True

    async def _routing(self, guild: discord.Guild) -> dict[str, int]:
        if guild.id not in LOG_ROUTING_CACHE:
            return await get_logging_channels(guild.id)
        return get_cached_logging_channels(guild.id)

    @staticmethod
    def event_options() -> dict[str, list[dict[str, str]]]:
        return {key: [dict(item) for item in options] for key, options in EVENT_OPTIONS.items()}

    async def _event_enabled(self, guild, category, event_type=None) -> bool:
        canonical = CATEGORY_ALIASES.get(category, category)
        settings = await get_logging_category_settings(guild.id)
        configured = settings.get(canonical)
        if configured is None:
            return True
        if not configured["enabled"]:
            return False
        if event_type and event_type not in configured["events"]:
            return False
        return True

    async def dashboard_state(self, guild) -> dict[str, Any]:
        routes = await self._routing(guild)
        settings = await get_logging_category_settings(guild.id)
        member = guild.me
        guild_permissions = getattr(member, "guild_permissions", None)
        can_view_audit = bool(getattr(guild_permissions, "view_audit_log", False))
        can_manage_guild = bool(getattr(guild_permissions, "manage_guild", False))
        message_content_requested = bool(
            getattr(getattr(self.bot, "intents", None), "message_content", False)
        )
        output_settings = {}
        statuses = {}
        for category in EVENT_OPTIONS:
            route_id = int(routes.get(category, 0) or 0)
            saved = settings.get(category)
            channel = guild.get_channel(route_id) if route_id else None
            output_settings[category] = {
                "enabled": bool(saved["enabled"]) if saved else bool(route_id),
                "events": list(saved["events"]) if saved else default_event_types(category),
            }
            status = "active"
            if not output_settings[category]["enabled"]:
                status = "disabled"
            elif not route_id:
                status = "unconfigured"
            elif channel is None:
                status = "missing_channel"
            else:
                permissions = channel.permissions_for(member) if member else None
                if permissions is None or not permissions.send_messages or not permissions.embed_links:
                    status = "missing_permissions"
                elif category == "log_security" and not can_view_audit:
                    status = "missing_permissions"
            runtime = self._delivery_status.get((guild.id, category))
            if runtime and runtime.get("status") == "error" and status == "active":
                status = "delivery_error"
            detail_parts = []
            if runtime and runtime.get("detail"):
                detail_parts.append(runtime["detail"])
            if category == "log_invites" and not can_manage_guild:
                detail_parts.append("Manage Guild غير متاحة؛ سيبقى مصدر الدعوة غير معروف.")
            if category in {"log_security", "log_sanctions", "log_roles", "log_channel", "log_server"} and not can_view_audit:
                detail_parts.append("View Audit Log غير متاحة؛ لن يُنسب الفاعل من التخمين.")
            if category == "log_message":
                if not message_content_requested:
                    detail_parts.append("Message Content Intent غير مفعّل في إعدادات البوت؛ قد لا يظهر محتوى الرسائل.")
                else:
                    detail_parts.append("تأكد من تفعيل Message Content Intent أيضاً في Developer Portal.")
            statuses[category] = {
                "state": status,
                "label": {
                    "active": "يعمل",
                    "disabled": "متوقف",
                    "unconfigured": "اختر قناة",
                    "missing_channel": "القناة غير متاحة",
                    "missing_permissions": "صلاحيات ناقصة",
                    "delivery_error": "تعذر الإرسال",
                }[status],
                "detail": " ".join(detail_parts),
            }
        return {
            "channels": {key: str(int(routes.get(key, 0) or 0)) for key in EVENT_OPTIONS},
            "settings": output_settings,
            "event_options": self.event_options(),
            "statuses": statuses,
            "categories": list(EVENT_OPTIONS),
            "capabilities": {
                "view_audit_log": can_view_audit,
                "manage_guild": can_manage_guild,
                "message_content_intent_requested": message_content_requested,
                "member_intent_requested": bool(
                    getattr(getattr(self.bot, "intents", None), "members", False)
                ),
            },
        }

    async def _fetch_invite_snapshot(self, guild):
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
            logger.warning(
                "[INVITE_LOG] Cannot fetch guild invites guild=%s; source attribution will be unknown",
                guild.id,
            )
            return None, False
        snapshot = {}
        for invite in invites:
            code = getattr(invite, "code", None)
            if not code:
                continue
            inviter = getattr(invite, "inviter", None)
            snapshot[str(code)] = {
                "uses": max(0, int(getattr(invite, "uses", 0) or 0)),
                "inviter_id": getattr(inviter, "id", None),
                "inviter_name": (
                    getattr(inviter, "global_name", None)
                    or getattr(inviter, "display_name", None)
                    or getattr(inviter, "name", None)
                ),
                "is_vanity": False,
            }
        vanity_known = False
        try:
            vanity = await guild.vanity_invite()
            vanity_known = True
            if vanity is not None and getattr(vanity, "code", None):
                snapshot[str(vanity.code)] = {
                    "uses": max(0, int(getattr(vanity, "uses", 0) or 0)),
                    "inviter_id": None,
                    "inviter_name": None,
                    "is_vanity": True,
                }
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            # A missing manage_guild permission or vanity feature is not evidence
            # that a previously cached vanity invite was deleted.
            pass
        return snapshot, vanity_known

    async def _persist_invites(self, guild_id: int, snapshot: dict[str, dict[str, Any]]):
        self._invite_cache[guild_id] = snapshot
        try:
            await replace_invite_tracking_cache(guild_id, snapshot)
        except Exception:
            logger.exception("[INVITE_LOG] Failed to persist invite cache guild=%s", guild_id)

    async def _refresh_invites(self, guild, *, report_missing=False):
        async with self._invite_locks[guild.id]:
            old = self._invite_cache.get(guild.id)
            if old is None:
                try:
                    old = await get_invite_tracking_cache(guild.id)
                except Exception:
                    logger.exception("[INVITE_LOG] Failed to restore invite cache guild=%s", guild.id)
                    old = {}
            current, vanity_known = await self._fetch_invite_snapshot(guild)
            if current is None:
                return
            if not vanity_known:
                for code, item in old.items():
                    if item.get("is_vanity") and code not in current:
                        current[code] = item
            if report_missing and old:
                for code, item in old.items():
                    if code in current or item.get("is_vanity") and not vanity_known:
                        continue
                    await self._log(
                        guild,
                        "log_invites",
                        "⌛ دعوة لم تعد متاحة",
                        "اختفت الدعوة من قائمة Discord؛ لا يوفّر الحدث سببًا موثوقًا يميّز الحذف اليدوي عن انتهاء الصلاحية.",
                        event_type="invite_delete",
                        fields=[
                            ("🔗 الرمز", f"`{code}`", True),
                            ("👤 صاحب الدعوة", f"<@{item['inviter_id']}>" if item.get("inviter_id") else "غير معروف", True),
                            ("📊 آخر عدد استخدامات", str(item.get("uses", 0)), True),
                            ("🧭 السبب", "حذف أو انتهاء صلاحية — غير محسوم", False),
                        ],
                    )
            await self._persist_invites(guild.id, current)
            self._invite_ready.add(guild.id)

    async def _identify_invite(self, guild):
        async with self._invite_locks[guild.id]:
            previous = self._invite_cache.get(guild.id)
            if previous is None:
                try:
                    previous = await get_invite_tracking_cache(guild.id)
                except Exception:
                    logger.exception("[INVITE_LOG] Failed to read saved invite cache guild=%s", guild.id)
                    previous = {}
            current, vanity_known = await self._fetch_invite_snapshot(guild)
            if current is None:
                return {"source": "unknown"}
            if not vanity_known:
                for code, item in previous.items():
                    if item.get("is_vanity") and code not in current:
                        current[code] = item
            if guild.id not in self._invite_ready:
                await self._persist_invites(guild.id, current)
                self._invite_ready.add(guild.id)
                return {"source": "unknown"}
            if set(current) != set(previous) or (
                not vanity_known and any(item.get("is_vanity") for item in previous.values())
            ):
                await self._persist_invites(guild.id, current)
                return {"source": "unknown"}
            changes = []
            for code, item in current.items():
                old_item = previous.get(code)
                if old_item is None or bool(item.get("is_vanity")) != bool(old_item.get("is_vanity")):
                    continue
                delta = int(item["uses"]) - int(old_item.get("uses", 0))
                if delta:
                    changes.append((code, item, delta))
            attribution = {"source": "unknown"}
            if len(changes) == 1 and changes[0][2] == 1:
                code, item, _ = changes[0]
                if item.get("is_vanity"):
                    attribution = {
                        "source": "vanity",
                        "code": code,
                        "uses": item["uses"],
                    }
                elif item.get("inviter_id"):
                    attribution = {
                        "source": "invite",
                        "code": code,
                        "inviter_id": int(item["inviter_id"]),
                        "inviter_name": item.get("inviter_name"),
                        "uses": item["uses"],
                    }
            await self._persist_invites(guild.id, current)
            return attribution

    @tasks.loop(minutes=20)
    async def _invite_expiry_poll(self):
        for guild in list(self.bot.guilds):
            try:
                await self._refresh_invites(guild, report_missing=True)
            except Exception:
                logger.exception("[INVITE_LOG] Periodic invite refresh failed guild=%s", guild.id)

    @_invite_expiry_poll.before_loop
    async def _before_invite_expiry_poll(self):
        await self.bot.wait_until_ready()

    async def cog_unload(self):
        self._invite_expiry_poll.cancel()

    async def _send(
        self,
        guild: discord.Guild,
        category: str,
        embed: discord.Embed,
        *,
        strict: bool = False,
        event_type: str | None = None,
        bypass_settings: bool = False,
    ) -> bool:
        canonical = CATEGORY_ALIASES.get(category, category)
        if not bypass_settings and not await self._event_enabled(guild, canonical, event_type):
            return False
        routing = await self._routing(guild)
        route = routing.get(canonical, routing.get(category, 0))
        channel = guild.get_channel(int(route)) if route else None
        if channel is None or not hasattr(channel, "send"):
            self._delivery_status[(guild.id, canonical)] = {
                "status": "error", "detail": "لا توجد قناة صالحة مرتبطة بهذا القسم.",
            }
            if strict:
                raise ValueError("log channel is not configured")
            return False
        try:
            me = guild.me
            if me:
                permissions = channel.permissions_for(me)
                if not permissions.send_messages or not permissions.embed_links:
                    self._delivery_status[(guild.id, canonical)] = {
                        "status": "error", "detail": "البوت يحتاج Send Messages وEmbed Links.",
                    }
                    if strict:
                        raise PermissionError("missing send or embed permission")
                    return False
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            self._delivery_status.pop((guild.id, canonical), None)
            return True
        except (discord.Forbidden, discord.HTTPException):
            self._delivery_status[(guild.id, canonical)] = {
                "status": "error", "detail": "رفض Discord الإرسال أو تعذر الاتصال.",
            }
            logger.debug("Unable to dispatch %s audit for guild %s", category, guild.id, exc_info=True)
            if strict:
                raise
            return False

    async def _audit(
        self,
        guild: discord.Guild,
        action: discord.AuditLogAction,
        target_id: int | None = None,
    ):
        try:
            now = discord.utils.utcnow()
            matches = []
            async for entry in guild.audit_logs(limit=8, action=action):
                target = getattr(entry, "target", None)
                if target_id is not None and getattr(target, "id", None) != int(target_id):
                    continue
                created_at = getattr(entry, "created_at", None)
                if created_at is None:
                    continue
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                age = abs((now - created_at).total_seconds())
                if age <= 15:
                    matches.append(entry)
            # If multiple recent entries fit, the actor cannot be assigned
            # safely from this event alone.
            if len(matches) == 1:
                return matches[0]
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            return None
        return None

    def _embed(self, guild, category, title, description, *, author=None, fields=None, thumbnail=None, color=None):
        formatted_fields, member_thumbnail = _format_log_fields(
            guild, category, fields or [],
        )
        embed = create_elite_log_embed(
            title,
            description,
            f"#{color or COLORS[category][0]:06X}",
            author_user=author,
            fields=formatted_fields,
            thumbnail_url=thumbnail or member_thumbnail,
        )
        icon = getattr(getattr(guild, "icon", None), "url", None)
        guild_name = _safe(getattr(guild, "name", "سيرفر Discord"), 128)
        if icon:
            embed.set_footer(text=f"PRIME • سجل التدقيق • {guild_name}", icon_url=str(icon))
        else:
            embed.set_footer(text=f"PRIME • سجل التدقيق • {guild_name}")
        return embed

    async def _log(
        self,
        guild,
        category,
        title,
        description,
        *,
        author=None,
        fields=None,
        thumbnail=None,
        color=None,
        strict: bool = False,
        event_type: str | None = None,
        bypass_settings: bool = False,
    ):
        return await self._send(
            guild,
            category,
            self._embed(
                guild,
                category,
                title,
                description,
                author=author,
                fields=fields,
                thumbnail=thumbnail,
                color=color,
            ),
            strict=strict,
            event_type=event_type,
            bypass_settings=bypass_settings,
        )

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await get_logging_channels(guild.id)
                await self._refresh_invites(guild, report_missing=True)
            except Exception:
                logger.exception("Unable to restore analytics logging guild=%s", guild.id)
        if not self._invite_expiry_poll.is_running():
            self._invite_expiry_poll.start()

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or getattr(message.author, "bot", False):
            return
        log_event = await self._event_enabled(message.guild, "log_message", "message_delete")
        log_content = await self._event_enabled(message.guild, "log_message", "message_delete_content")
        if not log_event and not log_content:
            return
        attachments = "\n".join(getattr(item, "url", "") for item in message.attachments) or "لا توجد"
        fields = [
            ("👤 كاتب الرسالة", _member_mention(message.author), True),
            ("💬 القناة", _channel_mention(message.channel), True),
            ("🔗 الرسالة", f"[فتح الرسالة]({message.jump_url})", True),
            ("📎 المرفقات", _safe(attachments, 900), False),
        ]
        if log_content:
            content = message.content or (
                "المحتوى غير متاح من Discord Gateway أو الذاكرة المؤقتة؛ لا يمكن الجزم إن كانت الرسالة فارغة."
            )
            fields.append(("🗑️ المحتوى المحذوف", _code(content), False))
        await self._log(
            message.guild,
            "log_message",
            "🗑️ حذف رسالة",
            "تم رصد حذف رسالة. هوية من حذفها غير مؤكدة من حدث الرسالة وحده.",
            color=0xEF4444,
            fields=fields,
            thumbnail=_avatar(message.author),
            event_type="message_delete" if log_event else "message_delete_content",
            bypass_settings=True,
        )

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or getattr(before.author, "bot", False):
            return
        content_changed = before.content != after.content
        before_attachments = tuple((item.id, item.filename, item.url) for item in before.attachments)
        after_attachments = tuple((item.id, item.filename, item.url) for item in after.attachments)
        attachments_changed = before_attachments != after_attachments
        embeds_changed = before.embeds != after.embeds
        if not content_changed and not attachments_changed and not embeds_changed:
            return
        log_event = await self._event_enabled(before.guild, "log_message", "message_edit")
        log_content = await self._event_enabled(before.guild, "log_message", "message_edit_content")
        if not log_event and not (log_content and content_changed):
            return
        fields = [
            ("👤 كاتب الرسالة", _member_mention(before.author), True),
            ("💬 القناة", f"{_channel_mention(before.channel)} • [فتح الرسالة]({after.jump_url})", True),
        ]
        if attachments_changed:
            fields.append((
                "📎 تغييرات المرفقات",
                f"قبل: {', '.join(item[1] for item in before_attachments) or 'لا توجد'}\n"
                f"بعد: {', '.join(item[1] for item in after_attachments) or 'لا توجد'}",
                False,
            ))
        if embeds_changed:
            fields.append(("🧩 التضمينات", f"قبل: {len(before.embeds)} • بعد: {len(after.embeds)}", True))
        if log_content and content_changed:
            before_content = before.content or "المحتوى السابق غير متاح من Discord Gateway أو الذاكرة المؤقتة."
            after_content = after.content or "المحتوى الجديد غير متاح من Discord Gateway أو الذاكرة المؤقتة."
            fields.extend([
                ("📝 المحتوى السابق", _code(before_content), False),
                ("✏️ المحتوى الجديد", _code(after_content), False),
            ])
        await self._log(
            before.guild,
            "log_message",
            "✏️ تعديل رسالة",
            "تم رصد تعديل رسالة. إذا كان المحتوى غير ظاهر، فتحقق من تفعيل Message Content Intent في Discord Developer Portal.",
            color=0xF59E0B,
            fields=fields,
            thumbnail=_avatar(before.author),
            event_type="message_edit" if log_event else "message_edit_content",
            bypass_settings=True,
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = guild.get_channel_or_thread(payload.channel_id)
        cached = list(getattr(payload, "cached_messages", ()) or ())
        authors = sorted({
            _member_mention(item.author) for item in cached
            if getattr(item, "author", None) and not getattr(item.author, "bot", False)
        })
        fields = [
            ("🔢 العدد", str(len(payload.message_ids)), True),
            ("💬 القناة", _channel_mention(channel or payload.channel_id, guild), True),
            ("🔗 الرسائل", "حذف جماعي؛ Discord لا يوفّر روابط صالحة للرسائل المحذوفة.", False),
        ]
        if authors:
            fields.append(("👤 كتّاب معروفون من الذاكرة المؤقتة", ", ".join(authors[:10]), False))
        await self._log(
            guild, "log_message", "🧹 حذف جماعي للرسائل",
            "رصد Discord حذف مجموعة رسائل؛ قد لا تتوفر الرسائل غير الموجودة في الذاكرة المؤقتة.",
            fields=fields, color=0xEF4444, event_type="message_bulk_delete",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        added = [role for role in after.roles if role not in before.roles and not role.is_default()]
        removed = [role for role in before.roles if role not in after.roles and not role.is_default()]
        role_events = []
        add_enabled = bool(added) and await self._event_enabled(
            after.guild, "log_roles", "member_role_add"
        )
        remove_enabled = bool(removed) and await self._event_enabled(
            after.guild, "log_roles", "member_role_remove"
        )
        if add_enabled:
            role_events.append("member_role_add")
        if remove_enabled:
            role_events.append("member_role_remove")
        if role_events:
            entry = await self._audit(after.guild, discord.AuditLogAction.member_role_update, after.id)
            moderator = getattr(entry, "user", None)
            fields = [("👤 العضو", _member_mention(after), True)]
            if add_enabled:
                fields.append(("➕ الرتب المضافة", ", ".join(role.mention for role in added), False))
            if remove_enabled:
                fields.append(("➖ الرتب المسحوبة", ", ".join(role.mention for role in removed), False))
            fields.append(("🧾 المنفذ", moderator.mention if moderator else "غير معروف", True))
            await self._log(
                after.guild, "log_roles", "🎭 تحديث رتب عضو",
                "تم رصد تغير في رتب العضو.",
                author=moderator, thumbnail=_avatar(after),
                color=0x8B5CF6, fields=fields,
                event_type=role_events[0], bypass_settings=True,
            )

        member_changes = []
        if before.nick != after.nick:
            member_changes.append(("nickname_update", "الاسم المستعار", before.nick, after.nick))
        before_guild_avatar = getattr(before, "guild_avatar", None)
        after_guild_avatar = getattr(after, "guild_avatar", None)
        if before_guild_avatar != after_guild_avatar:
            member_changes.append(("guild_avatar_update", "صورة العضو في السيرفر", "تغيرت", "تغيرت"))
        if getattr(before, "pending", None) != getattr(after, "pending", None):
            member_changes.append((
                "verification_update", "فحص قواعد Discord",
                "معلّق" if before.pending else "مكتمل",
                "معلّق" if after.pending else "مكتمل",
            ))
        selected_member_changes = [
            item for item in member_changes
            if await self._event_enabled(after.guild, "log_member", item[0])
        ]
        if selected_member_changes:
            fields = [("👤 العضو", _member_mention(after), True)]
            fields.extend(
                (label, f"`{old or '—'}` → `{new or '—'}`", True)
                for _, label, old, new in selected_member_changes
            )
            await self._log(
                after.guild, "log_member", "👤 تحديث بيانات عضو",
                "تم رصد تغييرات في بيانات العضو. حالة التحقق هنا تعني فحص Membership Screening من Discord فقط.",
                fields=fields, thumbnail=_avatar(after),
                event_type=selected_member_changes[0][0], bypass_settings=True,
            )

        old_timeout = getattr(before, "timed_out_until", None)
        new_timeout = getattr(after, "timed_out_until", None)
        if old_timeout != new_timeout:
            adding_timeout = new_timeout is not None and new_timeout > discord.utils.utcnow()
            event_type = "timeout_add" if adding_timeout else "timeout_remove"
            if await self._event_enabled(after.guild, "log_sanctions", event_type):
                if not self._claim_recent_event(after.guild.id, event_type, after.id):
                    return
                entry = await self._audit(after.guild, discord.AuditLogAction.member_update, after.id)
                moderator = getattr(entry, "user", None)
                await self._log(
                    after.guild, "log_sanctions",
                    "🤐 إضافة Timeout" if adding_timeout else "🔊 إزالة Timeout",
                    "تم رصد تحديث حالة Timeout عبر Discord.",
                    author=moderator,
                    fields=[
                        ("👤 العضو", _member_mention(after), True),
                        ("⌛ الانتهاء", new_timeout.isoformat() if adding_timeout else "لا يوجد", True),
                        ("🧾 المنفذ", moderator.mention if moderator else "غير معروف", True),
                        ("📌 السبب", getattr(entry, "reason", None) or "غير متاح من الحدث", False),
                    ],
                    thumbnail=_avatar(after), event_type=event_type,
                )

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User):
        changes = []
        if before.name != after.name:
            changes.append(("username_update", "Username", before.name, after.name))
        if getattr(before, "global_name", None) != getattr(after, "global_name", None):
            changes.append((
                "display_name_update", "Display Name",
                getattr(before, "global_name", None), getattr(after, "global_name", None),
            ))
        if getattr(before, "avatar", None) != getattr(after, "avatar", None):
            changes.append(("avatar_update", "صورة الحساب", "تغيرت", "تغيرت"))
        if not changes:
            return
        for guild in list(self.bot.guilds):
            member = guild.get_member(after.id)
            if member is None:
                continue
            selected = [
                item for item in changes
                if await self._event_enabled(guild, "log_member", item[0])
            ]
            if not selected:
                continue
            fields = [("👤 الحساب", _member_mention(after), True)]
            fields.extend(
                (label, f"`{old or '—'}` → `{new or '—'}`", True)
                for _, label, old, new in selected
            )
            await self._log(
                guild, "log_member", "🪪 تحديث ملف مستخدم",
                "تم رصد تغيير في ملف الحساب. Discord لا يحدد دائمًا من أجرى تغيير الملف.",
                fields=fields, thumbnail=_avatar(after),
                event_type=selected[0][0], bypass_settings=True,
            )

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        entry = await self._audit(role.guild, discord.AuditLogAction.role_create, role.id)
        await self._log(
            role.guild, "log_roles", "🟣 إنشاء رتبة", f"تم إنشاء {role.mention}.",
            author=getattr(entry, "user", None),
            event_type="role_create",
            fields=[
                ("🎭 الاسم", f"`{role.name}`", True),
                ("🎨 اللون", f"`{role.color}`", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        entry = await self._audit(role.guild, discord.AuditLogAction.role_delete, role.id)
        await self._log(
            role.guild, "log_roles", "🗑️ حذف رتبة", f"تم حذف الرتبة `{role.name}`.",
            author=getattr(entry, "user", None),
            event_type="role_delete",
            fields=[
                ("🎨 اللون", f"`{role.color}`", True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        general_changes = []
        if before.name != after.name:
            general_changes.append(f"الاسم: `{before.name}` → `{after.name}`")
        if before.color != after.color:
            general_changes.append(f"اللون: `{before.color}` → `{after.color}`")
        permissions_changed = before.permissions != after.permissions
        if not general_changes and not permissions_changed:
            return
        event_types = []
        visible_changes = []
        if general_changes and await self._event_enabled(after.guild, "log_roles", "role_update"):
            event_types.append("role_update")
            visible_changes.extend(general_changes)
        if permissions_changed and await self._event_enabled(after.guild, "log_roles", "role_permissions"):
            event_types.append("role_permissions")
            visible_changes.append("الصلاحيات: تم تحديث مجموعة الصلاحيات")
        if not event_types:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.role_update, after.id)
        await self._log(
            after.guild, "log_roles", "🛠️ تعديل رتبة", "تم تحديث إعدادات رتبة.",
            author=getattr(entry, "user", None),
            event_type=event_types[0], bypass_settings=True,
            fields=[
                ("🎭 الرتبة", after.mention, True),
                ("📝 التغييرات", "\n".join(visible_changes), False),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    def _channel_type(self, channel):
        return "صوتية" if isinstance(channel, discord.VoiceChannel) else "تصنيف" if isinstance(channel, discord.CategoryChannel) else "نصية"

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        entry = await self._audit(channel.guild, discord.AuditLogAction.channel_create, channel.id)
        await self._log(
            channel.guild, "log_channels", "🟢 إنشاء قناة", "تم إنشاء قناة جديدة.",
            author=getattr(entry, "user", None),
            event_type="channel_create",
            fields=[
                ("📁 القناة", _channel_mention(channel), True),
                ("🔖 النوع", self._channel_type(channel), True),
                ("📂 الفئة الأب", _channel_mention(channel.category) if getattr(channel, "category", None) else "بدون", True),
                ("🧾 المسؤول", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        entry = await self._audit(channel.guild, discord.AuditLogAction.channel_delete, channel.id)
        await self._log(
            channel.guild, "log_channels", "🔴 حذف قناة", "تم حذف قناة من السيرفر.",
            author=getattr(entry, "user", None),
            event_type="channel_delete",
            color=0xEF4444,
            fields=[
                ("📁 القناة", _channel_mention(channel), True),
                ("🔖 النوع", self._channel_type(channel), True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        general_changes = []
        for label, old, new in (
            ("الاسم", before.name, after.name),
            ("الموضوع", getattr(before, "topic", None), getattr(after, "topic", None)),
            ("Slowmode", getattr(before, "slowmode_delay", None), getattr(after, "slowmode_delay", None)),
            ("Bitrate", getattr(before, "bitrate", None), getattr(after, "bitrate", None)),
            ("NSFW", getattr(before, "nsfw", None), getattr(after, "nsfw", None)),
            ("حد المستخدمين", getattr(before, "user_limit", None), getattr(after, "user_limit", None)),
            ("منطقة الصوت", getattr(before, "rtc_region", None), getattr(after, "rtc_region", None)),
            ("مدة أرشفة Threads", getattr(before, "default_auto_archive_duration", None), getattr(after, "default_auto_archive_duration", None)),
        ):
            if old != new:
                general_changes.append(f"{label}: `{old}` → `{new}`")
        overwrites_changed = getattr(before, "overwrites", {}) != getattr(after, "overwrites", {})
        if not general_changes and not overwrites_changed:
            return
        event_types = []
        visible_changes = []
        if general_changes and await self._event_enabled(after.guild, "log_channel", "channel_update"):
            event_types.append("channel_update")
            visible_changes.extend(general_changes)
        if overwrites_changed and await self._event_enabled(after.guild, "log_channel", "overwrite_update"):
            event_types.append("overwrite_update")
            visible_changes.append("تم تحديث صلاحيات الأدوار والأعضاء في القناة.")
        if not event_types:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.channel_update, after.id)
        await self._log(
            after.guild, "log_channels", "🛠️ تعديل قناة", "تم تعديل خصائص قناة.",
            author=getattr(entry, "user", None),
            event_type=event_types[0], bypass_settings=True,
            fields=[
                ("📁 القناة", _channel_mention(after), True),
                ("📝 التغييرات", "\n".join(visible_changes), False),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        entry = await self._audit(thread.guild, discord.AuditLogAction.thread_create, thread.id)
        await self._log(
            thread.guild, "log_channel", "🧵 إنشاء Thread",
            "تم رصد إنشاء Thread.",
            author=getattr(entry, "user", None), event_type="thread_create",
            fields=[
                ("🧵 Thread", _channel_mention(thread), True),
                ("📍 القناة الأم", _channel_mention(getattr(thread, "parent", None)), True),
                ("🧾 المنفذ", entry.user.mention if entry and entry.user else "غير معروف", True),
            ],
        )

    @commands.Cog.listener()
    async def on_thread_update(self, before, after):
        changes = []
        for label, old, new in (
            ("الاسم", before.name, after.name),
            ("الأرشفة", getattr(before, "archived", None), getattr(after, "archived", None)),
            ("القفل", getattr(before, "locked", None), getattr(after, "locked", None)),
            ("مدة الأرشفة", getattr(before, "auto_archive_duration", None), getattr(after, "auto_archive_duration", None)),
            ("إمكانية الدعوة", getattr(before, "invitable", None), getattr(after, "invitable", None)),
        ):
            if old != new:
                changes.append(f"{label}: `{old}` → `{new}`")
        if not changes:
            return
        entry = await self._audit(after.guild, discord.AuditLogAction.thread_update, after.id)
        await self._log(
            after.guild, "log_channel", "🛠️ تعديل Thread",
            "تم تحديث إعدادات Thread.",
            author=getattr(entry, "user", None), event_type="thread_update",
            fields=[
                ("🧵 Thread", _channel_mention(after), True),
                ("📝 التغييرات", "\n".join(changes), False),
                ("🧾 المنفذ", entry.user.mention if entry and entry.user else "غير معروف", True),
            ],
        )

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        entry = await self._audit(thread.guild, discord.AuditLogAction.thread_delete, thread.id)
        await self._log(
            thread.guild, "log_channel", "🗑️ حذف Thread",
            "تم رصد حذف Thread.",
            author=getattr(entry, "user", None), event_type="thread_delete",
            fields=[
                ("🧵 Thread", _channel_mention(thread), True),
                ("🧾 المنفذ", entry.user.mention if entry and entry.user else "غير معروف", True),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_channel_pins_update(self, channel, last_pin):
        key = (channel.guild.id, channel.id)
        now = time.monotonic()
        if now - self._last_pin_at.get(key, 0) < 2:
            return
        self._last_pin_at[key] = now
        await self._log(
            channel.guild, "log_message", "📌 تحديث الرسائل المثبتة",
            "تغيّر سجل الرسائل المثبتة في القناة. Discord لا يحدد الفاعل ضمن هذا الحدث.",
            event_type="pins_update",
            fields=[
                ("💬 القناة", channel.mention, True),
                ("🕒 آخر تثبيت معروف", last_pin.isoformat() if last_pin else "لا توجد رسالة مثبتة", True),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        old = {item.id: item for item in before}
        new = {item.id: item for item in after}
        added = [item.name for item_id, item in new.items() if item_id not in old]
        removed = [item.name for item_id, item in old.items() if item_id not in new]
        renamed = [
            f"`{old[item_id].name}` → `{item.name}`"
            for item_id, item in new.items()
            if item_id in old and item.name != old[item_id].name
        ]
        if not (added or removed or renamed):
            return
        await self._log(
            guild, "log_server", "😀 تحديث Emoji",
            "أرسل Discord قائمة Emoji بعد تغييرها؛ لا يتضمن هذا الحدث منفذ التغيير.",
            event_type="emoji_update",
            fields=[
                ("➕ إضافة", ", ".join(added[:20]) or "لا يوجد", False),
                ("➖ حذف", ", ".join(removed[:20]) or "لا يوجد", False),
                ("✏️ تعديل الاسم", ", ".join(renamed[:20]) or "لا يوجد", False),
            ],
        )

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after):
        old = {item.id: item for item in before}
        new = {item.id: item for item in after}
        added = [item.name for item_id, item in new.items() if item_id not in old]
        removed = [item.name for item_id, item in old.items() if item_id not in new]
        renamed = [
            f"`{old[item_id].name}` → `{item.name}`"
            for item_id, item in new.items()
            if item_id in old and item.name != old[item_id].name
        ]
        if not (added or removed or renamed):
            return
        await self._log(
            guild, "log_server", "🏷️ تحديث Sticker",
            "أرسل Discord قائمة Sticker بعد تغييرها؛ لا يتضمن هذا الحدث منفذ التغيير.",
            event_type="sticker_update",
            fields=[
                ("➕ إضافة", ", ".join(added[:20]) or "لا يوجد", False),
                ("➖ حذف", ", ".join(removed[:20]) or "لا يوجد", False),
                ("✏️ تعديل الاسم", ", ".join(renamed[:20]) or "لا يوجد", False),
            ],
        )

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel):
        await self._log(
            channel.guild, "log_server", "🔗 تغيّر Webhook",
            "أبلغ Discord بتغيّر Webhooks في القناة. هذا الحدث لا يحدد هل تم الإنشاء أو التعديل أو الحذف، ولا يحدد الفاعل.",
            event_type="webhook_update",
            fields=[("📍 القناة", channel.mention, True)],
        )

    @commands.Cog.listener()
    async def on_guild_integrations_update(self, guild):
        await self._log(
            guild, "log_server", "🔌 تغيّر تكاملات السيرفر",
            "أبلغ Discord بتحديث التكاملات؛ لا تتوفر تفاصيل العنصر أو الفاعل في هذا الحدث.",
            event_type="integration_update",
        )

    @commands.Cog.listener()
    async def on_automod_action(self, execution):
        guild = getattr(execution, "guild", None)
        if guild is None:
            return
        user = getattr(execution, "user", None)
        channel = getattr(execution, "channel", None)
        action = getattr(execution, "action", None)
        fields = [
            ("🛡️ القاعدة", getattr(execution, "rule_name", None) or "غير متاح", True),
            ("⚙️ الإجراء", getattr(getattr(action, "type", None), "name", None) or "غير متاح", True),
            ("👤 العضو", _member_mention(user) if user else "غير متاح", True),
            ("📍 القناة", _channel_mention(channel, guild), True),
        ]
        message_id = getattr(execution, "message_id", None)
        if message_id and channel:
            fields.append(("🔗 الرسالة", f"https://discord.com/channels/{guild.id}/{channel.id}/{message_id}", False))
        await self._log(
            guild, "log_automod", "🛡️ إجراء AutoMod",
            "نفّذ Discord إجراءً آلياً. العضو المذكور هو الهدف/كاتب الرسالة، وليس بالضرورة منفذ الإجراء.",
            event_type="automod_action", fields=fields,
            thumbnail=_avatar(user), color=COLORS["log_automod"][0],
        )

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule):
        await self._log(
            rule.guild, "log_automod", "🛡️ إنشاء قاعدة AutoMod",
            "تم رصد إنشاء قاعدة AutoMod.",
            event_type="automod_rule_create",
            fields=[("📜 القاعدة", f"`{rule.name}` (`{rule.id}`)", True)],
        )

    @commands.Cog.listener()
    async def on_automod_rule_update(self, before, after):
        await self._log(
            after.guild, "log_automod", "🛠️ تعديل قاعدة AutoMod",
            "تم رصد تحديث قاعدة AutoMod.",
            event_type="automod_rule_update",
            fields=[
                ("📜 قبل", f"`{before.name}`", True),
                ("📜 بعد", f"`{after.name}`", True),
                ("🆔 القاعدة", str(after.id), True),
            ],
        )

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule):
        await self._log(
            rule.guild, "log_automod", "🗑️ حذف قاعدة AutoMod",
            "تم رصد حذف قاعدة AutoMod.",
            event_type="automod_rule_delete",
            fields=[("📜 القاعدة", f"`{rule.name}` (`{rule.id}`)", True)],
        )

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        guild = getattr(entry, "guild", None)
        if guild is None:
            return
        action_name = getattr(getattr(entry, "action", None), "name", "unknown")
        permission_actions = {
            "overwrite_create", "overwrite_update", "overwrite_delete",
        }
        event_type = (
            "security_permission_update"
            if action_name in permission_actions
            else "audit_action"
        )
        if not await self._event_enabled(guild, "log_security", event_type):
            return
        try:
            claimed = await claim_logging_audit_entry(guild.id, entry.id)
        except Exception:
            logger.exception("[AUDIT_LOG] Failed to deduplicate audit entry guild=%s", guild.id)
            claimed = True
        if not claimed:
            return
        target = getattr(entry, "target", None)
        if isinstance(target, (discord.User, discord.Member)):
            target_text = _member_mention(target)
        elif getattr(target, "mention", None):
            target_text = str(target.mention)
        else:
            target_text = _safe(target, 300)
        fields = [
            ("⚙️ الإجراء", _safe(getattr(entry.action, "name", entry.action), 200), True),
            ("🎯 الهدف", target_text, True),
            ("🧾 المنفذ", entry.user.mention if entry.user else "غير معروف", True),
            ("📌 السبب", entry.reason or "غير متاح", False),
            ("🆔 إدخال التدقيق", str(entry.id), True),
        ]
        await self._log(
            guild, "log_security", "🛡️ إجراء إداري في Audit Log",
            "إدخال مستلم من سجل تدقيق Discord. تتطلب هوية المنفذ وتفاصيل التغييرات أن تسمح صلاحية View Audit Log بذلك.",
            author=entry.user, thumbnail=_avatar(target) if isinstance(target, (discord.User, discord.Member)) else None,
            event_type=event_type, fields=fields,
        )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        entry = await self._audit(guild, discord.AuditLogAction.ban, user.id)
        await self._log(
            guild, "log_moderation", "🔨 حظر عضو (Ban)", "تم حظر عضو من السيرفر.",
            author=getattr(entry, "user", None), thumbnail=_avatar(user),
            event_type="ban",
            fields=[
                ("🎯 العضو", _member_mention(user), True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
                ("📌 السبب", getattr(entry, "reason", None) or "غير محدد", False),
            ],
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        entry = await self._audit(guild, discord.AuditLogAction.unban, user.id)
        await self._log(
            guild, "log_moderation", "🔓 فك حظر عضو (Unban)", "تم فك حظر عضو.",
            author=getattr(entry, "user", None), thumbnail=_avatar(user),
            event_type="unban",
            color=0x10B981,
            fields=[
                ("🎯 العضو", _member_mention(user), True),
                ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        kick_entry = await self._audit(guild, discord.AuditLogAction.kick, member.id)
        if kick_entry is not None:
            await self._log(
                guild, "log_sanctions", "👢 طرد عضو (Kick)", "تم رصد طرد عضو.",
                author=getattr(kick_entry, "user", None), thumbnail=_avatar(member),
                event_type="kick",
                fields=[
                    ("🎯 العضو", _member_mention(member), True),
                    ("🧾 المنفذ", kick_entry.user.mention if kick_entry.user else "غير معروف", True),
                    ("📌 السبب", kick_entry.reason or "غير محدد", False),
                ],
            )
        await self._log(
            guild, "log_member", "🔴 انتهاء عضوية عضو",
            "انتهت عضوية هذا الحساب في السيرفر. حدث Discord لا يحدد وحده إن كان السبب مغادرةً طوعية أو إجراءً إدارياً.",
            thumbnail=_avatar(member), event_type="member_leave",
            fields=[("👤 العضو", _member_mention(member), True)],
        )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if (
            before.channel == after.channel
            and before.mute == after.mute and before.deaf == after.deaf
            and getattr(before, "self_mute", None) == getattr(after, "self_mute", None)
            and getattr(before, "self_deaf", None) == getattr(after, "self_deaf", None)
        ):
            return
        selected = []
        fields = [("👤 العضو", _member_mention(member), True)]
        transition = None
        transition_fields = []
        actor = None
        if before.channel != after.channel:
            if before.channel and after.channel:
                transition = ("voice_move", "🔄 انتقال بين الرومات", "انتقل العضو بين قناتين صوتيتين.")
                transition_fields.extend([
                    ("من", _channel_mention(before.channel), True),
                    ("إلى", _channel_mention(after.channel), True),
                ])
            elif after.channel:
                transition = ("voice_join", "🟢 انضمام لروم صوتي", "انضم عضو إلى قناة صوتية.")
                transition_fields.append(("📍 القناة", _channel_mention(after.channel), True))
            else:
                entry = await self._audit(member.guild, discord.AuditLogAction.member_disconnect, member.id)
                if entry:
                    transition = ("voice_disconnect", "🔌 فصل عضو من الروم", "تم رصد فصل إداري حديث يطابق هذا العضو.")
                    actor = entry.user
                    transition_fields.append(("🧾 المنفذ", actor.mention if actor else "غير معروف", True))
                else:
                    transition = ("voice_leave", "🔴 مغادرة روم صوتي", "غادر عضو قناة صوتية؛ لا يثبت الحدث وحده أن مغادرته كانت إدارية.")
                transition_fields.append(("📍 القناة", _channel_mention(before.channel), True))
            if await self._event_enabled(member.guild, "log_voice", transition[0]):
                selected.append(transition)
                fields.extend(transition_fields)
        mute_changes = []
        if before.mute != after.mute:
            mute_changes.append(("كتم السيرفر", before.mute, after.mute))
        if getattr(before, "self_mute", None) != getattr(after, "self_mute", None):
            mute_changes.append(("كتم العضو لنفسه", before.self_mute, after.self_mute))
        if mute_changes and await self._event_enabled(member.guild, "log_voice", "voice_mute"):
            selected.append(("voice_mute", "🎚️ تغيير حالة المايك", "تم تغيير حالة كتم المايك."))
            fields.extend((f"🔇 {label}", f"`{old}` → `{new}`", True) for label, old, new in mute_changes)
        deaf_changes = []
        if before.deaf != after.deaf:
            deaf_changes.append(("صمم السيرفر", before.deaf, after.deaf))
        if getattr(before, "self_deaf", None) != getattr(after, "self_deaf", None):
            deaf_changes.append(("الصمم الذاتي", before.self_deaf, after.self_deaf))
        if deaf_changes and await self._event_enabled(member.guild, "log_voice", "voice_deafen"):
            selected.append(("voice_deafen", "🙉 تغيير حالة السماعة", "تم تغيير حالة الصمم."))
            fields.extend((f"🙉 {label}", f"`{old}` → `{new}`", True) for label, old, new in deaf_changes)
        if selected:
            event_type, title, description = selected[0]
            await self._log(
                member.guild, "log_voice", title, description,
                author=actor, thumbnail=_avatar(member),
                fields=fields, color=0x06B6D4,
                event_type=event_type, bypass_settings=True,
            )

    async def log_timeout(
        self, guild, member, moderator, minutes, reason, expires_at=None,
        source_channel=None,
    ):
        if not await self._event_enabled(guild, "log_sanctions", "timeout_add"):
            return
        if not self._claim_recent_event(guild.id, "timeout_add", member.id):
            return
        fields = [
            ("🎯 العضو", _member_mention(member), True),
            ("⏱️ المدة", f"{minutes} دقيقة", True),
            ("🧾 المنفذ", getattr(moderator, "mention", "غير معروف"), True),
            ("📌 السبب", reason, False),
            ("⌛ الانتهاء", expires_at or "غير محدد", False),
        ]
        if source_channel is not None:
            fields.append(("📍 قناة الأمر", _channel_mention(source_channel, guild), True))
        await self._log(
            guild, "log_moderation", "🤐 كتم عضو (Timeout)", "تم تطبيق كتم مؤقت على عضو.",
            author=moderator, thumbnail=_avatar(member),
            event_type="timeout_add",
            fields=fields,
        )

    async def log_warning(self, guild, member, moderator, reason, total, source_channel=None):
        fields = [
            ("👤 العضو", _member_mention(member), True),
            ("📊 الإجمالي الحالي", str(total), True),
            ("🧾 المنفذ", getattr(moderator, "mention", "غير معروف"), True),
            ("📌 السبب", reason, False),
        ]
        if source_channel is not None:
            fields.append(("📍 قناة الأمر", _channel_mention(source_channel, guild), True))
        await self._log(
            guild, "log_warnings", "⚠️ إصدار إنذار لعضو", "تم تسجيل مخالفة جديدة.",
            author=moderator, thumbnail=_avatar(member), color=0xEAB308,
            event_type="warning",
            fields=fields,
        )

    async def log_automod(self, guild, title, description, *, actor=None, fields=None, color=None):
        """Additive bridge for the existing Auto-Mod logger."""
        await self._log(
            guild,
            "log_automod",
            title,
            description,
            author=actor,
            fields=fields,
            color=color or COLORS["log_automod"][0],
            event_type="automod_action",
        )

    async def log_ticket_event(self, guild, title, description, *, actor=None, fields=None, color=None):
        """Additive bridge used by ticket operations without replacing ticket storage."""
        await self._log(
            guild,
            "log_ticket",
            title,
            description,
            author=actor,
            fields=fields,
            color=color or COLORS["log_ticket"][0],
            event_type="ticket_action",
        )

    @commands.Cog.listener("on_guild_update")
    async def on_guild_update_audit(self, before: discord.Guild, after: discord.Guild):
        changes = []
        for label, old, new in (
            ("الاسم", before.name, after.name),
            ("الوصف", getattr(before, "description", None), getattr(after, "description", None)),
            ("مستوى التحقق", getattr(before, "verification_level", None), getattr(after, "verification_level", None)),
                ("مستوى الإشعارات الافتراضي", getattr(before, "default_notifications", None), getattr(after, "default_notifications", None)),
                ("مرشح المحتوى الصريح", getattr(before, "explicit_content_filter", None), getattr(after, "explicit_content_filter", None)),
                ("اللغة المفضلة", getattr(before, "preferred_locale", None), getattr(after, "preferred_locale", None)),
                ("كود Vanity", getattr(before, "vanity_url_code", None), getattr(after, "vanity_url_code", None)),
                ("أيقونة السيرفر", getattr(getattr(before, "icon", None), "key", None), getattr(getattr(after, "icon", None), "key", None)),
                ("غلاف السيرفر", getattr(getattr(before, "banner", None), "key", None), getattr(getattr(after, "banner", None), "key", None)),
        ):
            if old != new:
                changes.append(f"{label}: `{old}` → `{new}`")
        for label, channel_attr, id_attr in (
            ("قناة AFK", "afk_channel", "afk_channel_id"),
            ("قناة النظام", "system_channel", "system_channel_id"),
            ("قناة القواعد", "rules_channel", "rules_channel_id"),
        ):
            old_id = getattr(before, id_attr, None)
            new_id = getattr(after, id_attr, None)
            if old_id != new_id:
                old_channel = getattr(before, channel_attr, None) or old_id
                new_channel = getattr(after, channel_attr, None) or new_id
                old_text = _channel_mention(old_channel, before) if old_id else "بدون"
                new_text = _channel_mention(new_channel, after) if new_id else "بدون"
                changes.append(f"{label}: {old_text} → {new_text}")
        if changes:
            entry = await self._audit(after, discord.AuditLogAction.guild_update)
            await self._log(
                after,
                "log_server",
                "🏰 تعديل إعدادات السيرفر",
                "تم تحديث إعدادات السيرفر.",
                author=getattr(entry, "user", None),
                event_type="guild_update",
                fields=[
                    ("📝 التغييرات", "\n".join(changes), False),
                    ("🧾 المنفذ", getattr(getattr(entry, "user", None), "mention", "غير معروف"), True),
                ],
            )

    @commands.Cog.listener("on_member_join")
    async def on_member_join_audit(self, member: discord.Member):
        attribution = await self._identify_invite(member.guild)
        await self._log(
            member.guild,
            "log_member",
            "🟢 انضمام عضو",
            "انضم عضو جديد إلى السيرفر.",
            thumbnail=_avatar(member),
            fields=[("👤 العضو", _member_mention(member), True)],
            event_type="member_join",
        )

        invite_event = "invite_used" if attribution["source"] in {"invite", "vanity"} else "invite_unknown"
        if await self._event_enabled(member.guild, "log_invites", invite_event):
            joined_at = getattr(member, "joined_at", None) or discord.utils.utcnow()
            source = attribution["source"]
            try:
                inserted = await record_invite_tracking_join(
                    member.guild.id,
                    member.id,
                    member.display_name,
                    source,
                    attribution.get("code"),
                    attribution.get("inviter_id"),
                    attribution.get("inviter_name"),
                    attribution.get("uses"),
                    joined_at.isoformat(),
                )
            except Exception:
                logger.exception("[INVITE_LOG] Failed to save join attribution guild=%s user=%s", member.guild.id, member.id)
                inserted = True
            if inserted:
                if source == "invite":
                    source_label = f"دعوة `{attribution['code']}`"
                    inviter = _member_mention(attribution["inviter_id"])
                elif source == "vanity":
                    source_label = f"رابط Vanity `{attribution['code']}`"
                    inviter = "لا يوجد منشئ فردي لرابط Vanity"
                else:
                    source_label = "غير معروف؛ لم توجد زيادة وحيدة موثوقة"
                    inviter = "غير معروف"
                await self._log(
                    member.guild, "log_invites", "🔗 تسجيل انضمام ودعوة",
                    "نُسب مصدر الانضمام فقط عند رصد زيادة وحيدة بمقدار استخدام واحد؛ خلاف ذلك بقي المصدر غير معروف.",
                    event_type=invite_event,
                    fields=[
                        ("👤 العضو", _member_mention(member), True),
                        ("🔗 المصدر", source_label, True),
                        ("🧑‍💼 منشئ الدعوة", inviter, True),
                        ("📊 الاستخدام بعد الانضمام", str(attribution.get("uses", "غير متاح")), True),
                        ("🕒 وقت الانضمام", joined_at.isoformat(), False),
                    ],
                    thumbnail=_avatar(member),
                )

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = getattr(invite, "guild", None)
        if guild is None:
            return
        async with self._invite_locks[guild.id]:
            cache = self._invite_cache.get(guild.id)
            if cache is None:
                try:
                    cache = await get_invite_tracking_cache(guild.id)
                except Exception:
                    cache = {}
            inviter = getattr(invite, "inviter", None)
            cache[str(invite.code)] = {
                "uses": max(0, int(getattr(invite, "uses", 0) or 0)),
                "inviter_id": getattr(inviter, "id", None),
                "inviter_name": getattr(inviter, "display_name", None) or getattr(inviter, "name", None),
                "is_vanity": False,
            }
            await self._persist_invites(guild.id, cache)
            self._invite_ready.add(guild.id)
        await self._log(
            guild, "log_invites", "🔗 إنشاء دعوة",
            "تم استلام حدث إنشاء دعوة من Discord.",
            event_type="invite_create",
            fields=[
                ("🔗 الرمز", f"`{invite.code}`", True),
                ("📍 القناة", _channel_mention(getattr(invite, "channel", None), guild), True),
                ("👤 المنشئ", getattr(invite.inviter, "mention", "غير معروف") if invite.inviter else "غير معروف", True),
                ("⏱️ العمر", f"{invite.max_age} ثانية" if invite.max_age else "غير محدود", True),
                ("🔢 الحد الأقصى", str(invite.max_uses or "غير محدود"), True),
            ],
        )

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        guild = getattr(invite, "guild", None)
        if guild is None:
            return
        async with self._invite_locks[guild.id]:
            cache = self._invite_cache.get(guild.id)
            if cache is None:
                try:
                    cache = await get_invite_tracking_cache(guild.id)
                except Exception:
                    cache = {}
            cached = cache.pop(str(invite.code), None) or {}
            await self._persist_invites(guild.id, cache)
        await self._log(
            guild, "log_invites", "🗑️ دعوة لم تعد متاحة",
            "استلم البوت حدث حذف الدعوة. لا يمكنه الجزم بسبب الحذف من الحدث وحده.",
            event_type="invite_delete",
            fields=[
                ("🔗 الرمز", f"`{invite.code}`", True),
                ("👤 المنشئ", getattr(invite.inviter, "mention", "غير معروف") if invite.inviter else "غير معروف", True),
                ("📊 آخر استخدامات معروفة", str(cached.get("uses", getattr(invite, "uses", "غير متاح"))), True),
                ("🧭 السبب", "غير محسوم من Discord Gateway", False),
            ],
        )

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add_audit(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == getattr(self.bot.user, "id", None):
            return
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id) if guild else None
        if guild is None:
            return
        await self._log(
            guild,
            "log_react",
            "👍 إضافة تفاعل",
            "أضاف عضو تفاعلاً إلى رسالة.",
            author=member,
            event_type="reaction_add",
            fields=[
                ("👤 العضو", _member_mention(member or payload.user_id), True),
                ("💬 الرسالة", f"[فتح الرسالة](https://discord.com/channels/{guild.id}/{payload.channel_id}/{payload.message_id})", True),
                ("📍 القناة", _channel_mention(payload.channel_id, guild), True),
                ("😀 التفاعل", str(payload.emoji), True),
            ],
            thumbnail=_avatar(member or self.bot.get_user(payload.user_id)),
        )

    @commands.Cog.listener("on_raw_reaction_remove")
    async def on_raw_reaction_remove_audit(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id or payload.user_id == getattr(self.bot.user, "id", None):
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        await self._log(
            guild, "log_react", "👎 إزالة تفاعل",
            "أزال عضو تفاعلاً من رسالة.",
            author=member, event_type="reaction_remove",
            fields=[
                ("👤 العضو", _member_mention(member or payload.user_id), True),
                ("💬 الرسالة", f"[فتح الرسالة](https://discord.com/channels/{guild.id}/{payload.channel_id}/{payload.message_id})", True),
                ("📍 القناة", _channel_mention(payload.channel_id, guild), True),
                ("😀 التفاعل", str(payload.emoji), True),
            ],
            thumbnail=_avatar(member or self.bot.get_user(payload.user_id)),
        )

    @commands.Cog.listener("on_raw_reaction_clear")
    async def on_raw_reaction_clear_audit(self, payload):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        await self._log(
            guild, "log_react", "🧹 مسح تفاعلات رسالة",
            "أرسل Discord حدث مسح جميع التفاعلات عن رسالة.",
            event_type="reaction_clear",
            fields=[
                ("💬 الرسالة", f"[فتح الرسالة](https://discord.com/channels/{guild.id}/{payload.channel_id}/{payload.message_id})", True),
                ("📍 القناة", _channel_mention(payload.channel_id, guild), True),
            ],
        )

    @commands.Cog.listener("on_raw_reaction_clear_emoji")
    async def on_raw_reaction_clear_emoji_audit(self, payload):
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        await self._log(
            guild, "log_react", "🧹 مسح تفاعل محدد",
            "أرسل Discord حدث إزالة جميع نسخ تفاعل محدد عن رسالة.",
            event_type="reaction_clear",
            fields=[
                ("💬 الرسالة", f"[فتح الرسالة](https://discord.com/channels/{guild.id}/{payload.channel_id}/{payload.message_id})", True),
                ("📍 القناة", _channel_mention(payload.channel_id, guild), True),
                ("😀 التفاعل", str(payload.emoji), True),
            ],
        )

    async def send_test(self, guild, category: str, actor=None):
        if category not in COLORS:
            raise ValueError("unsupported logging category")
        _, label = COLORS[category]
        sent = await self._log(
            guild, category, f"🧪 اختبار {label}", "هذه رسالة اختبار من موزع السجلات الاحترافي.",
            author=actor or guild.me,
            fields=[
                ("✅ الحالة", "القناة مرتبطة وتستقبل السجلات", True),
                ("🧭 التصنيف", category, True),
            ],
            strict=True,
            bypass_settings=True,
        )
        if not sent:
            raise PermissionError("log channel is not writable")


async def setup(bot: commands.Bot):
    await bot.add_cog(Analytics(bot))