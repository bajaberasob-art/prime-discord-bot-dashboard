"""Reusable visual lobby and Pillow rendering utilities for PRIME multiplayer games.

The module intentionally stays separate from PRIME's durable two-player game
engine: lobby membership and real-time round state belong to each live game.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Sequence

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:  # Arabic support is optional at runtime; English remains usable.
    arabic_reshaper = None
    get_display = None

logger = logging.getLogger("PrimeGameEngine")

BACKGROUND = "#090b14"
PANEL = "#111625"
PANEL_EDGE = "#29324a"
WHITE = "#f4f6ff"
MUTED = "#aab3c8"
PURPLE = "#a855f7"
CYAN = "#36e0d0"
GREEN = "#35e68b"
RED = "#ff5577"
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
DEFAULT_FONT_CANDIDATES = (
    FONT_DIR / "NotoSansArabic-Regular.ttf",
    FONT_DIR / "DejaVuSans.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf"),
)
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Load an available project/system font and fall back to Pillow's bitmap font."""
    candidates = []
    if bold:
        candidates.extend((FONT_DIR / "NotoSansArabic-Bold.ttf",
                           FONT_DIR / "DejaVuSans-Bold.ttf",
                           Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")))
    candidates.extend(DEFAULT_FONT_CANDIDATES)
    for path in candidates:
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), size=size)
        except (OSError, ValueError):
            logger.debug("Could not load font %s", path, exc_info=True)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions whose load_default has no size parameter.
        return ImageFont.load_default()


def _safe_text(value: object, limit: int = 240) -> str:
    """Normalize user-supplied text before passing it to Pillow."""
    text = str(value or "")
    text = "".join(char for char in text if char >= " " and char not in "\x7f")
    return text[:limit]


def _shape_arabic(value: object) -> str:
    """Shape Arabic text for Pillow; degrade gracefully if optional libraries fail."""
    text = _safe_text(value)
    if arabic_reshaper is None or get_display is None:
        return text
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        logger.debug("Arabic text shaping failed; using unshaped text", exc_info=True)
        return text


def draw_arabic_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: object,
    *,
    font: ImageFont.ImageFont | None = None,
    fill: str | tuple[int, int, int] = WHITE,
    anchor: str | None = None,
    direction: str | None = None,
    stroke_width: int = 0,
    stroke_fill: str | tuple[int, int, int] | None = None,
) -> None:
    """Draw Arabic/Latin mixed text with RTL shaping when the optional packages exist."""
    shaped = _shape_arabic(text)
    try:
        options = {
            "font": font or _font(22),
            "fill": fill,
            "stroke_width": max(0, int(stroke_width)),
        }
        if anchor:
            options["anchor"] = anchor
        if stroke_fill is not None:
            options["stroke_fill"] = stroke_fill
        # Do not pass direction="rtl": reshaped text has already been reordered.
        draw.text(xy, shaped, **options)
    except (TypeError, ValueError, OSError):
        logger.debug("Pillow rejected styled text options; using basic text", exc_info=True)
        draw.text(xy, shaped, font=font or _font(22), fill=fill)


