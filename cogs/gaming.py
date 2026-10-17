"""Persistent scrim lobbies and lightweight esports operations."""

import logging

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from database import (
    cancel_scrim_slot,
    close_scrim,
    create_scrim_config,
    get_active_scrims,
    reserve_scrim_slot,
    set_scrim_message,
    set_scrim_checkin_all,
)

logger = logging.getLogger("GamingCog")


def _admin(member: discord.Member) -> bool:
    permissions = getattr(member, "guild_permissions", None)
    return bool(permissions and (permissions.manage_events or permissions.manage_guild))


def _members(value: str, leader_id: int) -> list[int]:
    """Parse a roster strictly; malformed and duplicate Discord IDs are rejected."""
    result = [int(leader_id)]
    tokens = str(value or "").replace(",", " ").split()
    for token in tokens:
        if not token.isdigit() or int(token) <= 0:
            raise ValueError("اكتب معرفات Discord رقمية مفصولة بفواصل أو مسافات.")
        member_id = int(token)
        if member_id in result:
            raise ValueError("يوجد عضو مكرر في التشكيلة.")
        result.append(member_id)
    if len(result) > 16:
        raise ValueError("الحد الأقصى لحجم التشكيلة 16 لاعبًا.")
    return result


async def _scrim_embed(scrim: dict) -> discord.Embed:
    registrations = sorted(
        scrim.get("registrations", []),
        key=lambda item: int(item.get("slot_number", 0)),
    )
    occupied = int(scrim.get("occupied_slots", len(registrations)) or 0)
    capacity = int(scrim.get("max_slots", 0) or 0)
    lines = []
    for item in registrations:
        team = discord.utils.escape_mentions(
            discord.utils.escape_markdown(str(item.get("team_name", "فريق")))
        )
        check = " ✅" if item.get("checked_in") else ""
        line = (
            f"`#{int(item['slot_number']):02d}` — **{team}**"
            f" · <@{int(item['leader_id'])}>{check}"
        )
        if sum(len(value) + 1 for value in lines) + len(line) > 3300:
            break
        lines.append(line)
    hidden = max(0, len(registrations) - len(lines))
    if not lines:
        roster = "لا توجد فرق مسجلة حتى الآن."
    else:
        roster = "\n".join(lines)
    if hidden:
        roster += f"\n… وإخفاء {hidden} فريقًا إضافيًا لتجنب تجاوز حدود Discord."
    embed = discord.Embed(
        title=f"🎮 {discord.utils.escape_mentions(str(scrim['title']))}",
        description=(
            f"**اللعبة:** {discord.utils.escape_mentions(str(scrim['game_type']))}"
            f" · **حجم الفريق:** {int(scrim['team_size'])}\n"
            f"**المقاعد:** {occupied}/{capacity} · **الشاغر:** {max(0, capacity - occupied)}\n\n"
            + roster
        ),
        color=0x00E5FF,
    )
    embed.set_footer(text=f"SCRIM:{int(scrim['id'])} • الحجز خاص، والقائمة ظاهرة للجميع")
    return embed


