import asyncio
import datetime
import io
import logging
import re
from collections import defaultdict
from types import SimpleNamespace
from typing import Any, Optional
from pathlib import Path

import discord
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from discord import app_commands
from discord.ext import commands

from database import (
    get_onboarding_delivery_logs,
    get_invite_tracking_cache,
    get_guild_settings,
    get_role_panels,
    get_rules_panels,
    record_invite_use,
    record_onboarding_delivery,
    replace_invite_tracking_cache,
    record_rules_agreement,
    save_role_panel,
    save_rules_panel,
    get_self_role_panels,
    save_self_role_panel,
    get_welcome_background_asset,
)


logger = logging.getLogger("EngagementCog")

class TicketControl(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="استلام التذكرة (Claim)",
        style=discord.ButtonStyle.secondary,
        emoji="📌",
        custom_id="btn_claim_t",
    )
    async def claim(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        if not itx.user.guild_permissions.manage_channels:
            return await itx.response.send_message(
                "❌ هذا الإجراء متاح للمشرفين فقط.",
                ephemeral=True,
            )
        btn.disabled = True
        btn.label = f"مستلمة بواسطة {itx.user.display_name}"
        await itx.message.edit(view=self)
        await itx.response.send_message(
            f"📌 تم استلام التذكرة من قبل المشرف: {itx.user.mention}"
        )

    @discord.ui.button(
        label="إغلاق التذكرة",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="btn_close_t",
    )
    async def close(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        await itx.response.send_message(
            "⚠️ سيتم إغلاق التذكرة وحذف القناة خلال 5 ثوانٍ..."
        )
        await asyncio.sleep(5)
        try:
            await itx.channel.delete()
        except Exception:
            logger.exception("Failed to delete closed ticket channel")


class TicketLauncher(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="فتح تذكرة دعم",
        style=discord.ButtonStyle.success,
        emoji="📩",
        custom_id="btn_open_t",
    )
    async def open(
        self,
        itx: discord.Interaction,
        btn: discord.ui.Button,
    ):
        guild = itx.guild
        category = discord.utils.get(guild.categories, name="Tickets")
        if category is None:
            category = await guild.create_category("Tickets")

        # منع العضو من إنشاء أكثر من تذكرة مفتوحة
        ticket_name = f"ticket-{itx.user.name.lower()}"
        if discord.utils.get(category.text_channels, name=ticket_name):
            return await itx.response.send_message(
                "❌ لديك تذكرة مفتوحة بالفعل داخل السيرفر!",
                ephemeral=True,
            )

        permissions = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            itx.user: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                attach_files=True,
            ),
            guild.me: discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True,
                manage_channels=True,
            ),
        }
        channel = await guild.create_text_channel(
            name=ticket_name,
            category=category,
            overwrites=permissions,
        )
        embed = discord.Embed(
            title=f"🎫 تذكرة الدعم | {itx.user.display_name}",
            description=(
                "أهلاً بك، تفضل بطرح مشكلتك بالتفصيل وسيقوم أحد المشرفين "
                "بمساعدتك قريباً."
            ),
            color=0x2ECC71,
        )
        embed.set_footer(
            text="يمكن للمشرفين استلام التذكرة أو إغلاقها عبر الأزرار أدناه"
        )
        await channel.send(
            f"{itx.user.mention} تم إنشاء تذكرتك بنجاح.",
            embed=embed,
            view=TicketControl(),
        )
        await itx.response.send_message(
            f"✅ تم فتح تذكرتك: {channel.mention}",
            ephemeral=True,
        )


class RoleSelector(discord.ui.Select):
    def __init__(self, roles, role_specs=None):
        specs = {
            int(spec["id"]): spec
            for spec in (role_specs or [])
            if isinstance(spec, dict) and str(spec.get("id", "")).isdigit()
        }
        options = [
            discord.SelectOption(
                label=str(specs.get(role.id, {}).get("label") or role.name)[:100],
                value=str(role.id),
                emoji=specs.get(role.id, {}).get("emoji") or "🏷️",
            )
            for role in roles[:25]
        ]
        super().__init__(
            placeholder="اختر رتبك واهتماماتك...",
            min_values=1,
            max_values=min(len(options), 5),
            options=options,
            custom_id="slct_multi_roles",
        )

    async def callback(self, itx: discord.Interaction):
        added, removed = [], []
        for value in self.values:
            role = itx.guild.get_role(int(value))
            if not role or role >= itx.guild.me.top_role:
                continue
            if role in itx.user.roles:
                await itx.user.remove_roles(role)
                removed.append(role.name)
            else:
                await itx.user.add_roles(role)
                added.append(role.name)

        result = []
        if added:
            result.append(f"➕ مُنحت: **{', '.join(added)}**")
        if removed:
            result.append(f"➖ أُزيلت: **{', '.join(removed)}**")
        await itx.response.send_message(
            "\n".join(result) if result else "لم يتم تغيير أي رتبة.",
            ephemeral=True,
        )


class RulesAgreementView(discord.ui.View):
    """Persistent one-click rules gate registered again after every restart."""

    def __init__(self, engagement=None):
        super().__init__(timeout=None)
        self.engagement = engagement

    @discord.ui.button(
        label="أوافق على القوانين",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id="btn_rules_agree_v1",
    )
    async def agree(self, itx: discord.Interaction, button: discord.ui.Button):
        engagement = self.engagement or itx.client.get_cog("Engagement")
        if engagement is None:
            return await itx.response.send_message(
                "⚠️ نظام التحقق غير متاح مؤقتاً.",
                ephemeral=True,
            )
        await itx.response.send_modal(RulesAgreementModal(engagement))


class RulesAgreementModal(discord.ui.Modal, title="تأكيد الموافقة على القوانين"):
    def __init__(self, engagement):
        super().__init__()
        self.engagement = engagement
        self.confirmation = discord.ui.TextInput(
            label="اكتب أوافق للتأكيد",
            placeholder="أوافق",
            min_length=3,
            max_length=20,
        )
        self.add_item(self.confirmation)

    async def on_submit(self, itx: discord.Interaction):
        if self.confirmation.value.strip().casefold() not in {
            "أوافق",
            "اوافق",
            "موافق",
            "agree",
        }:
            return await itx.response.send_message(
                "❌ اكتب «أوافق» لتأكيد قراءة القوانين.",
                ephemeral=True,
            )
        result = await self.engagement.agree_to_rules(itx)
        if result["ok"]:
            return await itx.response.send_message(
                f"✅ تم توثيق موافقتك في {result['agreed_at']} ومنحك رتبة التحقق.",
                ephemeral=True,
            )
        await itx.response.send_message(result["message"], ephemeral=True)


class Engagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.invite_cache: dict[int, dict[str, dict[str, Any]]] = {}
        self._invite_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._restored_role_panels: set[int] = set()
        self._restored_rules_panels: set[int] = set()
        self._restored_self_role_panels: set[int] = set()

    async def engagement_settings(self, guild_id: int) -> dict[str, Any]:
        try:
            snapshot = await get_guild_settings(int(guild_id))
            values = snapshot["settings"]
            return {
                "welcome_channel_id": values.get("welcome_channel_id"),
                "leave_channel_id": values.get("leave_channel_id"),
                "welcome_enabled": bool(values.get("welcome_enabled", True)),
                "leave_enabled": bool(values.get("leave_enabled", True)),
                "welcome_message": values.get("welcome_message", ""),
                "welcome_dm_message": values.get("welcome_dm_message", ""),
                "leave_message": values.get("leave_message", ""),
                "welcome_embed_enabled": bool(values.get("welcome_embed_enabled", False)),
                "welcome_embed_color": values.get("welcome_embed_color", "#7c3aed"),
                "welcome_embed_title": values.get("welcome_embed_title", "أهلاً بك في {server} ✨"),
                "welcome_embed_description": values.get("welcome_embed_description", ""),
                "welcome_embed_image_url": values.get("welcome_embed_image_url", ""),
                "welcome_embed_sticker_id": values.get("welcome_embed_sticker_id"),
                "welcome_embed_footer": values.get("welcome_embed_footer", "PRIME | TEAM • تطوير abood2026"),
                "welcome_embed_show_avatar": bool(values.get("welcome_embed_show_avatar", True)),
                "welcome_generated_image_enabled": bool(
                    values.get("welcome_generated_image_enabled", False)
                ),
                "welcome_canvas_config": (
                    values.get("welcome_canvas_config")
                    if isinstance(values.get("welcome_canvas_config"), dict)
                    else {}
                ),
                "leave_embed_enabled": bool(values.get("leave_embed_enabled", False)),
                "leave_embed_color": values.get("leave_embed_color", "#334155"),
                "leave_embed_title": values.get("leave_embed_title", "{username} غادر {server}"),
                "leave_embed_description": values.get("leave_embed_description", ""),
                "leave_embed_image_url": values.get("leave_embed_image_url", ""),
                "leave_embed_footer": values.get("leave_embed_footer", ""),
                "leave_embed_show_avatar": bool(values.get("leave_embed_show_avatar", True)),
                "welcome_dm_embed_enabled": bool(
                    values.get("welcome_dm_embed_enabled", False)
                ),
                "welcome_dm_embed_color": values.get("welcome_dm_embed_color", "#7c3aed"),
                "welcome_dm_embed_title": values.get(
                    "welcome_dm_embed_title", "أهلاً بك في {server} ✨"
                ),
                "welcome_dm_embed_description": values.get(
                    "welcome_dm_embed_description", ""
                ),
                "welcome_dm_embed_image_url": values.get(
                    "welcome_dm_embed_image_url", ""
                ),
                "welcome_dm_embed_footer": values.get("welcome_dm_embed_footer", ""),
                "welcome_dm_embed_show_avatar": bool(
                    values.get("welcome_dm_embed_show_avatar", True)
                ),
                "welcome_dm_enabled": bool(values.get("welcome_dm_enabled", False)),
                "auto_role_id": values.get("auto_role_id"),
                "member_auto_role_id": values.get("member_auto_role_id"),
                "bot_auto_role_id": values.get("bot_auto_role_id"),
                "verified_role_id": values.get("verified_role_id"),
                "unverified_role_id": values.get("unverified_role_id"),
                "rules_channel_id": values.get("rules_channel_id"),
            }
        except Exception:
            logger.exception("[ENGAGEMENT_CONFIG] تعذر قراءة إعدادات السيرفر %s", guild_id)
            return {
                "welcome_channel_id": None,
                "leave_channel_id": None,
                "welcome_enabled": True,
                "leave_enabled": True,
                "welcome_message": "",
                "welcome_dm_message": "",
                "leave_message": "",
                "welcome_embed_enabled": False,
                "welcome_embed_color": "#7c3aed",
                "welcome_embed_title": "أهلاً بك في {server} ✨",
                "welcome_embed_description": "",
                "welcome_embed_image_url": "",
                "welcome_embed_sticker_id": None,
                "welcome_embed_footer": "PRIME | TEAM • تطوير abood2026",
                "welcome_embed_show_avatar": True,
                "welcome_generated_image_enabled": False,
                "welcome_canvas_config": {},
                "leave_embed_enabled": False,
                "leave_embed_color": "#334155",
                "leave_embed_title": "{username} غادر {server}",
                "leave_embed_description": "",
                "leave_embed_image_url": "",
                "leave_embed_footer": "",
                "leave_embed_show_avatar": True,
                "welcome_dm_embed_enabled": False,
                "welcome_dm_embed_color": "#7c3aed",
                "welcome_dm_embed_title": "أهلاً بك في {server} ✨",
                "welcome_dm_embed_description": "",
                "welcome_dm_embed_image_url": "",
                "welcome_dm_embed_footer": "",
                "welcome_dm_embed_show_avatar": True,
                "welcome_dm_enabled": False,
                "auto_role_id": None,
                "member_auto_role_id": None,
                "bot_auto_role_id": None,
                "verified_role_id": None,
                "unverified_role_id": None,
                "rules_channel_id": None,
            }

    async def resolve_text_channel(self, guild: discord.Guild, channel_id: int):
        channel = guild.get_channel(int(channel_id))
        if isinstance(channel, discord.TextChannel) or callable(getattr(channel, "send", None)):
            return channel
        fetch_channels = getattr(guild, "fetch_channels", None)
        if fetch_channels is None:
            return None
        try:
            for fetched in await fetch_channels():
                if fetched.id == int(channel_id) and (
                    isinstance(fetched, discord.TextChannel)
                    or callable(getattr(fetched, "send", None))
                ):
                    return fetched
        except (discord.Forbidden, discord.HTTPException):
            logger.debug("[ENGAGEMENT_CONFIG] تعذر تحديث قنوات السيرفر %s", guild.id, exc_info=True)
        return None

    async def resolve_sticker(self, guild: discord.Guild, sticker_id: int):
        for sticker in getattr(guild, "stickers", ()) or ():
            if sticker.id == int(sticker_id):
                return sticker
        fetch_stickers = getattr(guild, "fetch_stickers", None)
        if fetch_stickers is not None:
            try:
                for sticker in await fetch_stickers():
                    if sticker.id == int(sticker_id):
                        return sticker
            except (discord.Forbidden, discord.HTTPException):
                logger.debug("[ENGAGEMENT_CONFIG] تعذر تحميل ملصق السيرفر %s", sticker_id, exc_info=True)
        return None

    async def get_onboarding_snapshot(self, guild_id: int) -> dict[str, Any]:
        snapshot = await get_guild_settings(int(guild_id))
        fields = await self.engagement_settings(guild_id)
        return {
            "revision": snapshot["revision"],
            "updated_at": snapshot["updated_at"],
            "settings": fields,
            "self_roles": await get_self_role_panels(guild_id),
            "delivery_logs": await get_onboarding_delivery_logs(guild_id, limit=30),
        }

    @staticmethod
    def format_ordinal(count: int) -> str:
        count = max(0, int(count))
        suffix = "th"
        if 10 <= count % 100 <= 20:
            suffix = "th"
        elif count % 10 == 1:
            suffix = "st"
        elif count % 10 == 2:
            suffix = "nd"
        elif count % 10 == 3:
            suffix = "rd"
        return f"{count:,}{suffix}"

    def render_template(
        self,
        template: str,
        *,
        member: Optional[discord.Member] = None,
        guild: Optional[discord.Guild] = None,
        inviter: Optional[str] = None,
        invite_code: Optional[str] = None,
        count: Optional[int] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> str:
        member_count = count if count is not None else getattr(guild, "member_count", 0)
        data = {
            "user": getattr(member, "mention", "@user"),
            "username": getattr(member, "display_name", "عضو جديد"),
            "server": getattr(guild, "name", "السيرفر"),
            "count": self.format_ordinal(member_count),
            "inviter": inviter or "",
            "invite_code": invite_code or "",
        }
        if template_data:
            data.update(
                {
                    str(key): str(value)
                    for key, value in template_data.items()
                    if key in data and value is not None
                }
            )
        rendered = str(template or "")
        for key, value in data.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        # Never expose unresolved tokens (including unknown/custom variables) in
        # a live message. Invite fields are empty unless the caller has a
        # uniquely observed, trusted invite attribution.
        return re.sub(r"\{[^{}]*\}", "", rendered)

    async def build_welcome_embed(
        self,
        guild: discord.Guild,
        member: Optional[discord.Member] = None,
        *,
        config: Optional[dict[str, Any]] = None,
        inviter: Optional[str] = "دعوة تجريبية",
        invite_code: Optional[str] = None,
        count: Optional[int] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> discord.Embed:
        return await self.build_onboarding_embed(
            guild,
            member,
            "welcome",
            config=config,
            inviter=inviter,
            invite_code=invite_code,
            count=count,
            template_data=template_data,
        )

    async def build_onboarding_embed(
        self,
        guild: discord.Guild,
        member: Optional[discord.Member],
        delivery_type: str,
        *,
        config: Optional[dict[str, Any]] = None,
        inviter: Optional[str] = None,
        invite_code: Optional[str] = None,
        count: Optional[int] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> discord.Embed:
        config = config or await self.engagement_settings(guild.id)
        member_count = count if count is not None else int(guild.member_count or 0)
        if delivery_type == "welcome":
            prefix = "welcome_embed"
            title_default = "أهلاً بك في {server} ✨"
            description_default = (
                config.get("welcome_message")
                or "يا هلا {user} في {server}! أنت العضو رقم {count}."
            )
        elif delivery_type == "leave":
            prefix = "leave_embed"
            title_default = "{username} غادر {server}"
            description_default = (
                config.get("leave_message")
                or "{username} غادر {server}. كان عدد الأعضاء {count}."
            )
        elif delivery_type == "dm":
            prefix = "welcome_dm_embed"
            title_default = "أهلاً بك في {server} ✨"
            description_default = (
                config.get("welcome_dm_message")
                or config.get("welcome_message")
                or "مرحباً {user} في {server}!"
            )
        else:
            raise ValueError("invalid onboarding delivery type")

        title = self.render_template(
            config.get(f"{prefix}_title") or title_default,
            member=member,
            guild=guild,
            inviter=inviter,
            invite_code=invite_code,
            count=member_count,
            template_data=template_data,
        )
        description = self.render_template(
            config.get(f"{prefix}_description") or description_default,
            member=member,
            guild=guild,
            inviter=inviter,
            invite_code=invite_code,
            count=member_count,
            template_data=template_data,
        )
        raw_color = str(config.get(f"{prefix}_color") or "#7c3aed").lstrip("#")
        try:
            color = discord.Colour(int(raw_color, 16))
        except (TypeError, ValueError):
            color = discord.Colour(0x7C3AED)
        embed = discord.Embed(
            title=title[:256],
            description=description[:4096],
            color=color,
        )
        avatar = str(getattr(getattr(member, "display_avatar", None), "url", "") or "")
        display_name = getattr(member, "display_name", None) or "عضو جديد"
        if avatar and (delivery_type == "welcome" or config.get(f"{prefix}_show_avatar", True)):
            embed.set_author(name=display_name, icon_url=avatar)
            if config.get(f"{prefix}_show_avatar", True):
                embed.set_thumbnail(url=avatar)
        if delivery_type == "welcome":
            embed.add_field(name="العضو رقم", value=f"#{member_count:,}", inline=True)
            embed.add_field(name="عدد الأعضاء", value=f"{member_count:,}", inline=True)
        image_url = str(config.get(f"{prefix}_image_url") or "").strip()
        sticker_id = config.get("welcome_embed_sticker_id") if delivery_type == "welcome" else None
        if not image_url and sticker_id:
            sticker = await self.resolve_sticker(guild, int(sticker_id))
            if sticker is not None:
                image_url = str(sticker.url)
        if image_url:
            embed.set_image(url=image_url)
        footer = str(config.get(f"{prefix}_footer") or "").strip()
        if footer:
            embed.set_footer(text=footer[:2048])
        return embed

    async def _cache_guild_invites(self, guild: discord.Guild) -> None:
        async with self._invite_locks[guild.id]:
            try:
                invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
                logger.warning("[INVITES] تعذر قراءة دعوات السيرفر %s", guild.id)
                return
            previous = self.invite_cache.get(guild.id)
            if previous is None:
                try:
                    previous = await get_invite_tracking_cache(guild.id)
                except Exception:
                    logger.warning("[INVITES] تعذر تحميل لقطة الدعوات المحفوظة %s", guild.id)
                    previous = {}
            snapshot = {
                invite.code: {
                    "uses": int(invite.uses or 0),
                    "inviter_id": getattr(getattr(invite, "inviter", None), "id", None),
                    "inviter_name": getattr(
                        getattr(invite, "inviter", None), "display_name", None,
                    ) or getattr(getattr(invite, "inviter", None), "name", None),
                    "is_vanity": False,
                }
                for invite in invites
                if getattr(invite, "code", None)
            }
            try:
                vanity = await guild.vanity_invite()
                if vanity and vanity.code:
                    snapshot[vanity.code] = {
                        "uses": int(vanity.uses or 0),
                        "inviter_id": None,
                        "inviter_name": None,
                        "is_vanity": True,
                    }
            except (discord.Forbidden, discord.HTTPException, AttributeError):
                snapshot.update({
                    code: invite for code, invite in (previous or {}).items()
                    if invite.get("is_vanity")
                })
            self.invite_cache[guild.id] = snapshot
            try:
                await replace_invite_tracking_cache(guild.id, snapshot)
            except Exception:
                logger.warning("[INVITES] تعذر حفظ لقطة الدعوات %s", guild.id, exc_info=True)

    async def _refresh_all_invites(self) -> None:
        for guild in list(self.bot.guilds):
            await self._cache_guild_invites(guild)

    async def _identify_invite_details(
        self, guild: discord.Guild
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Attribute only a uniquely observed single-use increase; otherwise unknown."""
        async with self._invite_locks[guild.id]:
            previous = self.invite_cache.get(guild.id, {})
            try:
                current_invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException, asyncio.TimeoutError):
                return None, None, None
            current = {
                invite.code: {
                    "uses": int(invite.uses or 0),
                    "inviter_id": getattr(getattr(invite, "inviter", None), "id", None),
                    "inviter_name": getattr(
                        getattr(invite, "inviter", None),
                        "display_name",
                        None,
                    )
                    or getattr(getattr(invite, "inviter", None), "name", None),
                    "is_vanity": False,
                }
                for invite in current_invites
                if getattr(invite, "code", None)
            }
            vanity_known = False
            try:
                vanity = await guild.vanity_invite()
                vanity_known = True
                if vanity and vanity.code:
                    current[vanity.code] = {
                        "uses": int(vanity.uses or 0),
                        "inviter_id": None,
                        "inviter_name": None,
                        "is_vanity": True,
                    }
            except (discord.Forbidden, discord.HTTPException, AttributeError):
                pass
            if not vanity_known:
                current.update({
                    code: invite for code, invite in previous.items()
                    if invite.get("is_vanity")
                })
            if guild.id not in self.invite_cache:
                self.invite_cache[guild.id] = current
                try:
                    await replace_invite_tracking_cache(guild.id, current)
                except Exception:
                    logger.warning("[INVITES] تعذر حفظ لقطة الدعوات %s", guild.id, exc_info=True)
                return None, None, None
            if set(current) != set(previous) or (
                not vanity_known and any(invite.get("is_vanity") for invite in previous.values())
            ):
                self.invite_cache[guild.id] = current
                try:
                    await replace_invite_tracking_cache(guild.id, current)
                except Exception:
                    logger.warning("[INVITES] تعذر حفظ لقطة الدعوات %s", guild.id, exc_info=True)
                return None, None, None
            changes = []
            for code, invite in current.items():
                old = previous.get(code)
                if old is None or bool(old.get("is_vanity")) != bool(invite.get("is_vanity")):
                    continue
                if invite.get("is_vanity") and not vanity_known:
                    continue
                delta = invite["uses"] - int(old.get("uses", 0))
                if delta:
                    changes.append((code, invite, delta))
            self.invite_cache[guild.id] = current
            try:
                await replace_invite_tracking_cache(guild.id, current)
            except Exception:
                logger.warning("[INVITES] تعذر حفظ لقطة الدعوات %s", guild.id, exc_info=True)
            if len(changes) != 1 or changes[0][2] != 1:
                return None, None, None
            code, matched, _ = changes[0]
            if matched.get("is_vanity"):
                return "دعوة مخصصة", "دعوة مخصصة", code
            if not matched.get("inviter_id"):
                return None, None, None
            inviter_id = int(matched["inviter_id"])
            inviter_name = matched.get("inviter_name") or str(inviter_id)
            return f"<@{inviter_id}>", inviter_name, code

    async def _identify_inviter(self, guild: discord.Guild) -> tuple[str, str]:
        """Legacy two-value wrapper retained for existing callers and tests."""
        inviter, inviter_name, _ = await self._identify_invite_details(guild)
        return inviter or "غير معروف", inviter_name or "غير معروف"

    async def _restore_persistent_views(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                for panel in await get_role_panels(guild.id):
                    if panel["message_id"] in self._restored_role_panels:
                        continue
                    channel = guild.get_channel(panel["channel_id"])
                    roles = [
                        guild.get_role(role_id)
                        for role_id in panel["role_ids"]
                    ]
                    roles = [
                        role
                        for role in roles
                        if role and not role.managed and guild.me and role < guild.me.top_role
                    ]
                    if channel and roles:
                        view = discord.ui.View(timeout=None)
                        view.add_item(RoleSelector(roles))
                        self.bot.add_view(view, message_id=panel["message_id"])
                        self._restored_role_panels.add(panel["message_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[ROLE_PANEL] تعذر استعادة لوحة في %s", guild.id)
            try:
                for panel in await get_rules_panels(guild.id):
                    if panel["message_id"] in self._restored_rules_panels:
                        continue
                    if guild.get_channel(panel["channel_id"]):
                        self.bot.add_view(
                            RulesAgreementView(self),
                            message_id=panel["message_id"],
                        )
                        self._restored_rules_panels.add(panel["message_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[RULES_PANEL] تعذر استعادة لوحة في %s", guild.id)
            try:
                for panel in await get_self_role_panels(guild.id):
                    if panel["message_id"] in self._restored_self_role_panels:
                        continue
                    channel = guild.get_channel(panel["channel_id"])
                    specs = panel.get("role_specs") or []
                    roles = [
                        guild.get_role(int(spec["id"]))
                        for spec in specs
                        if isinstance(spec, dict) and str(spec.get("id", "")).isdigit()
                    ]
                    roles = [
                        role
                        for role in roles
                        if role and not role.managed and guild.me and role < guild.me.top_role
                    ]
                    if channel and roles:
                        view = discord.ui.View(timeout=None)
                        view.add_item(RoleSelector(roles, specs))
                        self.bot.add_view(view, message_id=panel["message_id"])
                        self._restored_self_role_panels.add(panel["message_id"])
            except (discord.Forbidden, discord.HTTPException):
                logger.warning("[SELF_ROLE_PANEL] تعذر استعادة لوحة في %s", guild.id)
    async def agree_to_rules(self, itx: discord.Interaction) -> dict[str, Any]:
        guild, member = itx.guild, itx.user
        if guild is None or not isinstance(member, discord.Member):
            return {"ok": False, "message": "🔒 هذا التحقق متاح داخل السيرفر فقط."}
        config = await self.engagement_settings(guild.id)
        verified = guild.get_role(int(config["verified_role_id"])) if config["verified_role_id"] else None
        unverified = guild.get_role(int(config["unverified_role_id"])) if config["unverified_role_id"] else None
        if verified is None or guild.me is None or verified >= guild.me.top_role:
            return {"ok": False, "message": "⚠️ رتبة التحقق غير مضبوطة أو أعلى من رتبة البوت."}
        roles = [role for role in member.roles if not unverified or role.id != unverified.id]
        if verified not in roles:
            roles.append(verified)
        try:
            await member.edit(roles=roles, reason="Rules agreement gate")
            agreed_at = await record_rules_agreement(guild.id, member.id, verified.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[RULES] فشل تحديث رتب %s في %s", member.id, guild.id, exc_info=True)
            return {"ok": False, "message": "❌ تعذر تحديث رتبتك؛ تحقق من صلاحيات البوت."}
        return {"ok": True, "agreed_at": agreed_at}

    async def _assign_join_role(self, mem: discord.Member, config: dict[str, Any]) -> None:
        raw_id = (
            config["bot_auto_role_id"]
            if mem.bot
            else config["member_auto_role_id"] or config["auto_role_id"]
        )
        if not raw_id:
            return
        role = mem.guild.get_role(int(raw_id))
        if role is None or role.managed or not mem.guild.me or role >= mem.guild.me.top_role:
            return
        try:
            await mem.add_roles(role, reason="Automatic onboarding role")
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[ONBOARDING_ROLE] فشل إسناد الرتبة %s", raw_id, exc_info=True)

    def _welcome_channel(self, guild: discord.Guild, config: dict[str, Any]):
        if config["welcome_channel_id"]:
            channel = guild.get_channel(int(config["welcome_channel_id"]))
            if channel:
                return channel
        return guild.system_channel

    @staticmethod
    def _welcome_image_text(value: str) -> str:
        """Shape Arabic glyphs and resolve bidirectional runs before drawing with Pillow."""
        text = str(value or "")
        if not any(0x0600 <= ord(char) <= 0x06FF or 0x0750 <= ord(char) <= 0x077F for char in text):
            return text
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            return get_display(arabic_reshaper.reshape(text))
        except (ImportError, ValueError):
            # Keep rendering available if an optional shaping dependency is missing.
            return text

    @staticmethod
    def _welcome_font(size: int, family: str = "sans", weight: str = "normal", style: str = "normal"):
        """Load a predictable Unicode font with real bold/italic faces and safe fallbacks."""
        families = {
            "sans": "DejaVuSans",
            "serif": "DejaVuSerif",
            "mono": "DejaVuSansMono",
            "condensed": "DejaVuSansCondensed",
        }
        family = family if family in families else "sans"
        weight = "bold" if weight == "bold" else "normal"
        style = "italic" if style == "italic" else "normal"
        root = families[family]
        italic_suffix = "Italic" if family == "serif" else "Oblique"
        suffix = ("Bold" if weight == "bold" else "") + (italic_suffix if style == "italic" else "")
        requested = f"{root}-{suffix}.ttf" if suffix else f"{root}.ttf"
        fallback_names = [
            requested,
            f"{root}-Bold.ttf" if weight == "bold" else f"{root}.ttf",
            "DejaVuSans-BoldOblique.ttf" if weight == "bold" and style == "italic" else "",
            "DejaVuSans-Bold.ttf" if weight == "bold" else "",
            "DejaVuSans.ttf",
        ]
        roots = (
            Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/dejavu"),
            Path("/usr/share/fonts/truetype/noto"),
            Path("/usr/share/fonts/truetype/liberation2"),
        )
        # Prefer fonts with explicit Arabic glyph coverage before Latin-first families.
        # Some minimal hosting images lack these fonts; in that case keep the safe fallbacks below.
        arabic_suffix = "Bold" if weight == "bold" else "Regular"
        candidates = [
            Path(f"/usr/share/fonts/truetype/noto/NotoSansArabic-{arabic_suffix}.ttf"),
            Path(f"/usr/share/fonts/truetype/noto/NotoNaskhArabic-{arabic_suffix}.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if weight == "bold" else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for name in fallback_names:
            if not name:
                continue
            candidates.extend(root_dir / name for root_dir in roots)
        candidates.extend([
            Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf") if weight == "bold" else Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
        ])
        for candidate in candidates:
            try:
                return ImageFont.truetype(str(candidate), max(6, int(size)))
            except OSError:
                continue
        return ImageFont.load_default()

    @classmethod
    def _render_welcome_image(
        cls, avatar_data: bytes, member_name: str, guild_name: str, member_count: int,
        accent: str, canvas_config: Optional[dict[str, Any]] = None,
        guild_id: Optional[int] = None,
    ) -> io.BytesIO:
        layout = canvas_config if isinstance(canvas_config, dict) else {}
        canvas_format = str(layout.get("canvas_format") or "auto")
        format_sizes = {
            "banner": (1200, 420),
            "landscape": (1200, 675),
            "square": (1000, 1000),
            "portrait": (800, 1200),
        }
        background_data = layout.get("_background_image_data")
        if not isinstance(background_data, (bytes, bytearray)):
            background_data = None
        if layout.get("use_custom_background") and guild_id and not background_data:
            try:
                legacy_slot = max(1, min(4, int(layout.get("background_slot", 1))))
            except (TypeError, ValueError):
                legacy_slot = 1
            legacy_name = f"{int(guild_id)}.png" if legacy_slot == 1 else f"{int(guild_id)}-{legacy_slot}.png"
            legacy_path = Path(__file__).resolve().parent.parent / "data" / "welcome-backgrounds" / legacy_name
            try:
                background_data = legacy_path.read_bytes() if legacy_path.is_file() else None
            except OSError:
                background_data = None
        if canvas_format == "auto" and background_data:
            try:
                with Image.open(io.BytesIO(bytes(background_data))) as source_image:
                    source_width, source_height = source_image.size
                scale = min(1200 / max(1, source_width), 1200 / max(1, source_height))
                width = max(1, int(round(source_width * scale)))
                height = max(1, int(round(source_height * scale)))
            except (OSError, ValueError):
                width, height = format_sizes["banner"]
        else:
            width, height = format_sizes.get(canvas_format, format_sizes["banner"])
        def pct(key: str, default: float) -> float:
            try: return max(0.0, min(100.0, float(layout.get(key, default)))) / 100.0
            except (TypeError, ValueError): return default / 100.0
        def color(key: str, default: str) -> tuple[int, int, int]:
            value = str(layout.get(key) or default)
            try:
                if len(value) == 7 and value.startswith("#"): return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
            except ValueError: pass
            return tuple(int(default[i:i + 2], 16) for i in (1, 3, 5))
        rgb = color("accent", accent or "#7c3aed")
        start, end = color("background_start", "#040a16"), color("background_end", "#194876")
        gradient = Image.new("RGB", (width, 1)); pixels = gradient.load()
        for x in range(width):
            blend = x / max(1, width - 1)
            pixels[x, 0] = tuple(int(start[i] * (1 - blend) + end[i] * blend) for i in range(3))
        image = gradient.resize((width, height))
        if layout.get("use_custom_background") and guild_id:
            try:
                if background_data:
                    with Image.open(io.BytesIO(bytes(background_data))) as opened:
                        custom_rgba = ImageOps.exif_transpose(opened).convert("RGBA")
                    # Build the blurred cover over the theme color, then layer the full
                    # original artwork above it without cropping transparent PNG content.
                    opaque_base = Image.new("RGBA", custom_rgba.size, (*start, 255))
                    opaque_base.alpha_composite(custom_rgba)
                    backdrop = ImageOps.fit(
                        opaque_base.convert("RGB"), (width, height),
                        method=Image.Resampling.LANCZOS,
                    ).filter(ImageFilter.GaussianBlur(radius=22))
                    foreground = ImageOps.contain(custom_rgba, (width, height), method=Image.Resampling.LANCZOS)
                    x_offset = (width - foreground.width) // 2
                    y_offset = (height - foreground.height) // 2
                    backdrop.paste(foreground, (x_offset, y_offset), foreground.getchannel("A"))
                    image = backdrop
            except (OSError, ValueError):
                logger.warning("Could not load custom welcome background for guild %s", guild_id, exc_info=True)
        elements = layout.get("elements")
        if isinstance(elements, list):
            # Editable layers are rendered from the same persisted element model used by Welcome Studio.
            image = image.convert("RGBA")
            try:
                avatar_source = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
            except (OSError, ValueError):
                avatar_source = None

            def element_color(value: Any, fallback: str = "#7c3aed") -> tuple[int, int, int]:
                raw = str(value or fallback)
                try:
                    if len(raw) == 7 and raw.startswith("#"):
                        return tuple(int(raw[i:i + 2], 16) for i in (1, 3, 5))
                except ValueError:
                    pass
                return tuple(int(fallback[i:i + 2], 16) for i in (1, 3, 5))

            def render_element(element: dict[str, Any]) -> None:
                if not isinstance(element, dict) or not element.get("visible", True):
                    return
                element_type = str(element.get("type") or "text")
                if element_type not in {"avatar", "title", "name", "server", "count", "text", "shape", "line", "frame"}:
                    return
                try:
                    ex = max(0, min(100, float(element.get("x", 0)))) / 100
                    ey = max(0, min(100, float(element.get("y", 0)))) / 100
                    ew = max(1, min(100, float(element.get("width", 30)))) / 100
                    eh = max(1, min(100, float(element.get("height", 10)))) / 100
                    # Font sizes in the editor are relative to the 1200px reference width.
                    font_size = max(6, min(160, int(float(element.get("fontSize", 24)) * width / 1200)))
                    opacity = max(0, min(100, float(element.get("opacity", 100)))) / 100
                    rotation = max(-180, min(180, float(element.get("rotation", 0))))
                except (TypeError, ValueError):
                    return
                if opacity <= 0:
                    return
                x0, y0 = int(width * ex), int(height * ey)
                box_w, box_h = max(2, int(width * ew)), max(2, int(height * eh))
                box_w, box_h = min(box_w, width - x0), min(box_h, height - y0)
                if box_w <= 0 or box_h <= 0:
                    return
                base_color = element_color(element.get("color"), str(layout.get("accent") or accent or "#7c3aed"))
                alpha = int(255 * opacity)
                overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                layer_draw = ImageDraw.Draw(overlay, "RGBA")
                box = (x0, y0, x0 + box_w - 1, y0 + box_h - 1)
                if element_type == "frame":
                    border_width = max(1, int(min(box_w, box_h) * 0.012))
                    radius = max(4, int(min(box_w, box_h) * 0.08))
                    layer_draw.rounded_rectangle(box, radius=radius, outline=(*base_color, alpha), width=border_width)
                elif element_type == "shape":
                    radius = max(2, int(min(box_w, box_h) * 0.08))
                    layer_draw.rounded_rectangle(box, radius=radius, fill=(*base_color, alpha))
                elif element_type == "line":
                    stroke = max(1, min(box_h, int(max(2, height * 0.008))))
                    layer_draw.line((x0, y0 + box_h // 2, x0 + box_w - 1, y0 + box_h // 2), fill=(*base_color, alpha), width=stroke)
                elif element_type == "avatar":
                    if avatar_source is not None:
                        diameter = max(2, min(box_w, box_h))
                        avatar = ImageOps.fit(avatar_source, (diameter, diameter), method=Image.Resampling.LANCZOS)
                        mask = Image.new("L", (diameter, diameter), 0)
                        ImageDraw.Draw(mask).ellipse((0, 0, diameter - 1, diameter - 1), fill=int(255 * opacity))
                        ring = ImageDraw.Draw(overlay, "RGBA")
                        ring_width = max(2, int(diameter * 0.025))
                        cx, cy = x0 + box_w // 2, y0 + box_h // 2
                        ring.ellipse((cx - diameter // 2 - ring_width, cy - diameter // 2 - ring_width,
                                      cx + diameter // 2 + ring_width, cy + diameter // 2 + ring_width),
                                     fill=(*base_color, int(90 * opacity)), outline=(*base_color, alpha), width=ring_width)
                        overlay.paste(avatar, (cx - diameter // 2, cy - diameter // 2), mask)
                else:
                    template = str(element.get("text") or "")
                    dynamic_values = {
                        "{username}": str(member_name or "عضو جديد")[:32],
                        "{server}": str(guild_name or "السيرفر")[:48],
                        "{member_count}": str(max(0, int(member_count))),
                        "{count}": str(max(0, int(member_count))),
                    }
                    for token, dynamic in dynamic_values.items():
                        template = template.replace(token, dynamic)
                    if element_type == "title" and template in {"", "أهلاً بك في PRIME"}:
                        text_value = "أهلاً بك في PRIME"
                    elif element_type == "name" and template in {"", "اسم العضو"}:
                        text_value = str(member_name or "عضو جديد")[:32]
                    elif element_type == "server" and template in {"", "اسم السيرفر"}:
                        text_value = str(guild_name or "السيرفر")[:48]
                    elif element_type == "count" and template in {"", "العضو رقم 128"}:
                        text_value = f"العضو رقم {max(0, int(member_count)):,}"
                    else:
                        text_value = template
                    if text_value:
                        font_preset = element.get("fontPreset", "default")
                        presets = {
                            "default": None,
                            "modern_bold": ("sans", "bold", "normal", "uppercase"),
                            "formal": ("serif", "normal", "normal", "capitalize"),
                            "friendly": ("sans", "bold", "normal", "none"),
                            "tech": ("mono", "bold", "normal", "uppercase"),
                            "gaming": ("condensed", "bold", "normal", "uppercase"),
                            "minimal": ("sans", "normal", "normal", "none"),
                            "luxury": ("serif", "normal", "italic", "capitalize"),
                        }
                        preset_values = presets.get(font_preset)
                        if preset_values:
                            font_family, font_weight, font_style, transform = preset_values
                        else:
                            font_family = element.get("fontFamily", "sans")
                            font_weight = element.get("fontWeight", "normal")
                            font_style = element.get("fontStyle", "normal")
                            transform = element.get("textTransform", "none")
                        if transform == "uppercase":
                            text_value = text_value.upper()
                        elif transform == "lowercase":
                            text_value = text_value.lower()
                        elif transform == "capitalize":
                            text_value = text_value.title()
                        max_text_width = max(4, box_w - 8)
                        max_text_height = max(4, box_h - 4)

                        def wrap_text(source: str, font: Any) -> list[str]:
                            lines: list[str] = []
                            for paragraph in (source.splitlines() or [source]):
                                words = paragraph.split()
                                if not words:
                                    lines.append("")
                                    continue
                                line = ""
                                for word in words:
                                    candidate = f"{line} {word}".strip()
                                    shaped_candidate = cls._welcome_image_text(candidate)
                                    if line and font.getlength(shaped_candidate) > max_text_width:
                                        lines.append(line)
                                        line = word
                                    else:
                                        line = candidate
                                if line:
                                    lines.append(line)
                            return lines or [""]

                        font = cls._welcome_font(font_size, font_family, font_weight, font_style)
                        lines = wrap_text(text_value, font)
                        line_height = max(1, font.getbbox("Ag")[3] - font.getbbox("Ag")[1])
                        for candidate_size in range(font_size, 5, -1):
                            candidate_font = cls._welcome_font(candidate_size, font_family, font_weight, font_style)
                            candidate_lines = wrap_text(text_value, candidate_font)
                            candidate_height = max(1, int((candidate_font.getbbox("Ag")[3] - candidate_font.getbbox("Ag")[1]) * 1.18))
                            widest = max((candidate_font.getlength(cls._welcome_image_text(line)) for line in candidate_lines), default=0)
                            if widest <= max_text_width and candidate_height * len(candidate_lines) <= max_text_height:
                                font, lines, line_height = candidate_font, candidate_lines, candidate_height
                                break
                            font, lines = candidate_font, candidate_lines
                            line_height = candidate_height
                        align = element.get("align", "left")
                        if align == "right":
                            anchor, tx = "rm", x0 + box_w - 4
                        elif align == "center":
                            anchor, tx = "mm", x0 + box_w // 2
                        else:
                            anchor, tx = "lm", x0 + 4
                        line_height = max(1, int(line_height * 1.18))
                        total_height = line_height * len(lines)
                        text_y = y0 + max(0, (box_h - total_height) // 2) + line_height // 2
                        for line_index, line in enumerate(lines):
                            shaped = cls._welcome_image_text(line)
                            layer_draw.text(
                                (tx, text_y + line_index * line_height), shaped, font=font,
                                fill=(*base_color, alpha), anchor=anchor,
                            )
                if rotation:
                    overlay = overlay.rotate(-rotation, resample=Image.Resampling.BICUBIC,
                                             center=(x0 + box_w / 2, y0 + box_h / 2))
                image.alpha_composite(overlay)

            for element in elements[:24]:
                render_element(element)
        else:
            # Legacy saved layouts remain supported until the server owner opens and saves the new editor.
            draw = ImageDraw.Draw(image, "RGBA")
            draw.rounded_rectangle((22, 22, width - 22, height - 22), radius=34, outline=(*rgb, 100), width=2)
            draw.ellipse((920, -170, 1390, 300), fill=(*rgb, 35)); draw.ellipse((790, 210, 1170, 590), fill=(30, 190, 230, 26))
            avatar_size = 220
            avatar = ImageOps.fit(Image.open(io.BytesIO(avatar_data)).convert("RGB"), (avatar_size, avatar_size), method=Image.Resampling.LANCZOS)
            mask = Image.new("L", (avatar_size, avatar_size), 0); ImageDraw.Draw(mask).ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)
            cx, cy = int(width * pct("avatar_x", 14)), int(height * pct("avatar_y", 50))
            left, top = cx - avatar_size // 2, cy - avatar_size // 2
            draw.ellipse((left - 7, top - 7, left + avatar_size + 7, top + avatar_size + 7), fill=(*rgb, 75), outline=(*rgb, 220), width=4)
            image.paste(avatar, (left, top), mask); draw = ImageDraw.Draw(image, "RGBA")
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            try:
                title_font, name_font, detail_font = ImageFont.truetype(font_path, 27), ImageFont.truetype(font_path, 48), ImageFont.truetype(font_path, 23)
            except OSError: title_font = name_font = detail_font = ImageFont.load_default()
            text_x = int(width * pct("name_x", 94))
            draw.text((text_x, int(height * pct("title_y", 20))), cls._welcome_image_text("أهلاً بك في PRIME"), font=title_font, fill=(*rgb, 255), anchor="ra")
            draw.text((text_x, int(height * pct("name_y", 38))), cls._welcome_image_text(str(member_name or "عضو جديد")[:32]), font=name_font, fill=(248, 250, 252, 255), anchor="ra")
            if layout.get("show_server", True): draw.text((text_x, int(height * pct("server_y", 60))), cls._welcome_image_text(str(guild_name or "السيرفر")[:48]), font=detail_font, fill=(192, 207, 226, 255), anchor="ra")
            if layout.get("show_count", True): draw.text((text_x, int(height * pct("count_y", 75))), cls._welcome_image_text(f"العضو رقم {max(0, int(member_count)):,}"), font=detail_font, fill=(*rgb, 255), anchor="ra")
        buffer = io.BytesIO(); image.save(buffer, format="PNG", optimize=True); buffer.seek(0); return buffer

    async def _generate_welcome_image(
        self,
        member: Optional[discord.Member],
        guild: discord.Guild,
        config: dict[str, Any],
    ) -> Optional[discord.File]:
        avatar = getattr(member, "display_avatar", None)
        read_avatar = getattr(avatar, "read", None)
        if not callable(read_avatar):
            return None
        try:
            avatar_data = await asyncio.wait_for(read_avatar(), timeout=8)
            if not avatar_data or len(avatar_data) > 4 * 1024 * 1024:
                return None
            canvas_config = dict(config.get("welcome_canvas_config") if isinstance(config.get("welcome_canvas_config"), dict) else {})
            if canvas_config.get("use_custom_background"):
                try:
                    background_slot = max(1, min(4, int(canvas_config.get("background_slot", 1))))
                except (TypeError, ValueError):
                    background_slot = 1
                try:
                    canvas_config["_background_image_data"] = await get_welcome_background_asset(
                        guild.id, slot=background_slot
                    )
                except Exception:
                    logger.warning(
                        "[WELCOME_IMAGE] تعذر تحميل الخلفية المحفوظة للسيرفر %s في الخانة %s",
                        guild.id, background_slot, exc_info=True,
                    )
            buffer = await asyncio.to_thread(
                self._render_welcome_image,
                avatar_data,
                getattr(member, "display_name", None) or "عضو جديد",
                getattr(guild, "name", "السيرفر"),
                int(getattr(guild, "member_count", 0) or 0),
                str(config.get("welcome_embed_color") or "#7c3aed"),
                canvas_config,
                int(getattr(guild, "id", 0) or 0),
            )
            return discord.File(buffer, filename="prime-welcome.png")
        except (asyncio.TimeoutError, discord.HTTPException, OSError, ValueError):
            logger.info("[WELCOME_IMAGE] تعذر توليد صورة الترحيب في السيرفر %s", guild.id)
        except Exception:
            logger.warning("[WELCOME_IMAGE] تعذر توليد صورة الترحيب في السيرفر %s",
                           guild.id, exc_info=True)
        return None

    async def _record_onboarding_delivery(self, **kwargs: Any) -> None:
        try:
            await record_onboarding_delivery(**kwargs)
        except Exception:
            logger.error("[ONBOARDING_LOG] تعذر حفظ سجل الإرسال", exc_info=True)

    async def _send_onboarding_delivery(
        self,
        target,
        delivery_type: str,
        trigger_type: str,
        member,
        guild: discord.Guild,
        config: dict[str, Any],
        *,
        target_type: str,
        inviter: Optional[str] = None,
        invite_code: Optional[str] = None,
        template_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if delivery_type == "welcome":
            template = config.get("welcome_message") or (
                "مرحباً {user} في {server}! أنت العضو {count}. تمت دعوتك بواسطة {inviter}."
            )
            embed_enabled = bool(config.get("welcome_embed_enabled"))
        elif delivery_type == "leave":
            template = config.get("leave_message") or (
                "{username} غادر {server}. كان عدد الأعضاء {count}."
            )
            embed_enabled = bool(config.get("leave_embed_enabled"))
        else:
            template = (
                config.get("welcome_dm_message")
                or config.get("welcome_message")
                or "مرحباً {user} في {server}!"
            )
            embed_enabled = bool(config.get("welcome_dm_embed_enabled"))
        rendered = self.render_template(
            template,
            member=member,
            guild=guild,
            inviter=inviter,
            invite_code=invite_code,
            template_data=template_data,
        )
        embed = None
        file = None
        warning_reason = None
        if embed_enabled:
            embed = await self.build_onboarding_embed(
                guild,
                member,
                delivery_type,
                config=config,
                inviter=inviter,
                invite_code=invite_code,
                template_data=template_data,
            )
        if delivery_type == "welcome" and config.get("welcome_generated_image_enabled"):
            file = await self._generate_welcome_image(member, guild, config)
            if file is None:
                warning_reason = "avatar_image_unavailable"
            elif embed is not None:
                embed.set_image(url="attachment://prime-welcome.png")

        allowed_mentions = (
            discord.AllowedMentions(users=True, roles=False, everyone=False)
            if delivery_type == "welcome" and target_type == "channel"
            else discord.AllowedMentions.none()
        )
        kwargs = {"allowed_mentions": allowed_mentions}
        if embed is not None:
            kwargs["embed"] = embed
        if file is not None:
            kwargs["file"] = file
        target_id = getattr(target, "id", None)
        channel_id = target_id if target_type == "channel" else None
        member_id = getattr(member, "id", None)
        # A dashboard test is specifically checking the generated image. Do not
        # silently report success if the upload cannot be rendered.
        if (
            delivery_type == "welcome"
            and trigger_type == "dashboard_test"
            and config.get("welcome_generated_image_enabled")
            and file is None
        ):
            await self._record_onboarding_delivery(
                guild_id=guild.id,
                delivery_type=delivery_type,
                trigger_type=trigger_type,
                status="failed",
                target_type=target_type,
                target_id=target_id,
                channel_id=channel_id,
                member_id=member_id,
                reason=warning_reason or "welcome_image_generation_failed",
            )
            return {"ok": False, "error": "welcome_image_generation_failed"}
        message_content = None if embed is not None else rendered or None
        try:
            try:
                message = await target.send(content=message_content, **kwargs)
            except discord.Forbidden:
                reason = "forbidden"
            except asyncio.TimeoutError:
                reason = "timeout"
            except discord.HTTPException:
                reason = "discord_http_error"
            except Exception:
                reason = "delivery_error"
                logger.exception("[ONBOARDING_SEND] فشل الإرسال في السيرفر %s", guild.id)
            else:
                message_id = getattr(message, "id", None)
                await self._record_onboarding_delivery(
                    guild_id=guild.id,
                    delivery_type=delivery_type,
                    trigger_type=trigger_type,
                    status="sent",
                    target_type=target_type,
                    target_id=target_id,
                    channel_id=channel_id,
                    member_id=member_id,
                    message_id=message_id,
                    reason=warning_reason,
                )
                return {
                    "ok": True,
                    "guild_id": guild.id,
                    "channel_id": channel_id,
                    "target_id": target_id,
                    "message_id": message_id,
                }
        finally:
            if file is not None:
                upload_stream = file.fp
                try:
                    file.close()
                finally:
                    if not upload_stream.closed:
                        upload_stream.close()
        await self._record_onboarding_delivery(
            guild_id=guild.id,
            delivery_type=delivery_type,
            trigger_type=trigger_type,
            status="failed",
            target_type=target_type,
            target_id=target_id,
            channel_id=channel_id,
            member_id=member_id,
            reason=reason,
        )
        return {"ok": False, "error": reason}

    @commands.Cog.listener()
    async def on_ready(self):
        await self._refresh_all_invites()
        await self._restore_persistent_views()

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        inviter = getattr(invite, "inviter", None)
        if invite.guild is None:
            return
        async with self._invite_locks[invite.guild.id]:
            self.invite_cache.setdefault(invite.guild.id, {})[invite.code] = {
                "uses": int(invite.uses or 0),
                "inviter_id": getattr(inviter, "id", None),
                "inviter_name": getattr(inviter, "display_name", None) or getattr(inviter, "name", None),
                "is_vanity": False,
            }
            try:
                await replace_invite_tracking_cache(
                    invite.guild.id, self.invite_cache[invite.guild.id]
                )
            except Exception:
                logger.warning("[INVITES] تعذر حفظ دعوة جديدة %s", invite.guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild is None:
            return
        async with self._invite_locks[invite.guild.id]:
            self.invite_cache.get(invite.guild.id, {}).pop(invite.code, None)
            try:
                await replace_invite_tracking_cache(
                    invite.guild.id, self.invite_cache.get(invite.guild.id, {})
                )
            except Exception:
                logger.warning("[INVITES] تعذر حفظ حذف دعوة %s", invite.guild.id, exc_info=True)

    @commands.Cog.listener()
    async def on_member_join(self, mem: discord.Member):
        config = await self.engagement_settings(mem.guild.id)
        inviter, inviter_name, invite_code = await self._identify_invite_details(mem.guild)
        inviter_match = re.fullmatch(r"<@(\d+)>", inviter or "")
        if inviter_match:
            await record_invite_use(mem.guild.id, int(inviter_match.group(1)))
        await self._assign_join_role(mem, config)
        if config.get("welcome_enabled", True):
            channel = self._welcome_channel(mem.guild, config)
            if channel:
                await self._send_onboarding_delivery(
                    channel,
                    "welcome",
                    "member_join",
                    mem,
                    mem.guild,
                    config,
                    target_type="channel",
                    inviter=inviter,
                    invite_code=invite_code,
                )
        if config.get("welcome_dm_enabled"):
            await self._send_onboarding_delivery(
                mem,
                "dm",
                "member_join",
                mem,
                mem.guild,
                config,
                target_type="dm",
                inviter=inviter_name,
                invite_code=invite_code,
            )

    @commands.Cog.listener()
    async def on_member_remove(self, mem: discord.Member):
        config = await self.engagement_settings(mem.guild.id)
        if not config.get("leave_enabled", True):
            return
        channel_id = config.get("leave_channel_id") or config.get("welcome_channel_id")
        channel = (
            await self.resolve_text_channel(mem.guild, int(channel_id))
            if channel_id
            else mem.guild.system_channel
        )
        if channel is not None:
            await self._send_onboarding_delivery(
                channel,
                "leave",
                "member_leave",
                mem,
                mem.guild,
                config,
                target_type="channel",
            )

    async def send_test_onboarding(
        self,
        guild_id: int,
        delivery_type: str,
        actor_user_id: int,
        target_channel_id: Optional[int] = None,
    ) -> dict[str, Any]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return {"ok": False, "error": "guild_not_found"}
        if delivery_type not in {"welcome", "leave", "dm"}:
            return {"ok": False, "error": "invalid_delivery_type"}
        config = await self.engagement_settings(guild.id)
        if delivery_type == "dm":
            target = self.bot.get_user(int(actor_user_id))
            if target is None:
                try:
                    target = await self.bot.fetch_user(int(actor_user_id))
                except discord.NotFound:
                    return {"ok": False, "error": "user_not_found"}
                except (discord.Forbidden, discord.HTTPException):
                    return {"ok": False, "error": "user_unavailable"}
            member = guild.get_member(int(actor_user_id)) or target
            return await self._send_onboarding_delivery(
                target,
                "dm",
                "dashboard_test",
                member,
                guild,
                config,
                target_type="dm",
                inviter="دعوة تجريبية",
                template_data={"username": "عضو تجريبي"},
            )

        configured_channel = (
            config.get("leave_channel_id") or config.get("welcome_channel_id")
            if delivery_type == "leave"
            else config.get("welcome_channel_id")
        )
        channel_id = target_channel_id or configured_channel
        target = (
            await self.resolve_text_channel(guild, int(channel_id))
            if channel_id
            else guild.system_channel
        )
        if target is None:
            return {"ok": False, "error": "channel_not_found"}
        # A dashboard test should preview the operator's own avatar/name, not the bot's.
        member = guild.get_member(int(actor_user_id))
        if member is None:
            member = getattr(guild, "me", None)
        if member is None:
            member = SimpleNamespace(
                id=int(actor_user_id),
                guild=guild,
                mention=f"<@{int(actor_user_id)}>",
                display_name="عضو تجريبي",
            )
        return await self._send_onboarding_delivery(
            target,
            delivery_type,
            "dashboard_test",
            member,
            guild,
            config,
            target_type="channel",
            inviter="دعوة تجريبية",
            template_data={"username": "عضو تجريبي"},
        )

    async def send_test_welcome(
        self,
        guild_id: int,
        target_channel_id: int,
        template_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return {"ok": False, "error": "guild_not_found"}
        channel = await self.resolve_text_channel(guild, int(target_channel_id))
        if channel is None or not callable(getattr(channel, "send", None)):
            return {"ok": False, "error": "channel_not_found"}
        config = await self.engagement_settings(guild.id)
        member = getattr(guild, "me", None)
        if member is None:
            member = SimpleNamespace(
                id=0,
                guild=guild,
                mention="@عضو_تجريبي",
                display_name="عضو تجريبي",
            )
        return await self._send_onboarding_delivery(
            channel,
            "welcome",
            "dashboard_test",
            member,
            guild,
            config,
            target_type="channel",
            inviter="دعوة تجريبية",
            template_data=template_data,
        )

    async def deploy_self_role_panel(
        self,
        guild_id: int,
        target_channel_id: int,
        title: str,
        description: str,
        color: str,
        emoji: str,
        role_specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return {"ok": False, "error": "guild_not_found"}
        channel = await self.resolve_text_channel(guild, int(target_channel_id))
        if channel is None or not callable(getattr(channel, "send", None)):
            return {"ok": False, "error": "channel_not_found"}
        if not isinstance(role_specs, list) or not 1 <= len(role_specs) <= 25:
            return {"ok": False, "error": "roles_invalid"}
        clean_specs = []
        roles = []
        for spec in role_specs:
            if not isinstance(spec, dict) or not str(spec.get("id", "")).isdigit():
                return {"ok": False, "error": "roles_invalid"}
            role = guild.get_role(int(spec["id"]))
            if role is None or role.managed or not guild.me or role >= guild.me.top_role:
                return {"ok": False, "error": "role_not_assignable"}
            clean_specs.append({
                "id": role.id,
                "label": str(spec.get("label") or role.name)[:100],
                "emoji": str(spec.get("emoji") or "🏷️")[:32],
            })
            roles.append(role)
        normalized_color = str(color or "#5865f2").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", normalized_color):
            normalized_color = "#5865f2"
        try:
            embed = discord.Embed(
                title=f"{str(emoji or '🏷️')[:8]} {str(title or 'الرتب الذاتية')[:256]}",
                description=str(description or "اختر الرتب المناسبة لك:")[:4000],
                color=int(normalized_color[1:], 16),
            )
            view = discord.ui.View(timeout=None)
            view.add_item(RoleSelector(roles, clean_specs))
            message = await channel.send(embed=embed, view=view)
            panel = await save_self_role_panel(
                guild.id,
                channel.id,
                message.id,
                str(title or "الرتب الذاتية")[:256],
                str(description or "")[:4000],
                normalized_color,
                str(emoji or "🏷️")[:8],
                clean_specs,
            )
            self._restored_self_role_panels.add(message.id)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("[SELF_ROLE_PANEL] فشل نشر لوحة في %s", guild.id, exc_info=True)
            return {"ok": False, "error": "send_failed"}
        return {"ok": True, "panel": panel}

    @app_commands.command(
        name="setup_tickets",
        description="تثبيت لوحة تذاكر الدعم الفني",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_tickets(self, itx: discord.Interaction):
        community = itx.client.get_cog("Community")
        if community is None:
            return await itx.response.send_message(
                "⚠️ نظام التذاكر غير متاح حالياً.",
                ephemeral=True,
            )
        try:
            panel = await community.deploy_ticket_panel(itx.channel.id)
        except (ValueError, discord.Forbidden, discord.HTTPException):
            logger.exception("[TICKETS_SETUP] فشل نشر لوحة التذاكر")
            return await itx.response.send_message(
                "❌ تعذر نشر لوحة التذاكر. تحقق من صلاحيات البوت.",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"✅ تم تثبيت لوحة التذاكر وحفظها برقم الرسالة `{panel['message_id']}`.",
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_roles",
        description="لوحة اختيار الرتب التفاعلية",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_roles(self, itx: discord.Interaction):
        roles = [
            role
            for role in itx.guild.roles
            if (
                not role.is_default()
                and not role.managed
                and role < itx.guild.me.top_role
            )
        ]
        if not roles:
            return await itx.response.send_message(
                "❌ لا توجد رتب متاحة للإسناد تحت رتبة البوت.",
                ephemeral=True,
            )
        view = discord.ui.View(timeout=None)
        view.add_item(RoleSelector(roles))
        embed = discord.Embed(
            title="🎭 اختيار الرتب الذاتية",
            description=(
                "حدد الرتب المناسبة لك من القائمة لتفعيلها أو إزالتها تلقائياً:"
            ),
            color=0x9B59B6,
        )
        message = await itx.channel.send(
            embed=embed,
            view=view,
        )
        await save_role_panel(
            itx.guild.id,
            itx.channel.id,
            message.id,
            [role.id for role in roles],
        )
        await itx.response.send_message(
            "✅ تم إرسال لوحة الرتب.",
            ephemeral=True,
        )

    @app_commands.command(
        name="setup_rules",
        description="تثبيت بوابة الموافقة على القوانين",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_rules(self, itx: discord.Interaction):
        config = await self.engagement_settings(itx.guild.id)
        if not config["verified_role_id"]:
            return await itx.response.send_message(
                "⚠️ اضبط رتبة التحقق أولاً من لوحة التحكم.",
                ephemeral=True,
            )
        channel = (
            itx.guild.get_channel(int(config["rules_channel_id"]))
            if config["rules_channel_id"]
            else itx.channel
        )
        if channel is None:
            return await itx.response.send_message(
                "❌ لم يتم العثور على قناة القوانين.",
                ephemeral=True,
            )
        embed = discord.Embed(
            title="📜 بوابة القوانين والتحقق",
            description=(
                "اقرأ قوانين السيرفر بعناية، ثم اضغط على الزر أدناه "
                "لتأكيد موافقتك وتفعيل رتبة الدخول."
            ),
            color=0x2ECC71,
        )
        message = await channel.send(embed=embed, view=RulesAgreementView(self))
        await save_rules_panel(itx.guild.id, channel.id, message.id)
        await itx.response.send_message(
            "✅ تم تثبيت بوابة القوانين وحفظها للاستعادة بعد إعادة التشغيل.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    bot.add_view(TicketLauncher())
    bot.add_view(TicketControl())
    await bot.add_cog(Engagement(bot))