def _placeholder_avatar(size: int, label: str = "?") -> Image.Image:
    size = max(16, min(int(size), 1024))
    image = Image.new("RGBA", (size, size), (20, 25, 42, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, size - 3, size - 3), fill="#25213c", outline=PURPLE, width=max(2, size // 32))
    initial = (_safe_text(label, 2).strip() or "?")[0].upper()
    font = _font(max(12, size // 2), bold=True)
    box = draw.textbbox((0, 0), initial, font=font)
    draw.text(((size - (box[2] - box[0])) / 2, (size - (box[3] - box[1])) / 2 - box[1]),
              initial, font=font, fill=WHITE)
    return image


async def get_circular_avatar(
    user: object,
    size: int = 96,
    *,
    session: aiohttp.ClientSession | None = None,
    timeout: float = 6.0,
    border_color: str = PURPLE,
) -> Image.Image:
    """Download a Discord avatar with aiohttp and return a framed circular RGBA image.

    A supplied session remains owned by the caller. If none is supplied, this
    function creates and closes its own session. Network, decoding and avatar
    URL failures return a placeholder rather than aborting a game.
    """
    size = max(24, min(int(size), 512))
    name = _safe_text(getattr(user, "display_name", getattr(user, "name", "?")), 2)
    avatar = _placeholder_avatar(size, name)
    url = None
    try:
        display_avatar = getattr(user, "display_avatar", None)
        url = str(getattr(display_avatar, "url", "") or "")
    except Exception:
        logger.debug("Could not resolve a Discord avatar URL", exc_info=True)
    if not url:
        return _frame_avatar(avatar, size, border_color)

    owns_session = session is None
    client = session
    try:
        if client is None:
            client = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=max(1.0, float(timeout)))
            )
        async with client.get(url, timeout=aiohttp.ClientTimeout(total=max(1.0, float(timeout)))) as response:
            if response.status < 200 or response.status >= 300:
                logger.debug("Avatar request returned HTTP %s", response.status)
                return _frame_avatar(avatar, size, border_color)
            raw = await response.content.read(MAX_IMAGE_BYTES + 1)
            if not raw or len(raw) > MAX_IMAGE_BYTES:
                return _frame_avatar(avatar, size, border_color)
        with Image.open(io.BytesIO(raw)) as source:
            source.load()
            avatar = ImageOps.fit(source.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, TypeError):
        logger.debug("Avatar download/decode failed; using placeholder", exc_info=True)
    except Exception:
        logger.warning("Unexpected avatar rendering error; using placeholder", exc_info=True)
    finally:
        if owns_session and client is not None and not client.closed:
            await client.close()
    return _frame_avatar(avatar, size, border_color)


def _frame_avatar(image: Image.Image, size: int, border_color: str) -> Image.Image:
    image = ImageOps.fit(image.convert("RGBA"), (size, size), method=Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, size - 3, size - 3), fill=255)
    image.putalpha(mask)
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    frame.alpha_composite(image)
    ImageDraw.Draw(frame).ellipse(
        (1, 1, size - 2, size - 2),
        outline=border_color,
        width=max(2, size // 28),
    )
    return frame


def create_base_canvas(
    width: int = 1200,
    height: int = 700,
    title: str = "PRIME GAMES",
    subtitle: str = "",
) -> Image.Image:
    """Create a reusable dark neon canvas with a framed title and subtitle."""
    width = max(320, min(int(width), 2400))
    height = max(240, min(int(height), 1600))
    image = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    # Restrained neon bands provide a consistent game identity without heavy filters.
    draw.rectangle((0, 0, width, 7), fill=PURPLE)
    draw.rectangle((0, 7, width // 3, 11), fill=CYAN)
    draw.rounded_rectangle(
        (22, 22, width - 22, height - 22),
        radius=28,
        fill=PANEL,
        outline=PANEL_EDGE,
        width=2,
    )
    draw_arabic_text(draw, (width // 2, 42), _safe_text(title, 90),
                     font=_font(max(24, min(42, width // 24)), True),
                     fill=WHITE, anchor="ma")
    if subtitle:
        draw_arabic_text(draw, (width // 2, 92), _safe_text(subtitle, 180),
                         font=_font(max(14, min(22, width // 48))),
                         fill=MUTED, anchor="ma")
    draw.line((width // 2 - 70, 126, width // 2 + 70, 126), fill=CYAN, width=3)
    return image


def image_to_file(image: Image.Image, filename: str = "prime-game.png") -> discord.File:
    """Encode a Pillow image as a Discord-uploadable PNG file."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


StartCallback = Callable[[discord.Interaction, list[discord.Member]], Awaitable[None]]


class UniversalLobbyView(discord.ui.View):
    """Reusable join/leave/start/rules lobby for a Discord multiplayer game."""

    def __init__(
        self,
        host: discord.Member,
        *,
        game_name: str,
        rules: str,
        on_start: StartCallback,
        min_players: int = 2,
        max_players: int = 10,
        timeout: float | None = 300.0,
        avatar_session: aiohttp.ClientSession | None = None,
    ) -> None:
        if min_players < 1 or max_players < min_players:
            raise ValueError("invalid_player_limits")
        if not callable(on_start):
            raise TypeError("on_start must be an async callback")
        super().__init__(timeout=timeout)
        self.host = host
        self.game_name = _safe_text(game_name, 80) or "لعبة جماعية"
        self.rules_text = _safe_text(rules, 1800) or "لا توجد قوانين إضافية."
        self.on_start_callback = on_start
        self.min_players = int(min_players)
        self.max_players = int(max_players)
        self.players: dict[int, discord.Member] = {int(host.id): host}
        self.avatar_session = avatar_session
        self.message: discord.Message | None = None
        self.closed = False
        self._state_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._set_button_labels()

    def _set_button_labels(self) -> None:
        self.join_button.label = f"انضمام ({len(self.players)}/{self.max_players})"
        self.leave_button.disabled = self.closed or int(self.host.id) not in self.players
        self.join_button.disabled = self.closed or len(self.players) >= self.max_players
        self.start_button.disabled = self.closed or len(self.players) < self.min_players
        self.rules_button.disabled = self.closed

    @discord.ui.button(label="انضمام", style=discord.ButtonStyle.success, emoji="🎮", row=0)
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._state_lock:
            if self.closed:
                await interaction.response.send_message("تم إغلاق اللوبي.", ephemeral=True)
                return
            user = interaction.user
            if not isinstance(user, discord.Member):
                await interaction.response.send_message("هذه اللعبة متاحة داخل السيرفر فقط.", ephemeral=True)
                return
            if int(user.id) in self.players:
                await interaction.response.send_message("أنت منضم بالفعل إلى اللوبي.", ephemeral=True)
                return
            if len(self.players) >= self.max_players:
                await interaction.response.send_message("اكتمل عدد اللاعبين.", ephemeral=True)
                return
            self.players[int(user.id)] = user
            self._set_button_labels()
        await interaction.response.defer()
        await self._refresh_message()

    @discord.ui.button(label="مغادرة", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def leave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._state_lock:
            if self.closed:
                await interaction.response.send_message("تم إغلاق اللوبي.", ephemeral=True)
                return
            user_id = int(interaction.user.id)
            if user_id == int(self.host.id):
                await interaction.response.send_message("المضيف لا يستطيع مغادرة لوبيه؛ يمكنه إغلاقه أو بدء اللعبة.", ephemeral=True)
                return
            if user_id not in self.players:
                await interaction.response.send_message("أنت لست ضمن اللاعبين.", ephemeral=True)
                return
            self.players.pop(user_id, None)
            self._set_button_labels()
        await interaction.response.defer()
        await self._refresh_message()

    @discord.ui.button(label="بدء اللعبة", style=discord.ButtonStyle.primary, emoji="🚀", row=0)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with self._state_lock:
            if self.closed:
                await interaction.response.send_message("تم إغلاق اللوبي بالفعل.", ephemeral=True)
                return
            if int(interaction.user.id) != int(self.host.id):
                await interaction.response.send_message("بدء اللعبة متاح للمضيف فقط.", ephemeral=True)
                return
            if len(self.players) < self.min_players:
                await interaction.response.send_message(
                    f"تحتاج إلى {self.min_players} لاعبين على الأقل؛ الموجودون الآن {len(self.players)}.",
                    ephemeral=True,
                )
                return
            self.closed = True
            players = list(self.players.values())
            self._set_button_labels()
        await interaction.response.defer()
        try:
            await self.on_start_callback(interaction, players)
        except Exception:
            logger.exception("Could not start multiplayer game %s", self.game_name)
            self.closed = False
            self._set_button_labels()
            try:
                await interaction.followup.send(
                    "تعذر بدء اللعبة بسبب خطأ داخلي. بقي اللوبي متاحًا للمحاولة مجددًا.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                logger.debug("Could not report lobby start failure", exc_info=True)
            await self._refresh_message()

    @discord.ui.button(label="شرح القوانين", style=discord.ButtonStyle.secondary, emoji="📖", row=0)
    async def rules_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            f"**قوانين {self.game_name}**\n{self.rules_text}",
            ephemeral=True,
        )

    async def on_timeout(self) -> None:
        async with self._state_lock:
            if self.closed:
                return
            self.closed = True
            self._set_button_labels()
        await self._refresh_message()

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item[object],
    ) -> None:
        logger.exception("Lobby component failed for %s", self.game_name, exc_info=error)
        if not interaction.response.is_done():
            try:
                await interaction.response.send_message(
                    "تعذر تنفيذ هذا الإجراء. حاول مجددًا.", ephemeral=True
                )
            except discord.HTTPException:
                pass

    async def bind_message(self, message: discord.Message) -> None:
        """Bind the message after sending it so subsequent actions edit in place."""
        self.message = message
        await self._refresh_message()

    def render_lobby_image(self) -> Image.Image:
        """Synchronous text-only lobby rendering; avatars are added asynchronously."""
        width, height = 1100, 360 + ((self.max_players + 3) // 4) * 118
        image = create_base_canvas(width, height, self.game_name, "LOBBY • انتظر اكتمال اللاعبين")
        draw = ImageDraw.Draw(image)
        draw_arabic_text(draw, (width - 52, 148), f"اللاعبون: {len(self.players)} / {self.max_players}",
                         font=_font(22, True), fill=CYAN, anchor="ra")
        columns = 4
        for index, player in enumerate(self.players.values()):
            column, row = index % columns, index // columns
            x = 60 + column * 260
            y = 176 + row * 118
            draw.rounded_rectangle((x, y, x + 244, y + 96), radius=16,
                                   fill="#171d2d", outline=CYAN if player.id == self.host.id else PANEL_EDGE, width=2)
            draw.ellipse((x + 14, y + 18, x + 74, y + 78), fill="#29233f", outline=PURPLE, width=2)
            draw_arabic_text(draw, (x + 44, y + 48), _safe_text(getattr(player, "display_name", player.name), 1)[:1],
                             font=_font(25, True), fill=WHITE, anchor="mm")
            draw_arabic_text(draw, (x + 88, y + 28), _safe_text(getattr(player, "display_name", player.name), 18),
                             font=_font(18, True), fill=WHITE)
            draw_arabic_text(draw, (x + 88, y + 59), "المضيف" if player.id == self.host.id else "جاهز",
                             font=_font(14), fill=CYAN if player.id == self.host.id else MUTED)
        return image

    async def render_lobby_file(self) -> discord.File:
        """Render the lobby with downloaded circular avatars and safe placeholders."""
        image = self.render_lobby_image().convert("RGBA")
        draw = ImageDraw.Draw(image)
        for index, player in enumerate(self.players.values()):
            column, row = index % 4, index // 4
            x, y = 60 + column * 260, 176 + row * 118
            avatar = await get_circular_avatar(
                player, 60, session=self.avatar_session,
                border_color=CYAN if player.id == self.host.id else PURPLE,
            )
            image.alpha_composite(avatar, (x + 14, y + 18))
            # Cover the placeholder avatar underneath with the downloaded one.
            draw = ImageDraw.Draw(image)
        return image_to_file(image, "prime-lobby.png")

    def lobby_embed(self) -> discord.Embed:
        status = "مغلق" if self.closed else "بانتظار اللاعبين"
        embed = discord.Embed(
            title=f"🎮 {self.game_name}",
            description=(
                f"**الحالة:** {status}\n"
                f"**المضيف:** {self.host.mention}\n"
                f"**اللاعبون:** {len(self.players)}/{self.max_players}\n"
                f"**الحد الأدنى:** {self.min_players} لاعبين"
            ),
            color=discord.Color.from_rgb(168, 85, 247),
        )
        embed.set_footer(text="PRIME • Multiplayer Arcade")
        return embed

    async def _refresh_message(self) -> None:
        if self.message is None:
            return
        async with self._refresh_lock:
            self._set_button_labels()
            try:
                image_file = await self.render_lobby_file()
                await self.message.edit(
                    content=None,
                    embed=self.lobby_embed(),
                    attachments=[image_file],
                    view=self,
                )
            except (discord.HTTPException, OSError, ValueError):
                logger.warning("Could not refresh lobby message for %s", self.game_name, exc_info=True)
                try:
                    await self.message.edit(embed=self.lobby_embed(), view=self)
                except discord.HTTPException:
                    logger.warning("Text-only lobby fallback failed for %s", self.game_name, exc_info=True)