class BookingModal(discord.ui.Modal, title="حجز مقعد سكريم"):
    team_name = discord.ui.TextInput(
        label="اسم الفريق",
        placeholder="مثال: PRIME Alpha",
        max_length=100,
    )
    members = discord.ui.TextInput(
        label="Discord IDs للأعضاء الآخرين (اختياري)",
        placeholder="123..., 456...",
        required=False,
        max_length=300,
    )

    def __init__(self, view: "ScrimBoardView"):
        super().__init__()
        self.board = view

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("الحجز متاح داخل السيرفر فقط.", ephemeral=True)

        try:
            team_member_ids = _members(str(self.members), interaction.user.id)
        except ValueError as error:
            return await interaction.followup.send(str(error), ephemeral=True)

        scrims = await get_active_scrims(guild.id)
        scrim = next(
            (item for item in scrims if int(item["id"]) == self.board.scrim_id),
            None,
        )
        if scrim is None:
            return await interaction.followup.send("❌ هذا السكريم مغلق أو لم يعد موجودًا.", ephemeral=True)
        expected_size = int(scrim["team_size"])
        if len(team_member_ids) != expected_size:
            return await interaction.followup.send(
                f"❌ حجم الفريق المطلوب {expected_size} لاعبًا؛ أنت أدرجت {len(team_member_ids)}.",
                ephemeral=True,
            )

        for member_id in team_member_ids:
            member = guild.get_member(member_id)
            if member is None:
                try:
                    member = await guild.fetch_member(member_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    return await interaction.followup.send(
                        f"❌ تعذر التحقق من العضو {member_id}. تأكد أنه عضو في هذا السيرفر.",
                        ephemeral=True,
                    )
            if member.guild.id != guild.id or member.bot:
                return await interaction.followup.send(
                    "❌ التشكيلة يجب أن تضم أعضاء حقيقيين من هذا السيرفر فقط.",
                    ephemeral=True,
                )

        registration = await reserve_scrim_slot(
            self.board.scrim_id,
            str(self.team_name).strip(),
            interaction.user.id,
            team_member_ids,
        )
        if registration is None:
            return await interaction.followup.send(
                "⚠️ لا يوجد مقعد متاح، أو أن أحد اللاعبين مسجل بالفعل في فريق آخر.",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ تم حجز المقعد #{registration['slot_number']} لفريق "
            f"**{discord.utils.escape_markdown(str(self.team_name))}**.",
            ephemeral=True,
        )
        await self.board.refresh_message(interaction)


class CancelModal(discord.ui.Modal, title="إلغاء حجز سكريم"):
    slot = discord.ui.TextInput(label="رقم المقعد", placeholder="1", max_length=3)

    def __init__(self, view: "ScrimBoardView"):
        super().__init__()
        self.board = view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            slot_number = int(str(self.slot))
        except ValueError:
            return await interaction.response.send_message("❌ رقم المقعد غير صالح.", ephemeral=True)
        scrims = await get_active_scrims(self.board.guild_id)
        scrim = next((item for item in scrims if int(item["id"]) == self.board.scrim_id), None)
        registration = next(
            (item for item in (scrim or {}).get("registrations", [])
             if int(item["slot_number"]) == slot_number),
            None,
        )
        if registration is None:
            return await interaction.response.send_message("❌ المقعد غير محجوز.", ephemeral=True)
        if int(registration["leader_id"]) != interaction.user.id and not _admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ لا يستطيع إلغاء الحجز إلا قائد الفريق أو المشرف.", ephemeral=True
            )
        removed = await cancel_scrim_slot(
            self.board.scrim_id,
            slot_number=slot_number,
            leader_id=None if _admin(interaction.user) else interaction.user.id,
        )
        if not removed:
            return await interaction.response.send_message("⚠️ تعذر إلغاء الحجز.", ephemeral=True)
        await interaction.response.send_message("✅ تم إلغاء الحجز.", ephemeral=True)
        await self.board.refresh_message(interaction)


