import random
import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("TournamentCog")


def _player_label(guild: discord.Guild, player_id: int | None) -> str:
    if player_id is None:
        return "تأهل تلقائي (BYE)"
    return f"<@{int(player_id)}>"


def _bracket_embed(
    guild: discord.Guild,
    title: str,
    matches: list[dict],
    *,
    tournament_id: int,
) -> discord.Embed:
    lines = []
    current_round = None
    for match in matches:
        round_number = int(match["round_number"])
        if round_number != current_round:
            current_round = round_number
            lines.append(f"\n**الجولة {current_round}**")
        player_one = _player_label(guild, match["player_one_id"])
        player_two = _player_label(guild, match["player_two_id"])
        status = match.get("status", "scheduled")
        if status == "completed" and match.get("winner_id") is not None:
            status_text = f"✅ الفائز: <@{int(match['winner_id'])}>"
            if match.get("score"):
                status_text += f" · النتيجة {match['score']}"
        else:
            status_text = "⏳ بانتظار النتيجة"
        line = f"**#{match['match_number']}** {player_one} 🆚 {player_two} — {status_text}"
        lines.append(line)
    description = "\n".join(lines).strip() or "لا توجد مواجهات محفوظة."
    if len(description) > 3900:
        description = description[:3700] + "\n… بقية المواجهات محفوظة. استخدم /tournament bracket لعرض الجدول."
    embed = discord.Embed(
        title=f"🏆 شجرة بطولة: {discord.utils.escape_mentions(title)}",
        description=description,
        color=0xF1C40F,
    )
    embed.set_footer(text=f"PRIME TOURNAMENT #{tournament_id} • النتائج محفوظة")
    return embed

from database import (
    add_tournament_entry,
    cancel_tournament,
    create_tournament,
    get_open_tournaments,
    get_tournament_entries,
    get_tournament_bracket,
    get_tournament_scores,
    record_tournament_score,
    record_tournament_bracket_result,
    remove_tournament_entries,
    set_tournament_message,
    start_tournament_with_bracket,
)


