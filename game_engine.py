"""Reusable visual lobby and Pillow rendering utilities for PRIME multiplayer games.

The module intentionally stays separate from PRIME's durable two-player game
engine: lobby membership and real-time round state belong to each live game.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable, Sequence

import aiohttp
import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, features

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


@lru_cache(maxsize=64)
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
    """Draw Arabic and mixed-direction text using Pillow's shaping engine when available.

    Prefer libraqm's native shaping/bidi support because applying the legacy
    reshape+reorder path to mixed Arabic, Latin, numbers and emoji can scramble
    their visual order. Keep the legacy path as a compatibility fallback.
    """
    raw_text = _safe_text(text)
    text_font = font or _font(22)
    options = {
        "font": text_font,
        "fill": fill,
        "stroke_width": max(0, int(stroke_width)),
    }
    if anchor:
        options["anchor"] = anchor
    if stroke_fill is not None:
        options["stroke_fill"] = stroke_fill

    has_arabic = any("\u0600" <= char <= "\u06ff" or "\ufb50" <= char <= "\ufdff"
                     or "\ufe70" <= char <= "\ufeff" for char in raw_text)
    if has_arabic:
        try:
            if features.check("raqm"):
                # Let libraqm handle shaping and bidi exactly once. Do not pre-reorder.
                draw.text(xy, raw_text, direction=direction or "rtl", language="ar", **options)
                return
        except (AttributeError, TypeError, ValueError, OSError):
            logger.debug("Native Arabic rendering unavailable; trying compatibility path", exc_info=True)

    shaped = _shape_arabic(raw_text)
    try:
        draw.text(xy, shaped, **options)
    except (TypeError, ValueError, OSError):
        logger.debug("Pillow rejected styled text options; using basic text", exc_info=True)
        draw.text(xy, shaped, font=text_font, fill=fill)

def fit_text(
    value: object,
    max_width: int,
    *,
    font_size: int = 22,
    min_font_size: int = 12,
    bold: bool = False,
) -> tuple[str, ImageFont.ImageFont]:
    """Return sanitized text and the largest cached font that fits a pixel width.

    The helper is useful for untrusted Discord display names, where a single
    very long name must not spill into neighboring cards.
    """
    text = _safe_text(value)
    max_width = max(1, int(max_width))
    min_size = max(8, int(min_font_size))
    size = max(min_size, int(font_size))
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    while size > min_size:
        font = _font(size, bold)
        try:
            if probe.textlength(text, font=font) <= max_width:
                return text, font
        except (TypeError, ValueError, OSError):
            break
        size -= 1
    font = _font(min_size, bold)
    try:
        if probe.textlength(text, font=font) <= max_width:
            return text, font
    except (TypeError, ValueError, OSError):
        return text[: max(1, max_width // max(1, min_size // 2))], font

    # Truncate by characters until the ellipsis also fits. Pillow's native
    # RAQM path measures the original logical string, matching draw_arabic_text.
    ellipsis = "…"
    candidate = text
    while candidate:
        candidate = candidate[:-1]
        shortened = candidate + ellipsis
        try:
            if probe.textlength(shortened, font=font) <= max_width:
                return shortened, font
        except (TypeError, ValueError, OSError):
            break
    return ellipsis, font


def draw_progress_bar(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    value: float,
    *,
    maximum: float = 100.0,
    track: str = PANEL_EDGE,
    fill: str = CYAN,
    radius: int = 8,
    outline: str | None = None,
) -> None:
    """Draw a clamped, rounded progress bar for timers, scores or lobby capacity."""
    x1, y1, x2, y2 = (int(part) for part in xy)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("progress_bar_bounds_must_have_positive_area")
    maximum = float(maximum)
    if maximum <= 0:
        raise ValueError("progress_bar_maximum_must_be_positive")
    ratio = max(0.0, min(1.0, float(value) / maximum))
    radius = max(0, min(int(radius), (x2 - x1) // 2, (y2 - y1) // 2))
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=track, outline=outline)
    inset = 2
    inner_x1, inner_y1 = x1 + inset, y1 + inset
    inner_x2, inner_y2 = x2 - inset, y2 - inset
    usable_width = max(0, inner_x2 - inner_x1)
    filled_width = int(usable_width * ratio)
    if filled_width <= 0 or inner_y2 <= inner_y1:
        return
    draw.rounded_rectangle(
        (inner_x1, inner_y1, min(inner_x2, inner_x1 + filled_width), inner_y2),
        radius=max(0, radius - inset),
        fill=fill,
    )


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


def create_luxury_canvas(
    width: int = 1240,
    height: int = 760,
    title: str = "PRIME GAMES",
    subtitle: str = "",
    *,
    accent: str = PURPLE,
) -> Image.Image:
    """Create a premium dark canvas with radial ambient light and soft neon trim."""
    width = max(320, min(int(width), 2400))
    height = max(240, min(int(height), 1600))
    base = Image.new("RGB", (width, height), BACKGROUND)
    pixels = base.load()
    # A low-cost two-axis gradient; deliberately subtle so foreground text stays legible.
    for y in range(height):
        vertical = y / max(1, height - 1)
        for x in range(width):
            nx = (x - width * 0.68) / max(1, width * 0.78)
            ny = (y - height * 0.16) / max(1, height * 0.88)
            glow = max(0.0, 1.0 - (nx * nx + ny * ny) ** 0.5) ** 2
            edge = max(0.0, 1.0 - abs(x - width / 2) / (width * 0.7))
            pixels[x, y] = (
                min(255, int(8 + 7 * (1 - vertical) + int(accent[1:3], 16) * glow * 0.10)),
                min(255, int(10 + 3 * (1 - vertical) + int(accent[3:5], 16) * glow * 0.07)),
                min(255, int(22 + 15 * (1 - vertical) + int(accent[5:7], 16) * glow * 0.12)),
            )
    image = base.convert("RGBA")
    # Soft ambient halo behind the main frame.
    halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    hd.ellipse((width * 0.18, -height * 0.45, width * 0.88, height * 0.72),
               fill=(*tuple(int(accent[i:i+2], 16) for i in (1, 3, 5)), 24))
    halo = halo.filter(ImageFilter.GaussianBlur(max(18, width // 30)))
    image.alpha_composite(halo)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((18, 18, width - 18, height - 18), radius=30,
                           fill=(12, 15, 31, 220), outline=(61, 54, 96, 255), width=2)
    draw.line((52, 25, width * 0.34, 25), fill=accent, width=4)
    draw.line((width * 0.66, height - 25, width - 52, height - 25), fill=CYAN, width=3)
    draw_arabic_text(draw, (width // 2, 52), _safe_text(title, 90),
                     font=_font(max(24, min(42, width // 25)), True), fill=WHITE, anchor="ma")
    if subtitle:
        draw_arabic_text(draw, (width // 2, 96), _safe_text(subtitle, 180),
                         font=_font(max(14, min(21, width // 55))), fill=MUTED, anchor="ma")
    draw.line((width // 2 - 72, 126, width // 2 + 72, 126), fill=accent, width=3)
    return image


def draw_glow_panel(
    image: Image.Image,
    xy: tuple[int, int, int, int],
    *,
    fill: tuple[int, int, int, int] = (15, 19, 39, 238),
    outline: str = PANEL_EDGE,
    radius: int = 20,
    glow: str | None = None,
    width: int = 2,
) -> None:
    """Draw a rounded panel with a soft shadow and optional neon halo."""
    x1, y1, x2, y2 = (int(value) for value in xy)
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((x1 + 3, y1 + 7, x2 + 3, y2 + 7), radius=radius,
                         fill=(0, 0, 0, 150))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)))
    if glow:
        halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ImageDraw.Draw(halo).rounded_rectangle((x1, y1, x2, y2), radius=radius,
                                               outline=glow, width=max(3, width + 2))
        image.alpha_composite(halo.filter(ImageFilter.GaussianBlur(11)))
    ImageDraw.Draw(image).rounded_rectangle((x1, y1, x2, y2), radius=radius,
                                             fill=fill, outline=glow or outline, width=width)


def draw_radial_glow(
    image: Image.Image,
    center: tuple[int, int],
    radius: int,
    *,
    color: str = "#ffca60",
    max_alpha: int = 96,
    blur: int = 20,
) -> None:
    """Composite a smooth radial glow for premium game panels and winner portraits."""
    radius = max(1, min(int(radius), max(image.size)))
    scale = 3
    layer = Image.new("RGBA", (radius * 2 // scale + 2, radius * 2 // scale + 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    cx = cy = layer.width // 2
    for r in range(max(1, radius // scale), 0, -1):
        alpha = int(max_alpha * (1 - r / max(1, radius // scale)) ** 2)
        if alpha:
            draw.ellipse((cx-r, cy-r, cx+r, cy+r), fill=(*rgb, alpha))
    layer = layer.resize((radius * 2, radius * 2), Image.Resampling.BICUBIC)
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(max(1, int(blur))))
    x = int(center[0] - radius)
    y = int(center[1] - radius)
    image.alpha_composite(layer, (x, y))


def draw_status_avatar(
    image: Image.Image,
    avatar: Image.Image,
    xy: tuple[int, int],
    size: int,
    *,
    status: str = "alive",
) -> None:
    """Composite an antialiased circular avatar with state-coded neon ring."""
    x, y = int(xy[0]), int(xy[1])
    colors = {
        "alive": GREEN,
        "shield": CYAN,
        "target": "#ffd166",
        "eliminated": "#737b91",
        "winner": "#ffca60",
    }
    ring = colors.get(status, PURPLE)
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    avatar = ImageOps.fit(avatar.convert("RGBA"), (size, size), method=resampling)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    avatar.putalpha(mask)
    halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((x - 5, y - 5, x + size + 5, y + size + 5),
                                 outline=ring, width=max(3, size // 20))
    image.alpha_composite(halo.filter(ImageFilter.GaussianBlur(max(4, size // 10))))
    image.alpha_composite(avatar, (x, y))
    ImageDraw.Draw(image).ellipse((x, y, x + size - 1, y + size - 1),
                                  outline=ring, width=max(2, size // 24))


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
            async with self._state_lock:
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
        else:
            # Update the original lobby so its buttons visibly become disabled
            # after the game starts; otherwise the stale message looks usable.
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

    def render_lobby_image(
        self,
        players: Sequence[discord.Member] | None = None,
    ) -> Image.Image:
        """Render a stable snapshot of the lobby; avatars are added asynchronously."""
        player_snapshot = list(self.players.values()) if players is None else list(players)
        width, height = 1100, 360 + ((self.max_players + 3) // 4) * 118
        image = create_base_canvas(width, height, self.game_name, "LOBBY • انتظر اكتمال اللاعبين")
        draw = ImageDraw.Draw(image)
        draw_arabic_text(draw, (width - 52, 148), f"اللاعبون: {len(player_snapshot)} / {self.max_players}",
                         font=_font(22, True), fill=CYAN, anchor="ra")
        columns = 4
        for index, player in enumerate(player_snapshot):
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
        """Render a stable lobby snapshot and fetch avatars concurrently."""
        async with self._state_lock:
            players = list(self.players.values())

        image = self.render_lobby_image(players).convert("RGBA")
        owns_session = self.avatar_session is None
        client = self.avatar_session
        if client is None:
            client = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=4.0)
            )

        try:
            avatar_results = await asyncio.gather(
                *(
                    get_circular_avatar(
                        player,
                        60,
                        session=client,
                        timeout=4.0,
                        border_color=CYAN if player.id == self.host.id else PURPLE,
                    )
                    for player in players
                ),
                return_exceptions=True,
            )
        finally:
            if owns_session and client is not None and not client.closed:
                await client.close()

        for index, result in enumerate(avatar_results):
            if isinstance(result, Exception):
                logger.debug("Avatar rendering failed for lobby player %s", players[index].id, exc_info=result)
                avatar = _frame_avatar(
                    _placeholder_avatar(60, getattr(players[index], "display_name", "?")),
                    60,
                    CYAN if players[index].id == self.host.id else PURPLE,
                )
            else:
                avatar = result
            column, row = index % 4, index // 4
            x, y = 60 + column * 260, 176 + row * 118
            image.alpha_composite(avatar, (x + 14, y + 18))
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
