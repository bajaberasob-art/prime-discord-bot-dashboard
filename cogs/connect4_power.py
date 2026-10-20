"""Power-Up Connect 4: neon 7x6 Connect Four with tactical abilities."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFilter

from game_engine import (
    CYAN, GREEN, MUTED, PURPLE, RED, WHITE, _font, draw_arabic_text,
    get_circular_avatar, create_luxury_canvas, draw_glow_panel,
    draw_radial_glow, image_to_file, UniversalLobbyView,
)

logger = logging.getLogger("PrimeConnect4Power")
ROWS = 6
COLS = 7
EMPTY = 0
PLAYERS = (1, 2)
POWERS = ("heavy_anvil", "freeze_column", "disk_bomb")
POWER_LABELS = {
    "heavy_anvil": "🔩 القرص الحديدي",
    "freeze_column": "❄️ تجميد عمود",
    "disk_bomb": "💣 قنبلة التفجير",
}
PLAYER_COLORS = {1: "#36e0d0", 2: "#ff5577"}


@dataclass
class Connect4Player:
    member: object
    player_no: int
    used_powers: set[str] = field(default_factory=set)

    @property
    def user_id(self) -> int:
        return int(getattr(self.member, "id"))

    @property
    def name(self) -> str:
        return str(getattr(self.member, "display_name", getattr(self.member, "name", "Player")))


@dataclass
class Connect4State:
    board: list[list[int]] = field(default_factory=lambda: [[EMPTY for _ in range(COLS)] for _ in range(ROWS)])
    current_player: int = 1
    frozen_column: int | None = None
    freeze_owner: int | None = None
    winner: int | None = None
    winning_cells: tuple[tuple[int, int], ...] = ()
    draw: bool = False
    last_action: str = "ابدأ المباراة باختيار عمود"
    selected_power: dict[int, str | None] = field(default_factory=lambda: {1: None, 2: None})


def new_board() -> list[list[int]]:
    return [[EMPTY for _ in range(COLS)] for _ in range(ROWS)]


def landing_row(board: Sequence[Sequence[int]], column: int) -> int | None:
    if not 0 <= int(column) < COLS:
        raise ValueError("invalid_column")
    for row in range(ROWS - 1, -1, -1):
        if board[row][column] == EMPTY:
            return row
    return None


def find_winner(board: Sequence[Sequence[int]]) -> tuple[int | None, tuple[tuple[int, int], ...]]:
    """Return winner and the first four connected cells, checking all 4 directions."""
    directions = ((0, 1), (1, 0), (1, 1), (1, -1))
    for r in range(ROWS):
        for c in range(COLS):
            player = board[r][c]
            if player == EMPTY:
                continue
            for dr, dc in directions:
                cells = tuple((r + step * dr, c + step * dc) for step in range(4))
                if all(0 <= rr < ROWS and 0 <= cc < COLS for rr, cc in cells):
                    if all(board[rr][cc] == player for rr, cc in cells):
                        return player, cells
    return None, ()


def apply_move(
    state: Connect4State,
    player: int,
    column: int,
    *,
    power: str | None = None,
    used_powers: set[str] | None = None,
) -> dict[str, object]:
    """Apply one turn and return an action summary; mutates the state board."""
    if player not in PLAYERS:
        raise ValueError("invalid_player")
    if state.winner is not None or state.draw:
        raise ValueError("game_over")
    if player != state.current_player:
        raise ValueError("not_your_turn")
    if not 0 <= int(column) < COLS:
        raise ValueError("invalid_column")
    if state.frozen_column == column and state.freeze_owner != player:
        raise ValueError("column_frozen")
    if power is not None and power not in POWERS:
        raise ValueError("invalid_power")
    used = used_powers if used_powers is not None else set()
    if power is not None and power in used:
        raise ValueError("power_already_used")
    if power == "freeze_column":
        if state.frozen_column is not None:
            raise ValueError("column_already_frozen")
        state.frozen_column = column
        state.freeze_owner = player
        state.last_action = f"❄️ اللاعب {player} جمّد العمود {column + 1}"
        state.current_player = 3 - player
        return {"player": player, "column": column, "power": power, "removed": []}

    row = landing_row(state.board, column)
    if row is None:
        raise ValueError("column_full")
    removed: list[tuple[int, int]] = []
    if power == "heavy_anvil" and row < ROWS - 1 and state.board[row + 1][column] != EMPTY:
        # The anvil crushes the disk directly beneath its landing point and occupies that cell.
        removed.append((row + 1, column))
        state.board[row + 1][column] = EMPTY
        row += 1
    state.board[row][column] = player
    if power == "disk_bomb":
        for rr in range(max(0, row - 1), min(ROWS, row + 2)):
            for cc in range(max(0, column - 1), min(COLS, column + 2)):
                if state.board[rr][cc] != EMPTY:
                    removed.append((rr, cc))
                    state.board[rr][cc] = EMPTY
    if power is not None:
        used.add(power)
    winner, cells = find_winner(state.board)
    if winner is not None:
        state.winner = winner
        state.winning_cells = cells
        state.last_action = f"🏆 اللاعب {winner} حقق أربعة متتالية!"
    elif all(cell != EMPTY for row_values in state.board for cell in row_values):
        state.draw = True
        state.last_action = "تعادل — امتلأت الشبكة"
    else:
        state.last_action = f"اللاعب {player} لعب في العمود {column + 1}" + (f" باستخدام {POWER_LABELS[power]}" if power else "")
        state.current_player = 3 - player
    # A freeze lasts until the frozen opponent completes their next legal move.
    if state.frozen_column is not None and player != state.freeze_owner:
        state.frozen_column = None
        state.freeze_owner = None
    return {"player": player, "column": column, "row": row, "power": power, "removed": removed}


def render_connect4_board(
    players: Sequence[Connect4Player],
    state: Connect4State,
    avatars: dict[int, Image.Image] | None = None,
) -> Image.Image:
    """Render a 7x6 glassy neon board, avatar disks, freeze and victory effects."""
    width, height = 1200, 850
    accent = GREEN if state.winner else (PLAYER_COLORS[state.current_player] if not state.draw else PURPLE)
    image = create_luxury_canvas(width, height, "POWER-UP CONNECT 4", "توصيل 4 بالقدرات الخارقة • شبكة 7 × 6", accent=accent).convert("RGBA")
    draw_radial_glow(image, (600, 430), 340, color=accent, max_alpha=45, blur=28)
    draw = ImageDraw.Draw(image)
    draw_glow_panel(image, (175, 155, 1025, 745), fill=(9, 20, 39, 235), outline="#34466a", radius=28, glow=accent, width=3)
    draw = ImageDraw.Draw(image)
    board_x, board_y, cell = 210, 190, 112
    # Soft outer neon edge and individual transparent-glass cells.
    glow_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow_layer)
    gd.rounded_rectangle((board_x - 9, board_y - 9, board_x + COLS * cell + 9, board_y + ROWS * cell + 9),
                         radius=22, outline=(54, 224, 208, 180), width=7)
    image.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(14)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((board_x, board_y, board_x + COLS * cell, board_y + ROWS * cell),
                           radius=18, fill=(13, 28, 51, 225), outline="#36e0d0", width=3)
    avatars = avatars or {}
    for r in range(ROWS):
        for c in range(COLS):
            x, y = board_x + c * cell, board_y + r * cell
            cx, cy = x + cell // 2, y + cell // 2
            if state.board[r][c] == EMPTY:
                fill = (4, 9, 21, 220)
                draw.ellipse((cx - 42, cy - 42, cx + 42, cy + 42), fill=fill, outline="#34435d", width=3)
            else:
                player = state.board[r][c]
                color = PLAYER_COLORS[player]
                halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
                ImageDraw.Draw(halo).ellipse((cx - 43, cy - 43, cx + 43, cy + 43),
                                             outline=color, width=8)
                image.alpha_composite(halo.filter(ImageFilter.GaussianBlur(8)))
                draw = ImageDraw.Draw(image)
                draw.ellipse((cx - 42, cy - 42, cx + 42, cy + 42), fill="#111827", outline=color, width=5)
                avatar = avatars.get(player)
                if avatar is not None:
                    avatar = avatar.convert("RGBA").resize((72, 72))
                    mask = Image.new("L", (72, 72), 0)
                    ImageDraw.Draw(mask).ellipse((0, 0, 71, 71), fill=255)
                    image.paste(avatar, (cx - 36, cy - 36), mask)
                    draw = ImageDraw.Draw(image)
                    draw.ellipse((cx - 37, cy - 37, cx + 37, cy + 37), outline=color, width=3)
                else:
                    draw.ellipse((cx - 27, cy - 27, cx + 27, cy + 27), fill=color)
                    draw_arabic_text(draw, (cx, cy), str(player), font=_font(24, True), fill="#07111f", anchor="mm")
            if state.frozen_column == c:
                frost = Image.new("RGBA", image.size, (0, 0, 0, 0))
                fd = ImageDraw.Draw(frost)
                fd.rectangle((x + 4, board_y + 4, x + cell - 4, board_y + ROWS * cell - 4),
                             fill=(115, 211, 255, 30), outline=(170, 235, 255, 210), width=4)
                for k in range(1, 5):
                    fx, fy = x + 12 + k * 17, board_y + 24 + k * 80
                    fd.line((fx - 7, fy, fx + 7, fy), fill=(200, 245, 255, 180), width=2)
                    fd.line((fx, fy - 7, fx, fy + 7), fill=(200, 245, 255, 180), width=2)
                image.alpha_composite(frost)
                draw = ImageDraw.Draw(image)
    if state.winning_cells:
        centers = [(board_x + c * cell + cell // 2, board_y + r * cell + cell // 2) for r, c in state.winning_cells]
        line = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(line)
        ld.line(centers, fill=(255, 255, 255, 255), width=12, joint="curve")
        image.alpha_composite(line.filter(ImageFilter.GaussianBlur(13)))
        image.alpha_composite(line)
        draw = ImageDraw.Draw(image)
        for cx, cy in centers:
            draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill="#ffffff", outline=GREEN, width=4)
    draw = ImageDraw.Draw(image)
    status = "تعادل" if state.draw else f"الفائز: اللاعب {state.winner}" if state.winner else f"الدور الحالي: اللاعب {state.current_player}"
    draw_arabic_text(draw, (600, 125), status, font=_font(24, True), fill=GREEN if state.winner else accent, anchor="mm")
    draw_arabic_text(draw, (600, 790), state.last_action[:90], font=_font(17, True), fill=WHITE, anchor="mm")
    for player in players:
        x = 190 if player.player_no == 1 else 770
        color = PLAYER_COLORS[player.player_no]
        draw_glow_panel(image, (x, 70, x + 240, 130), outline=color, glow=color, radius=16)
        draw = ImageDraw.Draw(image)
        draw_arabic_text(draw, (x + 120, 100), f"P{player.player_no}: {player.name[:16]}", font=_font(16, True), fill=WHITE, anchor="mm")
        powers_left = sum(power not in player.used_powers for power in POWERS)
        draw_arabic_text(draw, (x + 120, 145), f"القدرات المتبقية: {powers_left}/3", font=_font(13), fill=color, anchor="mm")
    return image


class Connect4PowerView(discord.ui.View):
    def __init__(self, game: "Connect4PowerGame") -> None:
        super().__init__(timeout=None)
        self.game = game
        for col in range(COLS):
            button = discord.ui.Button(label=str(col + 1), style=discord.ButtonStyle.primary, row=0, custom_id=f"c4p:col:{col}")
            button.callback = self._column_callback(col)
            self.add_item(button)
        for power, label, emoji in (
            ("heavy_anvil", "القرص الحديدي", "🔩"),
            ("freeze_column", "تجميد عمود", "❄️"),
            ("disk_bomb", "قنبلة التفجير", "💣"),
        ):
            button = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=1, custom_id=f"c4p:power:{power}")
            button.callback = self._power_callback(power)
            self.add_item(button)

    def _column_callback(self, col: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self.game.play(interaction, col)
        return callback

    def _power_callback(self, power: str):
        async def callback(interaction: discord.Interaction) -> None:
            await self.game.select_power(interaction, power)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.game.finished:
            await interaction.response.send_message("انتهت المباراة.", ephemeral=True)
            return False
        if int(interaction.user.id) not in self.game.by_id:
            await interaction.response.send_message("هذه المباراة للاعبين المسجلين فقط.", ephemeral=True)
            return False
        if self.game.by_id[int(interaction.user.id)].player_no != self.game.state.current_player:
            await interaction.response.send_message("انتظر دورك.", ephemeral=True)
            return False
        return True


class Connect4PowerGame:
    def __init__(self, cog: "Connect4Power", members: Sequence[object]) -> None:
        if len(members) != 2:
            raise ValueError("connect4_requires_two_players")
        self.cog = cog
        self.players = [Connect4Player(members[0], 1), Connect4Player(members[1], 2)]
        self.by_id = {p.user_id: p for p in self.players}
        self.state = Connect4State()
        self.avatars: dict[int, Image.Image] = {}
        self.message: discord.Message | None = None
        self.view = Connect4PowerView(self)
        self.finished = False
        self.lock = asyncio.Lock()

    def file(self):
        return image_to_file(render_connect4_board(self.players, self.state, self.avatars), "prime-connect4-power.png")

    def embed(self) -> discord.Embed:
        state = self.state
        if state.winner:
            description = f"🏆 الفائز: **{self.players[state.winner - 1].name}**"
        elif state.draw:
            description = "انتهت المباراة بالتعادل."
        else:
            current = self.players[state.current_player - 1]
            description = f"الدور: **{current.name}**\nاختر العمود 1–7 أو فعّل قدرة ثم اختر العمود. كل قدرة تُستخدم مرة واحدة."
        for player in self.players:
            powers = [POWER_LABELS[p] for p in POWERS if p not in player.used_powers]
            description += f"\n**{player.name}:** " + (", ".join(powers) if powers else "انتهت القدرات")
        if state.frozen_column is not None:
            description += f"\n❄️ العمود {state.frozen_column + 1} مجمّد حتى ينفّذ الخصم حركة قانونية."
        return discord.Embed(title="⚡ Power-Up Connect 4", description=description, color=discord.Color.teal())

    async def select_power(self, interaction: discord.Interaction, power: str) -> None:
        async with self.lock:
            player = self.by_id[int(interaction.user.id)]
            if power in player.used_powers:
                await interaction.response.send_message("استخدمت هذه القدرة سابقًا في المباراة.", ephemeral=True)
                return
            if power == "freeze_column" and self.state.frozen_column is not None:
                await interaction.response.send_message("يوجد عمود مجمّد بالفعل.", ephemeral=True)
                return
            self.state.selected_power[player.player_no] = power
            await interaction.response.send_message(f"تم اختيار {POWER_LABELS[power]}. اختر العمود الآن.", ephemeral=True)

    async def play(self, interaction: discord.Interaction, column: int) -> None:
        async with self.lock:
            player = self.by_id[int(interaction.user.id)]
            power = self.state.selected_power.get(player.player_no)
            try:
                apply_move(self.state, player.player_no, column, power=power, used_powers=player.used_powers)
            except ValueError as exc:
                messages = {
                    "invalid_column": "اختر عمودًا من 1 إلى 7.",
                    "column_full": "هذا العمود ممتلئ.",
                    "column_frozen": "هذا العمود مجمّد لدورك؛ اختر عمودًا آخر.",
                    "power_already_used": "استخدمت هذه القدرة سابقًا.",
                    "column_already_frozen": "يوجد عمود مجمّد بالفعل.",
                }
                await interaction.response.send_message(messages.get(str(exc), "لا يمكن تنفيذ هذه الحركة."), ephemeral=True)
                return
            self.state.selected_power[player.player_no] = None
            self.finished = self.state.winner is not None or self.state.draw
            file = self.file()
            try:
                await interaction.response.edit_message(embed=self.embed(), attachments=[file], view=None if self.finished else self.view)
            finally:
                file.close()
            if self.finished:
                await self.cog.release(self)

    async def start(self, message: discord.Message) -> None:
        self.message = message
        images = await asyncio.gather(
            *(get_circular_avatar(p.member, 72, session=getattr(self.cog.bot, "session", None), timeout=3,
                                  border_color=PLAYER_COLORS[p.player_no]) for p in self.players),
            return_exceptions=True,
        )
        for p, avatar in zip(self.players, images):
            if isinstance(avatar, Image.Image):
                self.avatars[p.player_no] = avatar
        file = self.file()
        try:
            await message.edit(embed=self.embed(), attachments=[file], view=self.view)
        finally:
            file.close()

    async def release(self) -> None:
        await self.cog.release(self)


class Connect4Power(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.games: dict[int, Connect4PowerGame] = {}
        self.lock = asyncio.Lock()

    async def release(self, game: Connect4PowerGame) -> None:
        guild_id = getattr(getattr(game.message, "guild", None), "id", None)
        if guild_id is not None:
            async with self.lock:
                if self.games.get(int(guild_id)) is game:
                    self.games.pop(int(guild_id), None)

    @app_commands.command(name="connect4_power", description="ابدأ لعبة توصيل 4 بالقدرات الخارقة")
    @app_commands.guild_only()
    async def connect4_power(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("اللعبة متاحة داخل السيرفر فقط.", ephemeral=True)
            return
        guild_id = int(interaction.guild.id)
        async with self.lock:
            if guild_id in self.games:
                await interaction.response.send_message("توجد مباراة توصيل 4 نشطة في هذا السيرفر.", ephemeral=True)
                return

        async def on_start(lobby_interaction: discord.Interaction, members: Sequence[object]) -> None:
            if len(members) != 2:
                await lobby_interaction.followup.send("تحتاج المباراة إلى لاعبين اثنين بالضبط.", ephemeral=True)
                return
            game = Connect4PowerGame(self, members)
            async with self.lock:
                if guild_id in self.games:
                    await lobby_interaction.followup.send("توجد مباراة نشطة بالفعل.", ephemeral=True)
                    return
                self.games[guild_id] = game
            board_file = game.file()
            try:
                message = await lobby_interaction.followup.send("⚡ بدأت مواجهة Power-Up Connect 4!", embed=game.embed(), file=board_file, view=game.view, wait=True)
            finally:
                board_file.close()
            await game.start(message)

        lobby = UniversalLobbyView(
            interaction.user,
            game_name="Power-Up Connect 4",
            rules=("مواجهة ثنائية على شبكة 7×6. اختر عمودًا 1–7. كل لاعب يملك 3 قدرات تُستخدم مرة واحدة: "
                   "🔩 القرص الحديدي يسحق القرص أسفله، ❄️ تجميد عمود لدور الخصم، 💣 القنبلة تمسح مساحة 3×3."),
            on_start=on_start,
            min_players=2,
            max_players=2,
            timeout=300,
            avatar_session=getattr(self.bot, "session", None),
        )
        lobby_file = await lobby.render_lobby_file()
        try:
            await interaction.response.send_message(embed=lobby.lobby_embed(), file=lobby_file, view=lobby)
        finally:
            lobby_file.close()
        await lobby.bind_message(await interaction.original_response())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Connect4Power(bot))
