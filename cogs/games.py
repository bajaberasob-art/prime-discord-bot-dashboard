"""Discord UI adapter for PRIME's shared, durable mini-game engine."""
from __future__ import annotations

import asyncio
import io
import logging

import discord
from PIL import Image, ImageDraw, ImageFont
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
from prime_games.classic_renderer import render_classic_game
from game_engine import image_to_file
from cogs.card_generator import generate_xo_game_card

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
    "dice": ("/game dice @عضو", "مواجهة نرد تكتيكية بثلاثة قلوب وقدرات تُستخدم مرة واحدة."),
    "word": ("/game word @عضو", "فكّ الشفرة عبر نافذة سرية مع تلميح اختياري في ست جولات."),
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
        last_round = state.get("last_round") or {}
        if status != "active" and not choices:
            choices = last_round.get("choices") or {}
        hp = state.get("hp") or {}
        description = (
            f"⚔️ <@{players[0]}> ضد <@{players[1]}>\n"
            f"❤️ <@{players[0]}>: **{int(hp.get(str(players[0]), 3))}/3**"
            f"  •  ❤️ <@{players[1]}>: **{int(hp.get(str(players[1]), 3))}/3**\n\n"
        )
        if status == "active":
            chosen = len(state.get("choices") or {})
            description += (
                "✅ سُجّل اختيار اللاعب الأول بشكل مخفي. ينتظر PRIME اختيار الخصم."
                if chosen == 1 else
                "لم يختر اللاعبان بعد؛ اختر حركتك سرًا."
            )
            if last_round:
                previous = last_round.get("choices") or {}
                previous_first = _RPS_LABELS.get(str(previous.get(str(players[0]), "")), "—")
                previous_second = _RPS_LABELS.get(str(previous.get(str(players[1]), "")), "—")
                description += f"\nآخر جولة: {previous_first} مقابل {previous_second}."
                if last_round.get("winner_id") is not None:
                    description += f" الضرر: {int(last_round.get('damage', 0))}."
            description += f"\n\n🎯 الدور الآن على <@{session.get('current_player_id')}>."
        else:
            first = _RPS_LABELS.get(str(choices.get(str(players[0]), "")), "—")
            second = _RPS_LABELS.get(str(choices.get(str(players[1]), "")), "—")
            description += f"🧑 <@{players[0]}>: **{first}**\n🧑 <@{players[1]}>: **{second}**"

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
        pairs = state.get("pairs") or {}
        combo = state.get("combo") or {}
        combo_player = str(session.get("current_player_id") or "")
        first_score = int(scores.get(str(players[0]), 0))
        second_score = int(scores.get(str(players[1]), 0))
        first_pairs = int(pairs.get(str(players[0]), first_score))
        second_pairs = int(pairs.get(str(players[1]), second_score))
        description = (
            f"🧑 <@{players[0]}>: **{first_score} نقطة** ({first_pairs} أزواج)\n"
            f"🧑 <@{players[1]}>: **{second_score} نقطة** ({second_pairs} أزواج)\n\n"
            + "\n".join(board_lines)
            + f"\n\n🧩 الأزواج المكتشفة: **{len(matched) // 2}/8**"
        )
        if status == "active":
            description += f"\n🔥 مضاعف الكومبو الحالي: **x{min(int(combo.get(combo_player, 0)), 3)}**"
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
            description += f"\n\n**الجولة {int(state.get('round') or 1)} • مواجهة بثلاثة قلوب**"
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
    """Render every legacy game as a premium Pillow card."""
    try:
        image = await render_classic_game(
            session,
            interaction.guild,
            avatar_session=getattr(interaction.client, "session", None),
            avatar_timeout=2.0,
        )
        embed = _game_embed(session, reward=reward, reward_capped=reward_capped)
        embed.set_image(url="attachment://prime-classic-game.png")
        return embed, image_to_file(image, "prime-classic-game.png")
    except Exception:
        logger.exception(
            "Could not render %s card for session %s; using embed fallback",
            session.get("game_key"), session.get("id")
        )
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


