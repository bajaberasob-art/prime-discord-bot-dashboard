"""Profile command and the short-lived entry point to the customization store.

This cog is separate from rank_commands: /profile never changes XP and never
uses the standard rank-card renderer. The web store URL is configurable because
the web authorization and shop UI are delivered in later phases.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import discord
from discord import app_commands
from discord.ext import commands

import database
import profile_store_db
from interaction_runtime import send_interaction_message
from level_progression import text_progress, xp_required
from profile_card_generator import generate_profile_card

logger = logging.getLogger("PrimeProfile")
PROFILE_STORE_URL = (
    os.environ.get("PROFILE_STORE_URL", "https://prime-panel.discloud.app/profile-store")
    .strip()
)


def _session_url(raw_token: str) -> str:
    """Add the temporary token to the configured public shop URL."""
    base = PROFILE_STORE_URL
    parts = urlsplit(base)
    if parts.scheme not in {"https", "http"} or not parts.netloc:
        raise ValueError("PROFILE_STORE_URL must be an absolute HTTP(S) URL")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key != "token"]
    query.append(("token", raw_token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


class ProfileStoreLinkView(discord.ui.View):
    """A one-button ephemeral response that exposes no token to the public channel."""

    def __init__(self, url: str):
        super().__init__(timeout=profile_store_db.DEFAULT_SESSION_TTL_SECONDS)
        self.add_item(discord.ui.Button(
            label="افتح السوق 🎨",
            style=discord.ButtonStyle.link,
            url=url,
        ))


class ProfileCardView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int):
        super().__init__(timeout=15 * 60)
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)

    @discord.ui.button(
        label="تعديل بطاقة 🎨",
        style=discord.ButtonStyle.secondary,
        emoji="🎨",
        custom_id="prime:profile-card:edit",
    )
    async def edit_profile(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await send_interaction_message(
                interaction,
                "زر التخصيص متاح لصاحب بطاقة البروفايل فقط.",
                ephemeral=True,
            )
            return
        if not interaction.guild or int(interaction.guild.id) != self.guild_id:
            await send_interaction_message(
                interaction,
                "افتح بطاقة البروفايل من السيرفر نفسه ثم حاول مجدداً.",
                ephemeral=True,
            )
            return
        try:
            raw_token, _expires_at = await profile_store_db.create_profile_store_session(
                self.guild_id, self.owner_id,
            )
            url = _session_url(raw_token)
            await send_interaction_message(
                interaction,
                "رابطك الخاص، صالح لـ 15 دقيقة 🔒",
                view=ProfileStoreLinkView(url),
                ephemeral=True,
            )
        except profile_store_db.ProfileStoreDisabled:
            await send_interaction_message(
                interaction,
                "متجر تخصيص البطاقات متوقف مؤقتاً من الإدارة.",
                ephemeral=True,
            )
        except Exception:
            logger.exception(
                "Could not create profile store link guild=%s user=%s",
                self.guild_id, self.owner_id,
            )
            await send_interaction_message(
                interaction,
                "تعذر إنشاء الرابط الخاص الآن؛ حاول مجدداً لاحقاً.",
                ephemeral=True,
            )


class ProfileCommands(commands.Cog):
    """Independent vertical, horizontal, compact, square and split profile cards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _build_card(self, guild: discord.Guild, member: discord.Member):
        humans = [int(user.id) for user in guild.members if not getattr(user, "bot", False)]
        if member.id not in humans:
            humans.append(int(member.id))

        snapshot = await database.get_command_rank_snapshot(
            guild.id, member.id, humans, mode="text",
        )
        progress = text_progress(int(snapshot.get("text_xp") or 0))
        wallet = await profile_store_db.get_profile_wallet(guild.id, member.id)
        configuration = await profile_store_db.get_profile_card_configuration(
            guild.id, member.id,
        )
        profile = {
            "display_name": str(getattr(member, "display_name", None) or member.name),
            "username": str(getattr(member, "name", "member")),
            "level": int(snapshot.get("text_level") or progress["level"]),
            "xp": int(progress["progress_xp"]),
            "required_xp": max(1, int(progress["xp_required"])),
            "balance": int(wallet.get("balance") or 0),
            "messages": int(snapshot.get("total_messages") or 0),
            "rank": snapshot.get("rank"),
            "voice_seconds": int(snapshot.get("total_voice_seconds") or 0),
        }
        return await generate_profile_card(member, profile, configuration)

    async def _send_profile(
        self,
        *,
        guild: discord.Guild,
        target: discord.Member,
        requester_id: int,
        send_method,
    ) -> None:
        if getattr(target, "bot", False):
            raise ValueError("لا تتوفر بطاقة بروفايل لحسابات البوتات.")
        image = await self._build_card(guild, target)
        attachment = discord.File(image, filename=f"prime-profile-{target.id}.png")
        view = ProfileCardView(target.id, guild.id) if int(target.id) == int(requester_id) else None
        try:
            await send_method(file=attachment, view=view,
                              allowed_mentions=discord.AllowedMentions.none())
        finally:
            attachment.close()
            image.close()

    @app_commands.command(name="profile", description="عرض بطاقة البروفايل المستقلة عن بطاقة اللفل")
    @app_commands.guild_only()
    @app_commands.describe(member="العضو المطلوب، أو اتركه فارغاً لعرض بطاقتك")
    async def profile_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
    ):
        guild = interaction.guild
        if guild is None:
            await send_interaction_message(
                interaction, "أمر البروفايل متاح داخل السيرفر فقط.", ephemeral=True,
            )
            return
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            try:
                target = guild.get_member(int(target.id)) or await guild.fetch_member(int(target.id))
            except Exception:
                await send_interaction_message(
                    interaction, "تعذر العثور على العضو المطلوب داخل السيرفر.", ephemeral=True,
                )
                return
        try:
            await interaction.response.defer(thinking=True)
            await self._send_profile(
                guild=guild,
                target=target,
                requester_id=interaction.user.id,
                send_method=lambda **kwargs: send_interaction_message(interaction, **kwargs),
            )
        except ValueError as error:
            await send_interaction_message(interaction, str(error), ephemeral=True)
        except Exception:
            logger.exception("Profile command failed guild=%s", getattr(guild, "id", None))
            await send_interaction_message(
                interaction, "تعذر إنشاء بطاقة البروفايل الآن؛ حاول مجدداً لاحقاً.",
                ephemeral=True,
            )

    @commands.command(name="profile", aliases=["بروف", "P", "p"], ignore_extra=False)
    @commands.guild_only()
    async def profile_prefix(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ):
        target = member or ctx.author
        try:
            async with ctx.typing():
                await self._send_profile(
                    guild=ctx.guild,
                    target=target,
                    requester_id=ctx.author.id,
                    send_method=ctx.send,
                )
        except ValueError as error:
            await ctx.send(str(error), allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            logger.exception("Prefix profile command failed guild=%s", getattr(ctx.guild, "id", None))
            await ctx.send(
                "تعذر إنشاء بطاقة البروفايل الآن؛ حاول مجدداً لاحقاً.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        original = getattr(error, "original", error)
        if isinstance(original, commands.NoPrivateMessage):
            message = "أمر البروفايل متاح داخل السيرفرات فقط."
        elif isinstance(original, commands.MemberNotFound):
            message = "لم أجد هذا العضو؛ استخدم منشن أو اترك الحقل فارغاً."
        else:
            logger.exception("Profile prefix command error", exc_info=original)
            message = "تعذر تنفيذ أمر البروفايل؛ حاول مجدداً لاحقاً."
        try:
            await ctx.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Could not send profile command error")


async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCommands(bot))


async def publish_profile_command(bot, guild=None) -> bool:
    """Upsert only /profile without replacing unrelated Discord commands."""
    try:
        command = bot.tree.get_command("profile")
        if command is None:
            raise RuntimeError("local /profile command is missing")
        fields = ("name", "description", "type", "options",
                  "default_member_permissions", "dm_permission")
        payload = command.to_dict(bot.tree)
        remote = await bot.tree.fetch_commands(guild=guild)
        existing = {item.name: item for item in remote}
        previous = existing.get("profile")
        if previous and all(payload.get(key) == previous.to_dict().get(key) for key in fields):
            return True
        if guild is None:
            await bot.http.upsert_global_command(bot.application_id, payload)
        else:
            await bot.http.upsert_guild_command(bot.application_id, int(guild.id), payload)
        logger.info("PRIME /profile command registered")
        return True
    except Exception:
        logger.exception("Could not register /profile; unrelated commands were left untouched")
        return False
