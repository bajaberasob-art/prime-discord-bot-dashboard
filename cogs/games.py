"""Discord UI adapter for PRIME's shared, durable mini-game engine."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from prime_games.engine import (
    apply_action,
    attach_message,
    cancel_session,
    create_session,
    ensure_game_schema,
    list_active_sessions,
)
from prime_games.registry import registry
from cogs.card_generator import generate_xo_game_card

logger = logging.getLogger("PrimeGamesCog")

_GAME_TITLES = {
    "xo": "🎮 PRIME · XO",
    "connect4": "🔴 PRIME · أربعة على التوالي",
    "rps": "🪨 PRIME · حجر ورقة مقص",
    "quiz": "🧠 PRIME · تحدّي المعلومات",
}
_GAME_COMMANDS = {
    "xo": ("/game xo @عضو", "ضع X وO في صف أو عمود أو قطر من ثلاثة رموز."),
    "connect4": ("/game connect4 @عضو", "أسقط الرموز في الأعمدة وحاول توصيل أربعة."),
    "rps": ("/game rps @عضو", "اختر حجرًا أو ورقة أو مقصًا؛ الاختيار الأول مخفي."),
    "quiz": ("/game quiz @عضو", "ستة أسئلة عربية، نقطة للإجابة الصحيحة."),
}
_RPS_LABELS = {
    "rock": "🪨 حجر",
    "paper": "📄 ورقة",
    "scissors": "✂️ مقص",
}


def _game_embed(session: dict, *, reward: int = 0, reward_capped: bool = False) -> discord.Embed:
    """Build a game-specific, readable message from the durable session state."""
    players = [int(value) for value in session.get("players") or []]
    state = session.get("state") or {}
    game_key = str(session.get("game_key") or "xo")
    status = session.get("status")
    winner_id = session.get("winner_id")
    description = ""
    color = 0x7C3AED

    if game_key == "xo":
        board = state.get("board") or [None] * 9
        description = f"❎ **X:** <@{players[0]}>\n⭕ **O:** <@{players[1]}>\n\n"
        description += "اضغط المربع الذي تريد اللعب فيه. لا يمكن تغيير حركة بعد تسجيلها."
        if status == "active":
            description += f"\n\n🎯 الدور الآن على <@{session.get('current_player_id')}>"
        elif status == "finished" and winner_id is None:
            description += "\n\n🤝 انتهت المباراة بالتعادل."
        elif status == "cancelled":
            description += "\n\n🔒 تم إيقاف المباراة."

    elif game_key == "connect4":
        board = list(state.get("board") or [None] * 42)
        symbols = {"R": "🔴", "Y": "🟡", None: "⚪"}
        board_lines = [
            " ".join(symbols.get(board[row * 7 + column], "⚪") for column in range(7))
            for row in range(6)
        ]
        description = (
            f"🔴 **الأحمر:** <@{players[0]}>\n"
            f"🟡 **الأصفر:** <@{players[1]}>\n\n"
            + "\n".join(board_lines)
            + "\n\n**الأعمدة:** 1  2  3  4  5  6  7"
        )
        if status == "active":
            description += f"\n🎯 الدور الآن على <@{session.get('current_player_id')}> — اختر عمودًا."
        elif status == "finished" and winner_id is None:
            description += "\n🤝 امتلأت اللوحة وانتهت المباراة بالتعادل."

    elif game_key == "rps":
        choices = state.get("choices") or {}
        description = f"⚔️ <@{players[0]}> ضد <@{players[1]}>\n\n"
        if status == "active":
            chosen = len(choices)
            description += (
                "✅ سُجّل اختيار اللاعب الأول بشكل مخفي. ينتظر PRIME اختيار الخصم."
                if chosen == 1 else
                "اختار كل لاعب حركته سرًا؛ لن تُكشف الخيارات قبل أن يختار الطرفان."
            )
            description += f"\n\n🎯 الدور الآن على <@{session.get('current_player_id')}>."
        else:
            first = _RPS_LABELS.get(str(choices.get(str(players[0]), "")), "—")
            second = _RPS_LABELS.get(str(choices.get(str(players[1]), "")), "—")
            description += f"🧑 <@{players[0]}>: **{first}**\n🧑 <@{players[1]}>: **{second}**"
            if winner_id is None:
                description += "\n\n🤝 تعادل! اخترتما الحركة نفسها."

    elif game_key == "quiz":
        questions = list(state.get("questions") or [])
        question_index = int(state.get("question_index") or 0)
        scores = state.get("scores") or {}
        first_score = int(scores.get(str(players[0]), 0))
        second_score = int(scores.get(str(players[1]), 0))
        description = (
            f"🧑 <@{players[0]}>: **{first_score}** نقطة\n"
            f"🧑 <@{players[1]}>: **{second_score}** نقطة\n"
        )
        last_answer = state.get("last_answer")
        if last_answer:
            description += (
                "\n✅ إجابة صحيحة!" if last_answer.get("correct")
                else " \n💡 إجابة غير صحيحة."
            )
        if status == "active" and question_index < len(questions):
            question = questions[question_index]
            options = question.get("options") or []
            description += (
                f"\n\n**السؤال {question_index + 1} من {len(questions)}**\n"
                f"{question.get('question', 'اختر إجابتك:')}\n\n"
                + "\n".join(
                    f"**{index + 1}.** {option}"
                    for index, option in enumerate(options)
                )
                + f"\n\n🎯 الدور الآن على <@{session.get('current_player_id')}>."
            )
        elif status == "finished" and winner_id is None:
            description += "\n\n🤝 انتهى التحدي بالتعادل."

    if status == "finished":
        if winner_id is not None:
            description += f"\n\n🏆 **الفائز: <@{winner_id}>**"
            if reward:
                description += f"\n💰 أضيفت مكافأة **{reward}** عملة إلى محفظته."
            elif reward_capped:
                description += (
                    "\n🛡️ وصلت إلى حد مكافآت الألعاب خلال آخر 24 ساعة؛ "
                    "لم تُصرف عملات إضافية."
                )
            color = 0x22C55E
        else:
            color = 0x64748B
    elif status == "cancelled":
        color = 0x64748B

    embed = discord.Embed(
        title=_GAME_TITLES.get(game_key, "🎮 PRIME · لعبة"),
        description=description,
        color=color,
    )
    reward_value = registry.get(game_key).reward_coins
    embed.set_footer(
        text=f"PRIME GAME #{session['id']} • مكافأة الفوز {reward_value} عملة قبل حد الـ24 ساعة"
    )
    return embed


async def _game_presentation(
    session: dict,
    interaction: discord.Interaction,
    *,
    reward: int = 0,
    reward_capped: bool = False,
) -> tuple[discord.Embed, discord.File | None]:
    """Render the XO card when possible; keep every other game lightweight."""
    if session.get("game_key") != "xo":
        return _game_embed(session, reward=reward, reward_capped=reward_capped), None

    guild = interaction.guild
    players = list(session.get("players") or [])
    names: list[str] = []
    for index, user_id in enumerate(players[:2]):
        member = guild.get_member(int(user_id)) if guild is not None else None
        names.append(str(member.display_name) if member is not None else f"Player {index + 1}")
    while len(names) < 2:
        names.append(f"Player {len(names) + 1}")

    winner_id = session.get("winner_id")
    winner_name = None
    if winner_id is not None:
        winner_name = names[players.index(int(winner_id))] if int(winner_id) in players else "Winner"
    current_id = session.get("current_player_id")
    current_name = names[players.index(int(current_id))] if current_id in players else "Waiting"

    state = session.get("state") or {}
    try:
        image = await generate_xo_game_card(
            names[0],
            names[1],
            list(state.get("board") or [None] * 9),
            current_name,
            game_id=int(session["id"]),
            winner_name=winner_name,
            is_draw=bool(session.get("status") == "finished" and session.get("result") == "draw"),
            reward=int(reward),
        )
        embed = _game_embed(session, reward=reward, reward_capped=reward_capped)
        embed.set_image(url="attachment://prime-xo.png")
        return embed, discord.File(image, filename="prime-xo.png")
    except Exception:
        logger.exception("Could not render XO card for session %s; using embed fallback", session.get("id"))
        return _game_embed(session, reward=reward, reward_capped=reward_capped), None


class GameBoardView(discord.ui.View):
    """Persistent action controls for every registered PRIME mini-game."""

    def __init__(self, session: dict):
        super().__init__(timeout=None)
        self.session_id = int(session["id"])
        self.game_key = str(session.get("game_key") or "xo")
        state = session.get("state") or {}
        terminal = session.get("status") != "active"

        if self.game_key == "xo":
            board = state.get("board") or [None] * 9
            for index, value in enumerate(board):
                label = str(value) if value else str(index + 1)
                style = (
                    discord.ButtonStyle.primary if value == "X"
                    else discord.ButtonStyle.danger if value == "O"
                    else discord.ButtonStyle.secondary
                )
                button = discord.ui.Button(
                    label=label,
                    style=style,
                    row=index // 3,
                    disabled=terminal or value is not None,
                    # Preserve the existing IDs so already-published XO games survive deploys.
                    custom_id=f"primegame:xo:{self.session_id}:{index}",
                )
                button.callback = self._move({"index": index})
                self.add_item(button)

        elif self.game_key == "connect4":
            board = list(state.get("board") or [None] * 42)
            for column in range(7):
                button = discord.ui.Button(
                    label=f"⬇️ العمود {column + 1}",
                    style=discord.ButtonStyle.primary,
                    row=0 if column < 5 else 1,
                    disabled=terminal or all(board[row * 7 + column] is not None for row in range(6)),
                    custom_id=f"primegame:connect4:{self.session_id}:{column}",
                )
                button.callback = self._move({"column": column})
                self.add_item(button)

        elif self.game_key == "rps":
            for choice, label in _RPS_LABELS.items():
                button = discord.ui.Button(
                    label=label,
                    style=discord.ButtonStyle.primary,
                    disabled=terminal,
                    custom_id=f"primegame:rps:{self.session_id}:{choice}",
                )
                button.callback = self._move({"choice": choice})
                self.add_item(button)

        elif self.game_key == "quiz":
            questions = list(state.get("questions") or [])
            question_index = int(state.get("question_index") or 0)
            if questions:
                question = questions[min(question_index, len(questions) - 1)]
                for index, option in enumerate(question.get("options") or []):
                    button = discord.ui.Button(
                        label=f"{index + 1}. {str(option)[:70]}",
                        style=discord.ButtonStyle.secondary,
                        row=index // 2,
                        disabled=terminal,
                        custom_id=f"primegame:quiz:{self.session_id}:{index}",
                    )
                    button.callback = self._move({"answer": index})
                    self.add_item(button)

    def _move(self, action: dict):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            outcome = await apply_action(
                self.session_id,
                interaction.user.id,
                action,
            )
            if not outcome.get("ok"):
                messages = {
                    "session_not_found": "لم أعد أجد هذه المباراة.",
                    "game_finished": "انتهت المباراة بالفعل.",
                    "not_a_player": "أنت لست أحد لاعبي هذه المباراة.",
                    "not_your_turn": "انتظر دور اللاعب الآخر.",
                    "square_taken": "هذا المربع محجوز؛ اختر مربعًا آخر.",
                    "invalid_square": "المربع المحدد غير صالح.",
                    "invalid_game_state": "تعذر التحقق من حالة المباراة.",
                    "invalid_column": "اختر عمودًا صالحًا.",
                    "column_full": "هذا العمود ممتلئ؛ اختر عمودًا آخر.",
                    "invalid_choice": "اختر حجرًا أو ورقة أو مقصًا من الأزرار.",
                    "already_chose": "تم تسجيل اختيارك بالفعل.",
                    "invalid_answer": "الإجابة المحددة غير صالحة.",
                }
                await interaction.followup.send(
                    messages.get(outcome.get("reason"), "تعذر تسجيل الحركة؛ حاول تحديث اللوحة."),
                    ephemeral=True,
                )
                return

            session = outcome.get("session")
            if not session:
                await interaction.followup.send(
                    "تم حفظ الحركة لكن تعذر تحميل اللوحة.", ephemeral=True
                )
                return
            embed, image_file = await _game_presentation(
                session,
                interaction,
                reward=int(outcome.get("reward") or 0),
                reward_capped=bool(outcome.get("reward_capped")),
            )
            await interaction.edit_original_response(
                embed=embed,
                attachments=[image_file] if image_file is not None else [],
                view=GameBoardView(session),
            )

        return callback


class Games(commands.Cog):
    game = app_commands.Group(name="game", description="ألعاب PRIME التفاعلية")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        await ensure_game_schema()
        for session in await list_active_sessions(limit=None):
            self.bot.add_view(
                GameBoardView(session),
                message_id=int(session["message_id"]),
            )

    async def _start_duel(
        self,
        interaction: discord.Interaction,
        opponent: discord.Member,
        game_key: str,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            return await interaction.response.send_message(
                "هذه اللعبة متاحة داخل السيرفرات فقط.", ephemeral=True
            )
        if opponent.id == interaction.user.id:
            return await interaction.response.send_message(
                "اختر عضوًا آخر لتلعب معه.", ephemeral=True
            )
        if opponent.bot:
            return await interaction.response.send_message(
                "اللعب ضد البوت غير متاح في هذه النسخة.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        session = None
        message = None
        game_name = registry.get(game_key).name
        try:
            session = await create_session(
                guild_id=interaction.guild.id,
                channel_id=interaction.channel.id,
                game_key=game_key,
                players=[interaction.user.id, opponent.id],
            )
            embed, image_file = await _game_presentation(session, interaction)
            send_options = {
                "content": (
                    f"⚔️ {interaction.user.mention} تحدّى {opponent.mention} "
                    f"في **{game_name}**!"
                ),
                "embed": embed,
                "view": GameBoardView(session),
                "allowed_mentions": discord.AllowedMentions(
                    users=[interaction.user, opponent]
                ),
            }
            if image_file is not None:
                send_options["file"] = image_file
            message = await interaction.channel.send(**send_options)
            if not await attach_message(int(session["id"]), int(message.id)):
                await message.edit(view=None)
                await cancel_session(int(session["id"]), guild_id=interaction.guild.id)
                raise RuntimeError("game message could not be attached to its session")
            restored = {**session, "message_id": int(message.id)}
            self.bot.add_view(GameBoardView(restored), message_id=int(message.id))
        except (discord.Forbidden, discord.HTTPException, RuntimeError, ValueError):
            logger.exception(
                "Could not create %s session in guild %s", game_key, interaction.guild.id
            )
            if session is not None:
                await cancel_session(int(session["id"]), guild_id=interaction.guild.id)
            if message is not None:
                try:
                    await message.edit(view=None)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            await interaction.followup.send(
                "تعذر نشر المباراة الآن. لم تُسجل مباراة قابلة للعب؛ حاول مرة أخرى.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"✅ نُشرت مباراة **{game_name}** رقم {session['id']} في القناة.",
            ephemeral=True,
        )

    @game.command(name="xo", description="تحدّ لاعبًا في لعبة XO التفاعلية")
    @app_commands.guild_only()
    async def xo(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "xo")

    @game.command(name="connect4", description="تحدّ لاعبًا في أربعة على التوالي")
    @app_commands.guild_only()
    async def connect4(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "connect4")

    @game.command(name="rps", description="تحدّ لاعبًا في حجر ورقة مقص")
    @app_commands.guild_only()
    async def rps(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "rps")

    @game.command(name="quiz", description="تحدّ لاعبًا في أسئلة المعلومات العامة")
    @app_commands.guild_only()
    async def quiz(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "quiz")

    @game.command(name="list", description="عرض ألعاب PRIME وأوامر تشغيلها")
    async def list_games(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🎮 مركز ألعاب PRIME",
            description=(
                "اختر لعبة وابدأ تحديًا مع عضو من السيرفر. "
                "كل لعبة تحفظ تقدمها، وتعود أزرارها بعد إعادة تشغيل البوت.\n"
                "🏅 مكافآت الفوز تخضع لحد يومي مشترك لكل لاعب."
            ),
            color=0x7C3AED,
        )
        for item in registry.all():
            command, description = _GAME_COMMANDS.get(
                item.key, (f"/game {item.key}", "لعبة تفاعلية")
            )
            embed.add_field(
                name=f"{item.name} · {item.reward_coins} عملة",
                value=f"{description}\n▶️ `{command}`",
                inline=False,
            )
        embed.set_footer(text="ابدأ بـ /game ثم اختر اللعبة المناسبة")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
