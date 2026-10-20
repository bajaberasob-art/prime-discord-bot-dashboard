"""Persistent interactive information panels with ephemeral button replies."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import os
import uuid
from typing import Any

import discord
from discord.ext import commands

import interactive_info_store as store

log = logging.getLogger("InteractiveInfoPanel")
STYLE_MAP = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


def _make_embed(config: dict[str, Any]) -> discord.Embed:
    color = str(config.get("color") or "#6366F1").lstrip("#")
    try:
        rgb = int(color, 16)
    except ValueError:
        rgb = 0x6366F1
    embed = discord.Embed(
        title=str(config.get("title") or "")[:256] or None,
        description=str(config.get("description") or "")[:4096] or None,
        color=discord.Color(rgb),
    )
    banner = str(config.get("banner_url") or "").strip()
    if banner:
        embed.set_image(url=banner)
    embed.set_footer(text="PRIME • Interactive Info Panel")
    return embed


class InteractiveInfoPanelView(discord.ui.View):
    """Persistent, dynamically-built buttons; callbacks reload the saved config."""

    def __init__(self, bot: commands.Bot, guild_id: int, buttons: list[dict[str, Any]]):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = int(guild_id)
        for item in buttons[:25]:
            button_id = str(item.get("id") or "")
            if not re.fullmatch(r"[a-f0-9]{12}", button_id):
                continue
            style = STYLE_MAP.get(str(item.get("style") or "primary"), discord.ButtonStyle.primary)
            button = discord.ui.Button(
                label=str(item.get("label") or "معلومات")[:80],
                emoji=(str(item["emoji"])[:100] if item.get("emoji") else None),
                style=style,
                custom_id=f"prime:info:{self.guild_id}:{button_id}",
                row=min(len(self.children) // 5, 4),
            )
            button.callback = self._button_callback(button_id)
            self.add_item(button)

    def _button_callback(self, button_id: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.guild_id != self.guild_id:
                await interaction.response.send_message("هذه اللوحة غير متاحة في هذا السيرفر.", ephemeral=True)
                return
            config = await store.get_panel(self.guild_id)
            if not config or not config.get("enabled"):
                await interaction.response.send_message("هذه اللوحة متوقفة حاليًا.", ephemeral=True)
                return
            button = next((x for x in config.get("buttons", []) if x.get("id") == button_id), None)
            if button is None:
                await interaction.response.send_message("لم يعد هذا الخيار متاحًا.", ephemeral=True)
                return
            content = str(button.get("response") or "").strip()
            if not content:
                content = "لا يوجد محتوى مضاف لهذا الخيار حتى الآن."
            await interaction.response.send_message(
                content[:2000],
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            delay = max(5, min(840, int(config.get("auto_delete_seconds") or 300)))
            async def delete_reply() -> None:
                await asyncio.sleep(delay)
                try:
                    await interaction.delete_original_response()
                except (discord.NotFound, discord.HTTPException):
                    pass
            asyncio.create_task(delete_reply(), name=f"prime-info-ephemeral-{self.guild_id}-{button_id}")
        return callback


class InteractiveInfoPanel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._registered: set[tuple[int, int]] = set()

    async def cog_load(self) -> None:
        await store.ensure_schema()
        for config in await store.list_enabled_panels():
            self.register_view(config)

    def register_view(self, config: dict[str, Any], *, replace: bool = False) -> None:
        guild_id = int(config["guild_id"])
        message_id = int(config.get("message_id") or 0)
        key = (guild_id, message_id)
        if not message_id or (key in self._registered and not replace):
            return
        self.bot.add_view(
            InteractiveInfoPanelView(self.bot, guild_id, config.get("buttons") or []),
            message_id=message_id,
        )
        self._registered.add(key)

    async def publish(self, guild: discord.Guild, config: dict[str, Any]) -> dict[str, Any]:
        if not config.get("enabled"):
            raise ValueError("فعّل اللوحة قبل النشر.")
        channel_id = int(config.get("channel_id") or 0)
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("اختر قناة نصية موجودة في السيرفر.")
        view = InteractiveInfoPanelView(self.bot, guild.id, config.get("buttons") or [])
        if not view.children:
            raise ValueError("أضف زرًا واحدًا على الأقل قبل النشر.")
        embed = _make_embed(config)
        banner_file = None
        banner_path = str(config.get("banner_file") or "").strip()
        if banner_path:
            if not os.path.isfile(banner_path):
                raise ValueError("ملف البانر المحفوظ غير موجود؛ ارفع الصورة مرة أخرى.")
            banner_name = os.path.basename(str(config.get("banner_url") or "attachment://prime-banner.png").replace("attachment://", ""))
            banner_file = discord.File(banner_path, filename=banner_name)
            embed.set_image(url=f"attachment://{banner_name}")
        message = None
        previous_id = config.get("message_id")
        if previous_id:
            try:
                message = await channel.fetch_message(int(previous_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is not None:
            await message.edit(embed=embed, view=view, content=None, attachments=[banner_file] if banner_file else [])
        else:
            message = await channel.send(embed=embed, view=view, file=banner_file)
        config["message_id"] = str(message.id)
        config["channel_id"] = str(channel.id)
        pinned = False
        if config.get("pin_message"):
            try:
                await message.pin(reason="PRIME interactive information panel")
                pinned = True
            except discord.HTTPException:
                log.warning("Could not pin interactive info panel guild=%s channel=%s", guild.id, channel.id, exc_info=True)
                config["pinned"] = False
                config["last_published_at"] = discord.utils.utcnow().isoformat()
                await store.save_panel(guild.id, config)
                self.register_view(config, replace=True)
                raise ValueError("تم نشر الرسالة وتحديثها، لكن تعذّر تثبيتها. تحقق من صلاحية Manage Messages.")
        config["last_published_at"] = discord.utils.utcnow().isoformat()
        config["pinned"] = pinned
        await store.save_panel(guild.id, config)
        self.register_view(config, replace=True)
        return config


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InteractiveInfoPanel(bot))