class WordAnswerModal(discord.ui.Modal, title="🔐 إدخال شفرة الخزنة"):
    answer = discord.ui.TextInput(
        label="اكتب الكلمة الصحيحة",
        placeholder="أدخل الكلمة هنا — لن تظهر في الشات العام",
        min_length=1,
        max_length=40,
        required=True,
    )

    def __init__(self, session_id: int):
        super().__init__(timeout=120)
        self.session_id = int(session_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            outcome = await apply_action(
                self.session_id, interaction.user.id,
                {"answer_text": str(self.answer.value).strip()},
            )
        except Exception:
            logger.exception("Word modal submission failed for session %s", self.session_id)
            await interaction.followup.send("تعذر حفظ الإجابة؛ حاول مرة أخرى.", ephemeral=True)
            return
        if not outcome.get("ok"):
            messages = {
                "not_a_player": "أنت لست أحد لاعبي هذه المباراة.",
                "not_your_turn": "انتظر دورك قبل إدخال الشفرة.",
                "invalid_answer": "الإجابة غير صالحة.",
                "game_finished": "انتهت المباراة بالفعل.",
            }
            await interaction.followup.send(messages.get(outcome.get("reason"), "تعذر قبول الإجابة."), ephemeral=True)
            return
        session = outcome.get("session")
        await interaction.followup.send(
            "✅ تم تسجيل إجابتك السرية." if (session or {}).get("state", {}).get("last_answer", {}).get("correct")
            else "تم تسجيل الإجابة السرية.", ephemeral=True
        )
        if not session:
            return
        try:
            channel = interaction.guild.get_channel(int(session["channel_id"])) if interaction.guild else None
            if channel is None:
                channel = await interaction.client.fetch_channel(int(session["channel_id"]))
            message = await channel.fetch_message(int(session["message_id"]))
            embed, image_file = await _game_presentation(
                session, interaction, reward=int(outcome.get("reward") or 0),
                reward_capped=bool(outcome.get("reward_capped")),
            )
            await message.edit(embed=embed, attachments=[image_file] if image_file else [], view=GameBoardView(session))
        except Exception:
            logger.exception("Could not refresh word board after modal submit for session %s", self.session_id)


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
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.primary,
                    disabled=terminal or str(session.get("current_player_id")) in (state.get("choices") or {}),
                    custom_id=f"primegame:rps:{self.session_id}:{choice}")
                button.callback = self._move({"choice": choice})
                self.add_item(button)
            used = state.get("abilities_used") or {}
            actor = str(session.get("current_player_id"))
            for ability, label, style in (
                ("double", "⚔️ ضربة مزدوجة", discord.ButtonStyle.danger),
                ("shield", "🛡️ درع", discord.ButtonStyle.primary),
            ):
                button = discord.ui.Button(label=label, style=style,
                    disabled=terminal or ability in used.get(actor, []) or actor in (state.get("choices") or {}),
                    row=1, custom_id=f"primegame:rps:{self.session_id}:{ability}")
                button.callback = self._move({"ability": ability})
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
            trap_button = discord.ui.Button(
                label="🃏 بطاقة الفخ / الخلط",
                style=discord.ButtonStyle.danger,
                row=4,
                disabled=terminal or bool((state.get("trap_used") or {}).get(str(session.get("current_player_id")), False)) or state.get("pending_index") is not None,
                custom_id=f"primegame:memory:{self.session_id}:trap",
            )
            trap_button.callback = self._move({"trap": True})
            self.add_item(trap_button)

        elif self.game_key == "dice":
            for action, label, style in (
                ({"roll": "roll"}, "🎲 ارم النرد", discord.ButtonStyle.primary),
                ({"reroll": True}, "🔄 إعادة الرمي", discord.ButtonStyle.secondary),
                ({"lock": True}, "🔒 تثبيت الرمية", discord.ButtonStyle.success),
                ({"ability": "double"}, "⚔️ ضربة مزدوجة", discord.ButtonStyle.danger),
                ({"ability": "shield"}, "🛡️ درع", discord.ButtonStyle.primary),
            ):
                ability = action.get("ability") or ("reroll" if action.get("reroll") else "")
                used = state.get("abilities_used") or {}
                actor = str(session.get("current_player_id"))
                rolls = state.get("round_rolls") or {}
                disabled = terminal
                if action.get("roll") and actor in rolls: disabled = True
                if action.get("reroll") and (actor not in rolls or "reroll" in used.get(actor, [])): disabled = True
                if action.get("lock") and (actor not in rolls or actor in set(state.get("committed") or [])): disabled = True
                if ability in {"double", "shield"} and ability in used.get(actor, []): disabled = True
                button = discord.ui.Button(label=label, style=style, disabled=disabled,
                    custom_id=f"primegame:dice:{self.session_id}:{ability or ('lock' if action.get('lock') else 'roll' if action.get('roll') else 'reroll')}")
                button.callback = self._move(action)
                self.add_item(button)

        elif self.game_key == "word":
            puzzles = list(state.get("puzzles") or [])
            round_index = int(state.get("round_index") or 0)
            if puzzles:
                answer_button = discord.ui.Button(
                    label="🔐 إدخال الشفرة",
                    style=discord.ButtonStyle.primary,
                    row=0, disabled=terminal,
                    custom_id=f"primegame:word:{self.session_id}:modal",
                )
                async def open_word_modal(interaction: discord.Interaction) -> None:
                    await interaction.response.send_modal(WordAnswerModal(self.session_id))
                answer_button.callback = open_word_modal
                self.add_item(answer_button)
                hint_button = discord.ui.Button(
                    label="💡 كشف حرف",
                    style=discord.ButtonStyle.secondary,
                    row=0, disabled=terminal or str(round_index) in (state.get("hints") or {}),
                    custom_id=f"primegame:word:{self.session_id}:hint",
                )
                hint_button.callback = self._move({"hint": True})
                self.add_item(hint_button)

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
                    "ability_used": "استخدمت هذه القدرة مسبقًا في المواجهة.",
                    "ability_after_choice": "لا يمكن استخدام القدرة بعد تثبيت اختيارك.",
                    "ability_after_lock": "لا يمكن استخدام القدرة بعد تثبيت الرمية.",
                    "trap_used": "استخدمت بطاقة الفخ بالفعل.",
                    "trap_pending_pair": "أكمل محاولة كشف الزوج قبل استخدام الفخ.",
                    "no_trap_targets": "لا توجد بطاقتان مغلقتان لتبديلهما.",
                    "hint_used": "استخدمت التلميح في هذه الجولة بالفعل.",
                    "roll_first": "ارم النرد أولًا قبل تثبيت الرمية أو إعادتها.",
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


