"""Discord UI adapter for PRIME's shared, durable mini-game engine."""
from __future__ import annotations

import io
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
    expire_stale_sessions,
    GAME_SESSION_MAX_IDLE_HOURS,
    get_session,
    list_active_sessions,
)
from prime_games.registry import registry
from cogs.card_generator import generate_xo_game_card
from game_engine import get_circular_avatar
from prime_games.visual_renderers import render_memory_board, render_xo_board

logger = logging.getLogger("PrimeGamesCog")

_GAME_TITLES = {
    "xo": "🎮 PRIME · XO",
    "connect4": "🔴 PRIME · أربعة على التوالي",
    "rps": "🪨 PRIME · حجر ورقة مقص",
    "quiz": "🧠 PRIME · تحدّي المعلومات",
    "memory": "🧩 PRIME · تحدّي الذاكرة",
    "dice": "🎲 PRIME · مبارزة النرد",
    "word": "🔐 PRIME · فكّ الكلمة",
}
_GAME_COMMANDS = {
    "xo": ("/game xo @عضو", "ضع X وO في صف أو عمود أو قطر من ثلاثة رموز."),
    "connect4": ("/game connect4 @عضو", "أسقط الرموز في الأعمدة وحاول توصيل أربعة."),
    "rps": ("/game rps @عضو", "اختر حجرًا أو ورقة أو مقصًا؛ الاختيار الأول مخفي."),
    "quiz": ("/game quiz @عضو", "ستة أسئلة عربية، نقطة للإجابة الصحيحة."),
    "memory": ("/game memory @عضو", "اكشف البطاقات المتطابقة واجمع الأزواج قبل خصمك."),
    "dice": ("/game dice @عضو", "خمس جولات نرد؛ الأعلى نقاطًا يفوز."),
    "word": ("/game word @عضو", "فكّ ترتيب الحروف واختر الكلمة الصحيحة في ست جولات."),
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

    elif game_key == "memory":
        cards = list(state.get("cards") or [])
        matched = {int(value) for value in state.get("matched") or []}
        revealed = {int(value) for value in state.get("revealed") or []}
        board_lines = []
        for row in range(4):
            board_lines.append("  ".join(
                cards[index] if index in matched or index in revealed else "❔"
                for index in range(row * 4, row * 4 + 4)
            ))
        scores = state.get("scores") or {}
        description = (
            f"🧑 <@{players[0]}>: **{int(scores.get(str(players[0]), 0))}** زوج\n"
            f"🧑 <@{players[1]}>: **{int(scores.get(str(players[1]), 0))}** زوج\n\n"
            + "\n".join(board_lines)
            + f"\n\n🧩 الأزواج المكتشفة: **{len(matched) // 2}/8**"
        )
        if state.get("last_match") is True:
            description += "\n✅ تطابق رائع! حصلت على زوج."
        elif state.get("last_match") is False:
            description += "\n👀 البطاقتان مختلفتان؛ تذكّر مكانهما."
        if status == "active":
            description += f"\n🎯 الدور الآن على <@{session.get('current_player_id')}>."
        elif status == "finished" and winner_id is None:
            description += "\n\n🤝 انتهت اللعبة بالتعادل."

    elif game_key == "dice":
        scores = state.get("scores") or {}
        description = (
            f"🧑 <@{players[0]}>: **{int(scores.get(str(players[0]), 0))}** نقاط\n"
            f"🧑 <@{players[1]}>: **{int(scores.get(str(players[1]), 0))}** نقاط\n"
        )
        last_round = state.get("last_round") or {}
        if last_round:
            previous_rolls = last_round.get("rolls") or {}
            description += (
                f"\n**نتيجة الجولة {last_round.get('number', '?')}:** "
                f"🎲 {previous_rolls.get(str(players[0]), '—')} مقابل "
                f"{previous_rolls.get(str(players[1]), '—')}"
            )
            if last_round.get("draw"):
                description += " — تعادل الجولة"
            elif last_round.get("winner_id") is not None:
                description += f" — نقطة لـ <@{last_round['winner_id']}>"
        if status == "active":
            description += f"\n\n**الجولة {int(state.get('round') or 1)} من 5**"
            current_rolls = state.get("round_rolls") or {}
            if current_rolls:
                rolling_player = next(iter(current_rolls))
                description += (
                    f"\n🎲 رمية <@{rolling_player}>: **{current_rolls[rolling_player]}**"
                    "\nبانتظار رمية اللاعب الآخر."
                )
            description += f"\n🎯 الدور الآن على <@{session.get('current_player_id')}>."
        elif status == "finished" and winner_id is None:
            description += "\n\n🤝 انتهت الجولات بالتعادل."

    elif game_key == "word":
        puzzles = list(state.get("puzzles") or [])
        round_index = int(state.get("round_index") or 0)
        scores = state.get("scores") or {}
        description = (
            f"🧑 <@{players[0]}>: **{int(scores.get(str(players[0]), 0))}** نقطة\n"
            f"🧑 <@{players[1]}>: **{int(scores.get(str(players[1]), 0))}** نقطة\n"
        )
        last_answer = state.get("last_answer")
        if last_answer:
            description += "\n✅ إجابة صحيحة!" if last_answer.get("correct") else "\n❌ إجابة غير صحيحة."
        if status == "active" and round_index < len(puzzles):
            puzzle = puzzles[round_index]
            description += (
                f"\n\n**الجولة {round_index + 1} من {len(puzzles)}**"
                f"\n\n🔐 رتّب الحروف: **{puzzle.get('scrambled', '')}**"
                "\nاختر الكلمة الصحيحة من الأزرار أسفل الرسالة."
                f"\n\n🎯 الدور الآن على <@{session.get('current_player_id')}>."
            )
        elif status == "finished" and winner_id is None:
            description += "\n\n🤝 انتهى التحدي بالتعادل."

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
        if session.get("result") == "expired_idle":
            description += (
                f"\n\n⏰ انتهت صلاحية اللعبة بعد {GAME_SESSION_MAX_IDLE_HOURS} ساعة من عدم النشاط."
            )
        else:
            description += "\n\n🔒 تم إيقاف المباراة."
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
    """Build the text summary plus a Pillow board for the upgraded XO/memory games."""
    game_key = str(session.get("game_key") or "")
    if game_key not in {"xo", "memory"}:
        return _game_embed(session, reward=reward, reward_capped=reward_capped), None

    players = [int(value) for value in session.get("players") or []]
    state = session.get("state") or {}
    guild = interaction.guild
    members = [
        guild.get_member(player_id) if guild is not None else None
        for player_id in players[:2]
    ]
    names = [
        str(getattr(member, "display_name", f"Player {index + 1}"))
        for index, member in enumerate(members)
    ]
    while len(names) < 2:
        names.append(f"Player {len(names) + 1}")

    try:
        if game_key == "xo":
            avatars = []
            client_session = getattr(interaction.client, "session", None)
            for index, member in enumerate(members):
                if member is None:
                    from PIL import Image
                    avatars.append(Image.new("RGBA", (64, 64), (35, 33, 60, 255)))
                else:
                    avatars.append(await get_circular_avatar(
                        member,
                        64,
                        session=client_session,
                        border_color="#36e0d0" if index == 0 else "#ff5577",
                    ))
            image = render_xo_board(
                state,
                players,
                names,
                avatars,
                session.get("current_player_id"),
                session.get("winner_id"),
                str(session.get("status") or "active"),
            )
            filename = "prime-infinite-neon-xo.png"
        else:
            image = render_memory_board(
                state,
                players,
                names,
                session.get("current_player_id"),
                str(session.get("status") or "active"),
            )
            filename = "prime-visual-memory-royale.png"

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        buffer.seek(0)
        embed = _game_embed(session, reward=reward, reward_capped=reward_capped)
        embed.set_image(url=f"attachment://{filename}")
        return embed, discord.File(buffer, filename=filename)
    except Exception:
        logger.exception(
            "Could not render visual %s board for session %s; using embed fallback",
            game_key,
            session.get("id"),
        )
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

        elif self.game_key == "memory":
            cards = list(state.get("cards") or [])
            matched = {int(value) for value in state.get("matched") or []}
            revealed = {int(value) for value in state.get("revealed") or []}
            pending = state.get("pending_index")
            for index, symbol in enumerate(cards):
                is_face_up = index in matched or index in revealed
                button = discord.ui.Button(
                    label=symbol if is_face_up else "❔",
                    style=(
                        discord.ButtonStyle.success if index in matched
                        else discord.ButtonStyle.primary if is_face_up
                        else discord.ButtonStyle.secondary
                    ),
                    row=index // 4,
                    disabled=terminal or index in matched or index == pending,
                    custom_id=f"primegame:memory:{self.session_id}:{index}",
                )
                button.callback = self._move({"index": index})
                self.add_item(button)

        elif self.game_key == "dice":
            button = discord.ui.Button(
                label="🎲 ارم النرد",
                style=discord.ButtonStyle.primary,
                disabled=terminal,
                custom_id=f"primegame:dice:{self.session_id}:roll",
            )
            button.callback = self._move({"roll": "roll"})
            self.add_item(button)

        elif self.game_key == "word":
            puzzles = list(state.get("puzzles") or [])
            round_index = int(state.get("round_index") or 0)
            if puzzles:
                puzzle = puzzles[min(round_index, len(puzzles) - 1)]
                for index, option in enumerate(puzzle.get("options") or []):
                    button = discord.ui.Button(
                        label=f"{index + 1}. {str(option)[:70]}",
                        style=discord.ButtonStyle.secondary,
                        row=index // 2,
                        disabled=terminal,
                        custom_id=f"primegame:word:{self.session_id}:{index}",
                    )
                    button.callback = self._move({"answer": index})
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
            try:
                outcome = await apply_action(
                    self.session_id,
                    interaction.user.id,
                    action,
                )
            except Exception:
                logger.exception(
                    "Could not apply %s action for session %s",
                    self.game_key,
                    self.session_id,
                )
                await interaction.followup.send(
                    "واجهت مشكلة أثناء حفظ الحركة. لم أستطع تأكيدها؛ حدّث اللوحة وحاول مجددًا.",
                    ephemeral=True,
                )
                return
            if not outcome.get("ok"):
                if outcome.get("reason") == "game_expired" and interaction.message is not None:
                    try:
                        expired_state = await get_session(self.session_id)
                        if expired_state is not None:
                            await interaction.message.edit(
                                embed=_game_embed(expired_state),
                                attachments=[],
                                view=GameBoardView(expired_state),
                            )
                    except Exception:
                        logger.exception(
                            "Could not update expired %s game board for session %s",
                            self.game_key,
                            self.session_id,
                        )
                messages = {
                    "session_not_found": "لم أعد أجد هذه المباراة.",
                    "game_finished": "انتهت المباراة بالفعل.",
                    "game_expired": f"انتهت صلاحية اللعبة بعد {GAME_SESSION_MAX_IDLE_HOURS} ساعة من عدم النشاط. ابدأ مباراة جديدة.",
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
                    "invalid_card": "اختر بطاقة من اللوحة.",
                    "card_matched": "هذا الزوج مكتمل بالفعل؛ اختر بطاقة أخرى.",
                    "same_card": "اختر بطاقة ثانية مختلفة.",
                    "invalid_roll": "اضغط زر رمي النرد.",
                    "already_rolled": "سُجلت رميتك بالفعل في هذه الجولة.",
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
            reward = int(outcome.get("reward") or 0)
            reward_capped = bool(outcome.get("reward_capped"))
            embed, image_file = await _game_presentation(
                session,
                interaction,
                reward=reward,
                reward_capped=reward_capped,
            )
            view = GameBoardView(session)
            try:
                await interaction.edit_original_response(
                    embed=embed,
                    attachments=[image_file] if image_file is not None else [],
                    view=view,
                )
            except discord.HTTPException:
                # The move is already durable. Try editing the message with the
                # bot token and a text-only embed instead of leaving a stale board.
                logger.exception(
                    "Interaction response edit failed for %s session %s; trying message edit",
                    self.game_key,
                    self.session_id,
                )
                try:
                    if interaction.message is None:
                        raise RuntimeError("component interaction has no source message")
                    await interaction.message.edit(
                        embed=_game_embed(
                            session,
                            reward=reward,
                            reward_capped=reward_capped,
                        ),
                        attachments=[],
                        view=view,
                    )
                except (discord.HTTPException, RuntimeError):
                    logger.exception(
                        "Could not synchronize Discord board after saved %s move in session %s",
                        self.game_key,
                        self.session_id,
                    )
                    await interaction.followup.send(
                        "✅ حُفظت الحركة، لكن تعذّر تحديث اللوحة تلقائيًا. "
                        "حاول إعادة فتح الرسالة أو تواصل مع مشرف السيرفر.",
                        ephemeral=True,
                    )

        return callback


class Games(commands.Cog):
    game = app_commands.Group(name="game", description="ألعاب PRIME التفاعلية")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        await ensure_game_schema()

        # Expire abandoned games after 72 hours, but keep handlers registered for
        # old buttons even when a message cannot be edited or its channel is gone.
        expired_sessions = await expire_stale_sessions()
        for session in expired_sessions:
            expired_state = {**session, "status": "cancelled"}
            message_id = int(session.get("message_id") or 0)
            if message_id <= 0:
                continue
            self.bot.add_view(GameBoardView(expired_state), message_id=message_id)
            try:
                channel = self.bot.get_channel(int(session["channel_id"]))
                if channel is None:
                    channel = await self.bot.fetch_channel(int(session["channel_id"]))
                message = await channel.fetch_message(message_id)
                await message.edit(
                    embed=_game_embed(expired_state),
                    attachments=[],
                    view=GameBoardView(expired_state),
                )
            except (discord.HTTPException, AttributeError):
                logger.warning(
                    "Expired game session %s could not be visually closed; "
                    "its persistent handler remains registered",
                    session.get("id"),
                    exc_info=True,
                )

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

    @game.command(name="memory", description="تحدّ لاعبًا في لعبة الذاكرة")
    @app_commands.guild_only()
    async def memory(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "memory")

    @game.command(name="dice", description="تحدّ لاعبًا في مبارزة النرد")
    @app_commands.guild_only()
    async def dice(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "dice")

    @game.command(name="word", description="تحدّ لاعبًا في فكّ الكلمة")
    @app_commands.guild_only()
    async def word(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
        await self._start_duel(interaction, opponent, "word")

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
