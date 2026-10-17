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


def _game_embed(session: dict, *, reward: int = 0) -> discord.Embed:
    players = session["players"]
    state = session.get("state") or {}
    board = state.get("board") or [None] * 9
    description = (
        f"❎ **X:** <@{players[0]}>\n"
        f"⭕ **O:** <@{players[1]}>\n\n"
    )
    if session["status"] == "finished":
        if session.get("winner_id") is not None:
            description += f"🏆 الفائز: <@{session['winner_id']}>"
            if reward:
                description += f"\n💰 أضيفت مكافأة **{reward}** عملة إلى محفظته."
        else:
            description += "🤝 انتهت المباراة بالتعادل."
        color = 0x22C55E if session.get("winner_id") is not None else 0x64748B
    elif session["status"] == "cancelled":
        description += "🔒 تم إيقاف المباراة."
        color = 0x64748B
    else:
        description += f"🎯 الدور الآن على <@{session['current_player_id']}>"
        color = 0x7C3AED

    embed = discord.Embed(
        title="🎮 PRIME · XO",
        description=description,
        color=color,
    )
    embed.add_field(
        name="اللوحة",
        value="اضغط المربع الذي تريد اللعب فيه. لا يمكن تغيير حركة بعد تسجيلها.",
        inline=False,
    )
    embed.set_footer(text=f"PRIME GAME #{session['id']} • ثلاث علامات متتالية للفوز")
    return embed


async def _game_presentation(
    session: dict,
    interaction: discord.Interaction,
    *,
    reward: int = 0,
) -> tuple[discord.Embed, discord.File | None]:
    """Prepare an embed and an optional custom board image without blocking Discord."""
    guild = interaction.guild
    players = list(session.get("players") or [])
    names: list[str] = []
    for index, user_id in enumerate(players[:2]):
        member = guild.get_member(int(user_id)) if guild is not None else None
        if member is None:
            names.append(f"Player {index + 1}")
        else:
            names.append(str(member.display_name))
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
        embed = _game_embed(session, reward=reward)
        embed.set_image(url="attachment://prime-xo.png")
        return embed, discord.File(image, filename="prime-xo.png")
    except Exception:
        logger.exception("Could not render XO card for session %s; using embed fallback", session.get("id"))
        return _game_embed(session, reward=reward), None


class GameBoardView(discord.ui.View):
    def __init__(self, session: dict):
        super().__init__(timeout=None)
        self.session_id = int(session["id"])
        state = session.get("state") or {}
        board = state.get("board") or [None] * 9
        terminal = session.get("status") != "active"
        for index, value in enumerate(board):
            label = str(value) if value else str(index + 1)
            if value == "X":
                style = discord.ButtonStyle.primary
            elif value == "O":
                style = discord.ButtonStyle.danger
            else:
                style = discord.ButtonStyle.secondary
            button = discord.ui.Button(
                label=label,
                style=style,
                row=index // 3,
                disabled=terminal or value is not None,
                custom_id=f"primegame:xo:{self.session_id}:{index}",
            )
            button.callback = self._move(index)
            self.add_item(button)

    def _move(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            outcome = await apply_action(
                self.session_id,
                interaction.user.id,
                {"index": index},
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
                }
                await interaction.followup.send(
                    messages.get(outcome.get("reason"), "تعذر تسجيل الحركة؛ حاول تحديث اللوحة."),
                    ephemeral=True,
                )
                return

            session = outcome.get("session")
            if not session:
                await interaction.followup.send("تم حفظ الحركة لكن تعذر تحميل اللوحة.", ephemeral=True)
                return
            embed, image_file = await _game_presentation(
                session,
                interaction,
                reward=int(outcome.get("reward") or 0),
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

    @game.command(name="xo", description="تحدّ لاعبًا في لعبة XO التفاعلية")
    @app_commands.guild_only()
    async def xo(self, interaction: discord.Interaction, opponent: discord.Member) -> None:
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
        try:
            session = await create_session(
                guild_id=interaction.guild.id,
                channel_id=interaction.channel.id,
                game_key="xo",
                players=[interaction.user.id, opponent.id],
            )
            embed, image_file = await _game_presentation(session, interaction)
            send_options = {
                "content": f"⚔️ {interaction.user.mention} تحدّى {opponent.mention} في XO!",
                "embed": embed,
                "view": GameBoardView(session),
                "allowed_mentions": discord.AllowedMentions(users=[interaction.user, opponent]),
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
            logger.exception("Could not create XO session in guild %s", interaction.guild.id)
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
            f"✅ نُشرت مباراة XO رقم {session['id']} في القناة. يبدأ الدور معك.",
            ephemeral=True,
        )

    @game.command(name="list", description="عرض الألعاب المتاحة في PRIME")
    async def list_games(self, interaction: discord.Interaction) -> None:
        lines = [
            f"• **{item.name}** — {item.min_players} إلى {item.max_players} لاعبين"
            for item in registry.all()
        ]
        embed = discord.Embed(
            title="🎮 ألعاب PRIME",
            description="\n".join(lines) or "لا توجد ألعاب مسجلة.",
            color=0x7C3AED,
        )
        embed.set_footer(text="يمكن بدء XO من الأمر /game xo")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