_GAME_CATEGORIES = {
    "all": {
        "label": "🎮 جميع الألعاب",
        "description": "كل الألعاب التفاعلية المتاحة ضمن أوامر /game.",
        "keys": {"xo", "connect4", "rps", "quiz", "memory", "dice", "word"},
    },
    "strategy": {
        "label": "⚔️ المواجهات التكتيكية",
        "description": "ألعاب المواجهة التي تعتمد على الحركة والتخطيط.",
        "keys": {"xo", "connect4", "rps", "dice"},
    },
    "mind": {
        "label": "🧠 ألعاب الذكاء والتركيز",
        "description": "اختبر الذاكرة والمفردات والمعلومات العامة.",
        "keys": {"memory", "word", "quiz"},
    },
}


def _game_catalog_embed(category: str = "all") -> discord.Embed:
    selected = _GAME_CATEGORIES.get(category, _GAME_CATEGORIES["all"])
    embed = discord.Embed(
        title="🎮 PRIME • مركز الألعاب",
        description=(
            "اختر تصنيفًا من القائمة أسفل البطاقة لاستعراض الألعاب وأوامر تشغيلها.\n"
            "تحتاج ألعاب المواجهة إلى اختيار خصم من نفس السيرفر.\n\n"
            f"**{selected['label']}**\n{selected['description']}"
        ),
        color=0x7C3AED,
    )
    for item in registry.all():
        if item.key not in selected["keys"]:
            continue
        command, description = _GAME_COMMANDS.get(
            item.key, (f"/game {item.key}", "لعبة تفاعلية")
        )
        embed.add_field(
            name=f"{item.name}  •  🪙 {item.reward_coins} عملة",
            value=f"{description}\n**طريقة البدء:** `{command}`",
            inline=False,
        )
    embed.set_footer(text="PRIME GAMES • تتجدد القائمة حسب التصنيف المختار")
    embed.set_image(url="attachment://prime-game-list.png")
    return embed