class TournamentEntryView(discord.ui.View):
    def __init__(self, tournament_id: int, title: str, max_players: int):
        super().__init__(timeout=None)
        self.tournament_id = int(tournament_id)
        self.title, self.max_players = title, max_players
        join_button = discord.ui.Button(
            label="تسجيل اشتراك 🎯",
            style=discord.ButtonStyle.success,
            custom_id=f"tournament:join:{self.tournament_id}",
        )
        join_button.callback = self.join
        self.add_item(join_button)
        start_button = discord.ui.Button(
            label="إغلاق وقرعة المواجهات ⚔️",
            style=discord.ButtonStyle.danger,
            custom_id=f"tournament:start:{self.tournament_id}",
        )
        start_button.callback = self.generate_bracket
        self.add_item(start_button)

    async def join(self, itx: discord.Interaction):
        added, count, maximum = await add_tournament_entry(
            self.tournament_id,
            itx.user.id,
        )
        if not added and count >= maximum:
            return await itx.response.send_message(
                "⚠️ اكتمل العدد الأقصى للمشاركين!",
                ephemeral=True,
            )
        if not added:
            return await itx.response.send_message(
                "❌ أنت مسجل بالفعل في هذه البطولة أو أُغلقت.",
                ephemeral=True,
            )
        await itx.response.send_message(
            f"✅ تم تسجيلك بنجاح! ({count}/{maximum})",
            ephemeral=True,
        )

    async def generate_bracket(self, itx: discord.Interaction):
        if not itx.user.guild_permissions.manage_events:
            return await itx.response.send_message(
                "❌ هذا الإجراء متاح لمنظمي الفعاليات فقط.",
                ephemeral=True,
            )
        if itx.guild is None:
            return await itx.response.send_message("هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)

        registered = await get_tournament_entries(self.tournament_id)
        if len(registered) < 2:
            return await itx.response.send_message(
                "⚠️ يجب تسجيل لاعبين على الأقل لبدء القرعة!",
                ephemeral=True,
            )
        await itx.response.defer(ephemeral=True, thinking=True)

        players = []
        unavailable = []
        for player_id in registered:
            player = itx.guild.get_member(player_id)
            if player is None:
                try:
                    player = await itx.guild.fetch_member(player_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    unavailable.append(player_id)
                    continue
            players.append(player)

        if len(players) < 2:
            await remove_tournament_entries(self.tournament_id, unavailable)
            return await itx.followup.send(
                "تعذر بدء القرعة لعدم وجود لاعبين كافيين. تم تنظيف التسجيلات التي لم تعد متاحة؛ "
                "اطلب من لاعبين آخرين التسجيل ثم أعد المحاولة.",
                ephemeral=True,
            )

        random.shuffle(players)
        tournament = await start_tournament_with_bracket(
            self.tournament_id,
            registered,
            [player.id for player in players],
        )
        if tournament is None:
            return await itx.followup.send(
                "لم تبدأ البطولة لأن حالة التسجيل تغيّرت أثناء إنشاء القرعة. حدّث اللوحة ثم أعد المحاولة.",
                ephemeral=True,
            )
        self.stop()
        embed = _bracket_embed(
            itx.guild,
            tournament["title"],
            tournament["matches"],
            tournament_id=self.tournament_id,
        )
        try:
            await itx.channel.send(embed=embed)
            if itx.message is not None:
                await itx.message.edit(view=None)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Bracket saved but public announcement failed for tournament %s", self.tournament_id)
            return await itx.followup.send(
                f"تم حفظ القرعة في قاعدة البيانات، لكن تعذر نشر الإعلان. استخدم /tournament_bracket "
                f"برقم البطولة {self.tournament_id} لعرض المواجهات.",
                ephemeral=True,
            )
        await itx.followup.send(
            f"✅ بدأت البطولة {self.tournament_id} وحُفظت المواجهات. سجّل النتائج بالأمر /tournament result.",
            ephemeral=True,
        )


class Tournaments(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        for tournament in await get_open_tournaments():
            self.bot.add_view(
                TournamentEntryView(
                    tournament["id"],
                    tournament["title"],
                    tournament["max_players"],
                ),
                message_id=int(tournament["message_id"]),
            )

    @app_commands.command(
        name="scrim_split",
        description="توزيع لاعبي الروم الصوتي بالتساوي بين رومين",
    )
    @app_commands.checks.has_permissions(move_members=True)
    async def scrim_split(
        self,
        itx: discord.Interaction,
        target_voice: discord.VoiceChannel,
    ):
        if not itx.user.voice or not itx.user.voice.channel:
            return await itx.response.send_message(
                "❌ يجب أن تتواجد في روم صوتي أولاً!",
                ephemeral=True,
            )

        source = itx.user.voice.channel
        players = [member for member in source.members if not member.bot]
        if len(players) < 2:
            return await itx.response.send_message(
                "⚠️ لا يوجد عدد كافٍ لعمل قرعة.",
                ephemeral=True,
            )

        await itx.response.defer()
        random.shuffle(players)
        midpoint = len(players) // 2
        team_a, team_b = players[:midpoint], players[midpoint:]

        for member in team_b:
            try:
                await member.move_to(
                    target_voice,
                    reason="Scrims Balancing",
                )
            except discord.HTTPException:
                logger.warning("Could not move tournament participant %s", member.id, exc_info=True)

        embed = discord.Embed(
            title="⚔️ قرعة السكريمات المتوازنة",
            color=0xE74C3C,
        )
        embed.add_field(
            name=f"🔴 الفريق 1 ({source.name})",
            value="\n".join(member.mention for member in team_a) or "فارغ",
            inline=False,
        )
        embed.add_field(
            name=f"🔵 الفريق 2 ({target_voice.name})",
            value="\n".join(member.mention for member in team_b) or "فارغ",
            inline=False,
        )
        await itx.followup.send(embed=embed)

    @app_commands.command(
        name="scrim_teams",
        description="تقسيم لاعبي الروم إلى فرق محددة (سولو، ديو، سكواد)",
    )
    @app_commands.describe(
        size="حجم كل فريق (2 = ديو, 3 = تريو, 4 = سكواد)",
    )
    async def scrim_teams(
        self,
        itx: discord.Interaction,
        size: int,
    ):
        if size < 2:
            return await itx.response.send_message(
                "❌ حجم الفريق يجب أن يكون شخصين على الأقل.",
                ephemeral=True,
            )
        if not itx.user.voice or not itx.user.voice.channel:
            return await itx.response.send_message(
                "❌ ادخل روم صوتي أولاً!",
                ephemeral=True,
            )

        players = [
            member
            for member in itx.user.voice.channel.members
            if not member.bot
        ]
        if len(players) < size:
            return await itx.response.send_message(
                f"⚠️ عدد اللاعبين أقل من حجم الفريق ({size}).",
                ephemeral=True,
            )

        random.shuffle(players)
        teams = [
            players[index : index + size]
            for index in range(0, len(players), size)
        ]

        embed = discord.Embed(
            title=f"🎮 تشكيل الفرق التكتيكية (أحجام {size})",
            color=0x3498DB,
        )
        for index, squad in enumerate(teams, 1):
            embed.add_field(
                name=f"فريق {index} 🛡️",
                value="\n".join(member.mention for member in squad),
                inline=True,
            )
        await itx.response.send_message(embed=embed)

    @app_commands.command(
        name="map_randomizer",
        description="اختيار خريطة عشوائية لمباراة تكتيكية",
    )
    @app_commands.describe(
        maps_list=(
            "اكتب الخرائط مفصولة بفاصلة "
            "(مثال: الشوتر, الفيراري, الميناء)"
        ),
    )
    async def map_randomizer(
        self,
        itx: discord.Interaction,
        maps_list: str,
    ):
        maps = [item.strip() for item in maps_list.split(",") if item.strip()]
        if not maps:
            return await itx.response.send_message(
                "❌ أدخل قائمة خرائط صالحة.",
                ephemeral=True,
            )
        chosen = random.choice(maps)
        embed = discord.Embed(
            title="🗺️ قرعة الخرائط العشوائية",
            description=f"الخريطة المختارة للمواجهة:\n# **{chosen}**",
            color=0x2ECC71,
        )
        await itx.response.send_message(embed=embed)

    @app_commands.command(
        name="tournament_open",
        description="فتح باب التسجيل لبطولة رسمية وتوليد الجدول",
    )
    @app_commands.describe(
        title="اسم البطولة",
        max_players="الحد الأقصى للمشاركين",
    )
    @app_commands.checks.has_permissions(manage_events=True)
    async def tournament_open(
        self,
        itx: discord.Interaction,
        title: str,
        max_players: int = 16,
    ):
        if itx.guild is None or itx.channel is None:
            return await itx.response.send_message("افتح البطولة داخل سيرفر وقناة.", ephemeral=True)
        if not 2 <= max_players <= 100:
            return await itx.response.send_message(
                "❌ عدد المشاركين يجب أن يكون بين 2 و100.",
                ephemeral=True,
            )
        await itx.response.defer(ephemeral=True, thinking=True)
        tournament_id = await create_tournament(
            itx.guild.id,
            itx.channel.id,
            title,
            max_players,
            itx.user.id,
        )
        embed = discord.Embed(
            title=f"🏆 إعلان بطولة: {discord.utils.escape_mentions(title)}",
            description=(
                f"المقاعد المتاحة: {max_players} لاعب\n"
                "اضغط على الزر بالأسفل للاشتراك فوراً!"
            ),
            color=0xF1C40F,
        )
        embed.set_footer(
            text="يسجّل المنظّم القرعة ويحفظ الجولة والنتائج بعد إغلاق التسجيل",
        )
        view = TournamentEntryView(tournament_id, title, max_players)
        try:
            message = await itx.channel.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            await cancel_tournament(tournament_id)
            logger.exception("Could not publish tournament %s", tournament_id)
            return await itx.followup.send(
                "تعذر نشر لوحة البطولة؛ لم يبق تسجيل مفتوح غير ظاهر.",
                ephemeral=True,
            )
        await set_tournament_message(tournament_id, message.id)
        self.bot.add_view(view, message_id=message.id)
        await itx.followup.send(
            f"✅ تم فتح التسجيل للبطولة رقم {tournament_id}.",
            ephemeral=True,
        )

    @app_commands.command(
        name="match_record",
        description="توثيق نتيجة فريقين وتحديث ترتيب النقاط",
    )
    @app_commands.describe(
        winner_team="اسم الفريق الفائز",
        loser_team="اسم الفريق الخاسر",
        score="النتيجة الرقمية مثل 3-2",
        match_ref="معرّف فريد للمباراة لمنع تسجيلها مرتين",
    )
    @app_commands.checks.has_permissions(manage_events=True)
    async def match_record(
        self,
        itx: discord.Interaction,
        winner_team: str,
        loser_team: str,
        score: str,
        match_ref: app_commands.Range[str, 1, 64],
    ):
        if itx.guild is None or itx.channel is None:
            return await itx.response.send_message("هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)
        await itx.response.defer(ephemeral=True, thinking=True)
        try:
            recorded = await record_tournament_score(
                itx.guild.id,
                winner_team,
                loser_team,
                score=score,
                match_ref=match_ref,
                recorded_by=itx.user.id,
            )
        except ValueError as error:
            return await itx.followup.send(f"❌ {error}", ephemeral=True)
        if not recorded:
            return await itx.followup.send(
                "⚠️ هذا المعرّف سُجّل سابقًا؛ لم تُضف النقاط مرة أخرى.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="🏆 توثيق نتيجة مباراة رسمية",
            color=0x2ECC71,
        )
        embed.add_field(name="الفائز 🥇 (+3 نقاط)", value=f"**{discord.utils.escape_mentions(winner_team)}**", inline=True)
        embed.add_field(name="النتيجة 📊", value=f"{score}", inline=True)
        embed.add_field(name="الخاسر 🥈", value=f"**{discord.utils.escape_mentions(loser_team)}**", inline=True)
        embed.set_footer(text=f"المعرّف {match_ref} • وثّق النتيجة: {itx.user.display_name}")
        try:
            await itx.channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Result %s was saved but announcement failed", match_ref)
            return await itx.followup.send(
                "تم حفظ النتيجة وتحديث النقاط، لكن تعذر نشر الإعلان في القناة.",
                ephemeral=True,
            )
        await itx.followup.send("✅ تم حفظ النتيجة وتحديث النقاط.", ephemeral=True)

    @app_commands.command(
        name="standings",
        description="عرض ترتيب فرق البطولات المحفوظ",
    )
    async def standings(self, itx: discord.Interaction):
        rows = await get_tournament_scores(itx.guild.id)
        if not rows:
            return await itx.response.send_message(
                "لا توجد نتائج بطولات محفوظة بعد.",
                ephemeral=True,
            )
        lines = [
            f"**{index}.** {row['team_name']} — "
            f"`{row['points']}` نقطة · {row['wins']} فوز · {row['losses']} خسارة"
            for index, row in enumerate(rows, 1)
        ]
        embed = discord.Embed(
            title="🏆 ترتيب البطولات",
            description="\n".join(lines),
            color=0xF1C40F,
        )
        await itx.response.send_message(embed=embed)

    @app_commands.command(
        name="bracket",
        description="عرض شجرة المواجهات المحفوظة لبطولة",
    )
    @app_commands.describe(tournament_id="رقم البطولة")
    async def tournament_bracket(self, itx: discord.Interaction, tournament_id: int):
        if itx.guild is None:
            return await itx.response.send_message("هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)
        matches = await get_tournament_bracket(itx.guild.id, tournament_id)
        if not matches:
            return await itx.response.send_message(
                "لا توجد شجرة مواجهات محفوظة لهذه البطولة في هذا السيرفر.",
                ephemeral=True,
            )
        await itx.response.send_message(
            embed=_bracket_embed(itx.guild, f"بطولة {tournament_id}", matches, tournament_id=tournament_id),
            ephemeral=True,
        )

    @app_commands.command(
        name="result",
        description="توثيق نتيجة جولة وتحريك الفائز إلى الجولة التالية",
    )
    @app_commands.describe(
        tournament_id="رقم البطولة",
        round_number="رقم الجولة",
        match_number="رقم المباراة داخل الجولة",
        winner="اللاعب الفائز",
        score="النتيجة الرقمية مثل 3-2",
    )
    @app_commands.checks.has_permissions(manage_events=True)
    async def tournament_result(
        self,
        itx: discord.Interaction,
        tournament_id: int,
        round_number: app_commands.Range[int, 1, 16],
        match_number: app_commands.Range[int, 1, 64],
        winner: discord.Member,
        score: str,
    ):
        if itx.guild is None:
            return await itx.response.send_message("هذا الأمر متاح داخل السيرفر فقط.", ephemeral=True)
        await itx.response.defer(ephemeral=True, thinking=True)
        try:
            result = await record_tournament_bracket_result(
                itx.guild.id,
                tournament_id,
                round_number,
                match_number,
                winner.id,
                score,
                itx.user.id,
            )
        except ValueError as error:
            return await itx.followup.send(f"❌ {error}", ephemeral=True)
        if result is None:
            return await itx.followup.send(
                "تعذر تسجيل النتيجة. تحقق من رقم البطولة والجولة والمباراة وأن الفائز أحد اللاعبين "
                "وأن المباراة لم تُسجّل سابقًا.",
                ephemeral=True,
            )
        matches = await get_tournament_bracket(itx.guild.id, tournament_id)
        await itx.followup.send(
            f"✅ تم حفظ نتيجة المباراة {match_number} في الجولة {round_number}. "
            + ("🏆 اكتملت البطولة!" if result["tournament_completed"] else
               ("تم فتح الجولة التالية." if result["round_completed"] else "بانتظار بقية نتائج الجولة.")),
            ephemeral=True,
        )
        try:
            await itx.channel.send(
                embed=_bracket_embed(
                    itx.guild, f"بطولة {tournament_id}", matches,
                    tournament_id=tournament_id,
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Tournament result saved but bracket refresh announcement failed")



async def setup(bot: commands.Bot):
    await bot.add_cog(Tournaments(bot))