class ScrimBoardView(discord.ui.View):
    def __init__(self, scrim_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.scrim_id = int(scrim_id)
        self.guild_id = int(guild_id)
        book = discord.ui.Button(
            label="حجز مقعد 🎮",
            style=discord.ButtonStyle.success,
            custom_id=f"scrim:book:{self.scrim_id}",
        )
        book.callback = self.book
        cancel = discord.ui.Button(
            label="إلغاء حجزي",
            style=discord.ButtonStyle.danger,
            custom_id=f"scrim:cancel:{self.scrim_id}",
        )
        cancel.callback = self.cancel
        checkin = discord.ui.Button(
            label="تأكيد الحضور ✅",
            style=discord.ButtonStyle.primary,
            custom_id=f"scrim:checkin:{self.scrim_id}",
        )
        checkin.callback = self.checkin
        self.add_item(book)
        self.add_item(cancel)
        self.add_item(checkin)

    async def book(self, interaction: discord.Interaction):
        await interaction.response.send_modal(BookingModal(self))

    async def cancel(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CancelModal(self))

    async def checkin(self, interaction: discord.Interaction):
        if not _admin(interaction.user):
            return await interaction.response.send_message(
                "⛔ تأكيد الحضور متاح لمنظمي الفعالية فقط.", ephemeral=True
            )
        changed = await set_scrim_checkin_all(self.scrim_id, checked_in=True)
        if not changed:
            return await interaction.response.send_message(
                "❌ السكريم مغلق أو لم يعد موجودًا.", ephemeral=True
            )
        await interaction.response.send_message(
            "✅ تم تأكيد حضور جميع الفرق المسجلة. لن يؤدي الضغط مرة أخرى إلى إلغاء التأكيد.",
            ephemeral=True,
        )
        await self.refresh_message(interaction)

    async def refresh_message(self, interaction: discord.Interaction):
        scrims = await get_active_scrims(self.guild_id)
        scrim = next((item for item in scrims if int(item["id"]) == self.scrim_id), None)
        if not scrim:
            return
        message = interaction.message
        if message is None and interaction.channel is not None:
            try:
                message = await interaction.channel.fetch_message(int(scrim["message_id"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                message = None
        if message is not None:
            await message.edit(embed=await _scrim_embed(scrim), view=self)


class Gaming(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for scrim in await get_active_scrims_for_restore():
            if int(scrim.get("message_id") or 0) <= 0:
                continue
            self.bot.add_view(
                ScrimBoardView(scrim["id"], scrim["guild_id"]),
                message_id=int(scrim["message_id"]),
            )

    async def deploy_scrim(
        self,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        title: str,
        game_type: str,
        team_size: int,
        max_slots: int,
    ) -> dict:
        scrim = await create_scrim_config(
            guild.id, channel.id, title, game_type, team_size, max_slots
        )
        scrim["registrations"] = []
        scrim["occupied_slots"] = 0
        view = ScrimBoardView(scrim["id"], guild.id)
        try:
            message = await channel.send(embed=await _scrim_embed(scrim), view=view)
            await set_scrim_message(scrim["id"], message.id)
            self.bot.add_view(view, message_id=message.id)
        except (discord.Forbidden, discord.HTTPException):
            await close_scrim(scrim["id"])
            raise
        scrim["message_id"] = message.id
        return scrim

    async def close_scrim_board(self, scrim_id: int, guild: discord.Guild) -> bool:
        scrims = await get_scrims(guild.id)
        scrim = next(
            (item for item in scrims if int(item["id"]) == int(scrim_id)),
            None,
        )
        if scrim is None:
            return False
        changed = await close_scrim(scrim_id)
        if not changed:
            return False

        # The database's saved channel/message IDs are authoritative. Never scan
        # every text channel and a moving window of recent messages to find it.
        channel_id = int(scrim.get("channel_id") or 0)
        message_id = int(scrim.get("message_id") or 0)
        channel = guild.get_channel(channel_id) if channel_id else None
        if channel is None and channel_id:
            try:
                channel = await guild.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                chann    @app_commands.command(name="scrim_open", description="فتح لوحة تسجيل سكريم للفرق")
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.describe(title="عنوان السكريم", game_type="اسم اللعبة", team_size="حجم الفريق", max_slots="عدد المقاعد")
    async def scrim_open(
        self,
        interaction: discord.Interaction,
        title: str,
        game_type: str,
        team_size: int = 5,
        max_slots: int = 8,
    ):
        if interaction.guild is None or interaction.channel is None:
            return await interaction.response.send_message("افتح السكريم داخل سيرفر وقناة.", ephemeral=True)
        if not 1 <= team_size <= 16 or not 1 <= max_slots <= 128:
            return await interaction.response.send_message(
                "❌ حجم الفريق بين 1 و16 وعدد المقاعد بين 1 و128.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            scrim = await self.deploy_scrim(
                interaction.guild, interaction.channel, title, game_type, team_size, max_slots
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("Could not publish scrim in guild %s", interaction.guild.id, exc_info=True)
            return await interaction.followup.send(
                "تعذر نشر لوحة السكريم. تحقق من صلاحيات البوت في القناة.",
                ephemeral=True,
            )
        await interaction.followup.send(
            f"✅ تم فتح لوحة السكريم رقم {scrim['id']}.", ephemeral=True
        )



async def get_active_scrims_for_restore():
    # Restore all guilds without changing the public guild-scoped API helper.
    from database import connect
    async with connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM scrim_configs WHERE is_active = 1 AND message_id > 0"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def setup(bot: commands.Bot):
    await bot.add_cog(Gaming(bot))