def _game_catalog_banner() -> discord.File:
    """Build a compact, branded game-list banner with Pillow (no external assets)."""
    width, height = 1100, 300
    image = Image.new("RGB", (width, height), "#100b22")
    pixels = image.load()
    for x in range(width):
        blend = x / max(1, width - 1)
        left, right = (24, 10, 48), (72, 12, 125)
        color = tuple(int(left[i] * (1 - blend) + right[i] * blend) for i in range(3))
        for y in range(height):
            glow = max(0.0, 1.0 - abs(y - height * 0.48) / (height * 0.9)) * 0.12
            pixels[x, y] = tuple(min(255, int(channel + 255 * glow)) for channel in color)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 22, width - 24, height - 22), radius=28,
                           outline="#a855f7", width=3)
    draw.rounded_rectangle((48, 48, 270, height - 48), radius=22,
                           fill="#210d3b", outline="#7c3aed", width=2)
    # Geometric controller mark: robust across hosts without relying on emoji fonts.
    draw.rounded_rectangle((94, 105, 225, 190), radius=24,
                           fill="#7c3aed", outline="#e9d5ff", width=4)
    draw.rounded_rectangle((116, 125, 151, 135), radius=4, fill="#ffffff")
    draw.rounded_rectangle((129, 113, 139, 148), radius=4, fill="#ffffff")
    draw.ellipse((177, 125, 191, 139), fill="#ffffff")
    draw.ellipse((197, 145, 211, 159), fill="#f0abfc")
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 68)
        subtitle_font = ImageFont.truetype("DejaVuSans.ttf", 24)
        small_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 18)
    except OSError:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        small_font = ImageFont.load_default()
    draw.text((310, 76), "PRIME GAME CENTER", font=title_font, fill="#ffffff",
              stroke_width=1, stroke_fill="#c084fc")
    draw.text((316, 165), "CHOOSE YOUR GAME • CHALLENGE YOUR FRIENDS",
              font=subtitle_font, fill="#e9d5ff")
    draw.rounded_rectangle((316, 218, 640, 260), radius=18, fill="#6d28d9")
    draw.text((338, 228), "VISUAL GAME CATALOG", font=small_font, fill="#ffffff")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="prime-game-list.png")


class GameCatalogSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="جميع الألعاب", value="all", emoji="🎮",
                                 description="عرض كل ألعاب /game"),
            discord.SelectOption(label="المواجهات التكتيكية", value="strategy", emoji="⚔️",
                                 description="XO والنرد وحجر ورقة مقص والتوصيل"),
            discord.SelectOption(label="الذكاء والتركيز", value="mind", emoji="🧠",
                                 description="الذاكرة وفك الكلمة وتحدي المعلومات"),
        ]
        super().__init__(
            placeholder="اختر تصنيف الألعاب...",
            min_values=1, max_values=1, options=options,
            custom_id="prime:game_catalog:category",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=_game_catalog_embed(self.values[0]),
            view=self.view,
        )


class GameCatalogView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(GameCatalogSelect())


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

    @game.command(name="list", description="عرض قائمة ألعاب PRIME بصورة منظمة وتصنيفات")
    async def list_games(self, interaction: discord.Interaction) -> None:
        # Acknowledge before generating/encoding the Pillow asset. This keeps
        # the Discord 3-second interaction deadline independent of image work.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            banner = await asyncio.to_thread(_game_catalog_banner)
            await interaction.followup.send(
                embed=_game_catalog_embed("all"),
                file=banner,
                view=GameCatalogView(),
                ephemeral=True,
            )
        except Exception:
            logger.exception("Failed to render /game list for guild %s", interaction.guild_id)
            await interaction.followup.send(
                "تعذر تجهيز قائمة الألعاب المصوّرة الآن. حاول مرة أخرى بعد قليل.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
