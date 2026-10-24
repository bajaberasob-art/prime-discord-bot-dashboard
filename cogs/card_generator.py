"""Phase 5 memory-only rank cards. Import and await; this is not a Discord cog.

text_xp is cumulative database XP. next_level_xp is the XP cost of advancing
from text_level, not a cumulative threshold. Optional statistics are supplied
in settings: total_messages, total_voice_seconds, current_streak.
"""
import asyncio
import io
import math
import random
import unicodedata
import weakref
from pathlib import Path

import arabic_reshaper
import discord
from bidi.algorithm import get_display
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from cogs.card_images import MAX_ANIMATED_FRAMES, fetch_image
from level_progression import total_xp_for_level, xp_required

LAYOUTS = {"vertical": (560, 900), "stats": (1000, 420), "minimal": (900, 230),
           "ring": (620, 680), "classic": (1000, 340), "banner": (1000, 300),
           "square": (640, 640), "spotlight": (1000, 500), "showcase": (1100, 460)}
PARTICLES = {"none", "sparks", "shine", "embers", "snow", "petals", "neon"}
CARD_VISUAL_STYLES = {"cyberpunk", "cosmic", "royal", "frost", "emerald", "obsidian", "inferno", "classic"}
CARD_DESIGN_DEFAULTS = {
    "glowStrength": 54, "particleDensity": 46, "particleColor": "accent",
    "barStyle": "gradient", "frame": "auto", "bgOverlay": 28, "bgBlur": 4,
    "animationEnabled": True, "animationStyle": "beam", "animationIntensity": 62,
    "visualStyle": "cyberpunk",
    "evolutionEnabled": True,
    "stats": {"messages": True, "voice": True, "streak": True, "serverRank": True},
}
FONT_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_gates = weakref.WeakKeyDictionary()
SCALE = 2
WHITE = "#f8f5fc"
MUTED = "#aaa5ba"


def number(value, default=0):
    try:
        value = int(value)
        return max(0, min(value, 10**18))
    except (ValueError, TypeError, OverflowError):
        return default


def calculate_progress(text_level, text_xp, next_level_xp):
    """Return (XP within level, cost to next level, clamped fraction)."""
    level = number(text_level)
    required = number(next_level_xp)
    earned = max(0, number(text_xp) - total_xp_for_level(level))
    return earned, required, min(1.0, max(0.0, earned / required)) if required else 0.0


LEVEL_EVOLUTION_STAGES = (
    {"minimum": 0, "key": "bronze", "name": "Initiate", "color": "#c88754", "rank": 1},
    {"minimum": 10, "key": "silver", "name": "Veteran", "color": "#d3d9e6", "rank": 2},
    {"minimum": 25, "key": "gold", "name": "Elite", "color": "#ffd166", "rank": 3},
    {"minimum": 50, "key": "diamond", "name": "Ascendant", "color": "#77e4f2", "rank": 4},
    {"minimum": 100, "key": "mythic", "name": "Mythic", "color": "#c084fc", "rank": 5},
)


def level_evolution_stage(level):
    """Resolve the earned visual evolution tier from the member's actual level."""
    level = number(level)
    stage = LEVEL_EVOLUTION_STAGES[0]
    for candidate in LEVEL_EVOLUTION_STAGES[1:]:
        if level < candidate["minimum"]:
            break
        stage = candidate
    return dict(stage)


def display_text(value):
    text = "".join(c for c in str(value)[:160]
                   if unicodedata.category(c) not in {"Cc", "Cf", "Cs"})
    # BASIC font layout + explicit shaping is portable to Pillow builds without RAQM.
    return get_display(arabic_reshaper.reshape(text))


def compact(value):
    value = number(value)
    for limit, suffix in ((10**12, "T"), (10**9, "B"), (10**6, "M")):
        if value >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{value:,}"


class Card:
    def __init__(self, size, color, background, mode, design=None, background_frame=0):
        self.w, self.h = size
        self.accent = color
        self.design = design or {}
        self.image = Image.new("RGB", (self.w * SCALE, self.h * SCALE), "#07070b")
        if background:
            with Image.open(io.BytesIO(background)) as source:
                if getattr(source, "format", None) == "GIF":
                    source.seek(min(max(0, int(background_frame)), source.n_frames - 1))
                fitted = ImageOps.fit(source.convert("RGB"), self.image.size, method=Image.Resampling.LANCZOS)
                blur = number(self.design.get("bgBlur"), 4)
                if blur:
                    fitted = fitted.filter(ImageFilter.GaussianBlur(blur * SCALE))
                self.image = Image.blend(self.image, fitted, 0.54)
                overlay = number(self.design.get("bgOverlay"), 28)
                if overlay:
                    self.image = Image.blend(
                        self.image, Image.new("RGB", self.image.size, "#000000"),
                        min(0.85, overlay / 100),
                    )
        glow = Image.new("RGB", (self.w // 4, self.h // 4), "#000000")
        gd = ImageDraw.Draw(glow)
        gd.ellipse((-40, -50, self.w // 4, self.h // 6), fill=color)
        gd.ellipse((self.w // 8, self.h // 7, self.w // 3, self.h // 3), fill="#554bcd")
        strength = number(self.design.get("glowStrength"), 54)
        glow = glow.filter(ImageFilter.GaussianBlur(max(2, int(35 * (0.25 + strength / 100)))))
        glow = glow.resize(self.image.size, Image.Resampling.BILINEAR)
        self.image = Image.blend(self.image, glow, 0.18 * strength / 100)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self._draw_ambient_lighting()
        self.panel((12, 12, self.w - 12, self.h - 12), radius=28, fill=None)
        self.particles(mode)
        self._draw_visual_motif()

    def _draw_ambient_lighting(self):
        """Give every template a layered backdrop without hiding profile photos."""
        width, height = self.image.size
        accent = ImageColor.getrgb(self.accent)[:3]
        bloom = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glow = ImageDraw.Draw(bloom)
        glow.ellipse(
            (-int(width * .28), -int(height * .62), int(width * .52), int(height * .72)),
            fill=(*accent, 42),
        )
        glow.ellipse(
            (int(width * .54), int(height * .36), int(width * 1.28), int(height * 1.35)),
            fill=(87, 103, 246, 34),
        )
        bloom = bloom.filter(ImageFilter.GaussianBlur(max(24, SCALE * 40)))
        self.image = Image.alpha_composite(self.image.convert("RGBA"), bloom).convert("RGB")

        # Near-invisible texture avoids flat, muddy black areas on OLED screens.
        texture = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        td = ImageDraw.Draw(texture)
        for x in range(32 * SCALE, width, 64 * SCALE):
            td.line((x, 0, x, height), fill=(230, 240, 255, 4), width=SCALE)
        for y in range(32 * SCALE, height, 64 * SCALE):
            td.line((0, y, width, y), fill=(230, 240, 255, 4), width=SCALE)
        self.image = Image.alpha_composite(self.image.convert("RGBA"), texture).convert("RGB")
        self.draw = ImageDraw.Draw(self.image)

    def _draw_visual_motif(self):
        """Theme-level card art: circuit traces, orbits, frames and bevels."""
        style = self.design.get("visualStyle", "cyberpunk")
        if not isinstance(style, str) or style not in CARD_VISUAL_STYLES:
            style = "cyberpunk"
        if style == "classic":
            return

        w, h = self.w, self.h
        accent = ImageColor.getrgb(self.accent)
        overlay = Image.new("RGBA", self.image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        def point(x, y):
            return int(x * SCALE), int(y * SCALE)

        def trace(points, alpha=32, weight=1):
            draw.line([point(x, y) for x, y in points],
                      fill=(*accent, alpha), width=max(1, weight * SCALE), joint="curve")

        def tint(points, alpha=14):
            draw.polygon([point(x, y) for x, y in points], fill=(*accent, alpha))

        if style == "cyberpunk":
            tint([(0, 0), (w * .28, 0), (w * .23, 8), (12, 8)], 22)
            tint([(w, h), (w * .72, h), (w * .77, h - 8), (w - 12, h - 8)], 22)
            for x in range(18, max(19, w - 18), 44):
                trace([(x, 14), (x + 7, 14), (x + 7, 22)], 24)
                trace([(x, h - 22), (x + 7, h - 22), (x + 7, h - 14)], 18)
            trace([(14, h * .19), (24, h * .19), (36, h * .19 + 10),
                   (36, h * .19 + 24)], 70, 2)
            trace([(w - 14, h * .72), (w - 25, h * .72),
                   (w - 36, h * .72 - 10), (w - 36, h * .72 - 24)], 58, 2)
        elif style == "cosmic":
            cx, cy = w * .84, h * .18
            for rx, ry, alpha in ((h * .10, h * .07, 42), (h * .14, h * .035, 24)):
                draw.ellipse((int((cx-rx)*SCALE), int((cy-ry)*SCALE),
                              int((cx+rx)*SCALE), int((cy+ry)*SCALE)),
                             outline=(*accent, alpha), width=1*SCALE)
            trace([(cx - 16, cy), (cx + 16, cy)], 40)
            rng = random.Random(731)
            for _ in range(max(12, min(28, w // 28))):
                x = rng.choice([rng.uniform(18, w * .17), rng.uniform(w * .83, w - 18)])
                y = rng.uniform(20, h - 20)
                draw.ellipse((int(x*SCALE), int(y*SCALE), int((x+2)*SCALE),
                              int((y+2)*SCALE)), fill=(*accent, rng.randint(25, 62)))
        elif style == "royal":
            draw.rounded_rectangle((18*SCALE, 18*SCALE, (w-18)*SCALE, (h-18)*SCALE),
                                   radius=24*SCALE, outline=(*accent, 52), width=2*SCALE)
            draw.rounded_rectangle((25*SCALE, 25*SCALE, (w-25)*SCALE, (h-25)*SCALE),
                                   radius=20*SCALE, outline=(*accent, 24), width=1*SCALE)
            for x, y, direction in ((21, 21, 1), (w-21, 21, -1),
                                    (21, h-21, 1), (w-21, h-21, -1)):
                tint([(x, y-5), (x+5*direction, y), (x, y+5),
                      (x-5*direction, y)], 70)
        elif style == "frost":
            for side in (0, 1):
                x = 18 if side == 0 else w - 18
                direction = 1 if side == 0 else -1
                for y in range(34, max(35, h - 24), 68):
                    trace([(x, y-12), (x+direction*7, y-4), (x+direction*7, y+5),
                           (x, y+13), (x-direction*7, y+5), (x-direction*7, y-4),
                           (x, y-12)], 34)
            tint([(0, 0), (w*.18, 0), (0, h*.12)], 12)
            tint([(w, h), (w*.82, h), (w, h*.88)], 12)
        elif style == "emerald":
            for side in (0, 1):
                x = 18 if side == 0 else w - 18
                direction = 1 if side == 0 else -1
                for y in range(42, max(43, h - 30), 82):
                    trace([(x, y), (x+direction*18, y), (x+direction*18, y+14),
                           (x+direction*30, y+14)], 42)
        elif style == "obsidian":
            neutral = (190, 198, 214, 42)
            trace([(14, 44), (14, 14), (w*.22, 14)], 36)
            trace([(w-14, h-44), (w-14, h-14), (w*.78, h-14)], 36)
            draw.polygon([point(0, 0), point(34, 0), point(0, 34)], fill=neutral)
            draw.polygon([point(w, h), point(w-34, h), point(w, h-34)], fill=neutral)
        elif style == "inferno":
            tint([(0, h*.25), (w*.15, h*.08), (w*.12, h*.17), (0, h*.32)], 24)
            tint([(w, h*.67), (w*.85, h*.84), (w*.88, h*.75), (w, h*.58)], 24)
            for offset in range(4):
                trace([(8 + offset*5, h*.35 + offset*9),
                       (30 + offset*5, h*.23 + offset*9)], 24 + offset*7)

        self.image = Image.alpha_composite(self.image.convert("RGBA"), overlay).convert("RGB")
        self.draw = ImageDraw.Draw(self.image)

    def font(self, size, bold=False, emoji=False):
        key = (size, bold, emoji)
        if key not in self.fonts:
            path = FONT_DIR / ("NotoEmoji.ttf" if emoji else
                               ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"))
            try:
                self.fonts[key] = ImageFont.truetype(
                    str(path), size * SCALE, layout_engine=ImageFont.Layout.BASIC)
            except OSError:
                self.fonts[key] = ImageFont.load_default(size=size * SCALE)
        return self.fonts[key]

    def text_runs(self, text, size, bold):
        runs = []
        for char in text:
            emoji = ord(char) >= 0x1F000 or 0x2600 <= ord(char) <= 0x27BF
            if runs and runs[-1][0] == emoji:
                runs[-1][1] += char
            else:
                runs.append([emoji, char])
        return [(part, self.font(size, bold, emoji)) for emoji, part in runs]

    def text(self, xy, value, size=18, color=WHITE, bold=False, width=None, center=False):
        # Microtype was nearly unreadable after wide cards were scaled down on phones.
        # Boost labels while preserving the geometry and headline sizes of every layout.
        if size <= 14:
            size = max(size, int(round(size * 1.24)))
        elif size <= 18:
            size = int(round(size * 1.12))
        text = display_text(value)
        def measure(content):
            return sum(self.draw.textlength(part, font=font)
                       for part, font in self.text_runs(content, size, bold))
        if width:
            while text and measure(text) > width * SCALE:
                text = text[:-2] + "…" if len(text) > 2 else ""
        x, y = xy
        if center:
            x -= measure(text) / (2 * SCALE)
        for part, font in self.text_runs(text, size, bold):
            # A tiny dark offset keeps labels crisp over gradients and uploaded photos.
            self.draw.text((x * SCALE, y * SCALE + SCALE), part, font=font, fill="#050711", stroke_width=0)
            self.draw.text((x * SCALE, y * SCALE), part, font=font, fill=color, stroke_width=0)
            x += self.draw.textlength(part, font=font) / SCALE

    def panel(self, box, radius=18, fill="#12121c", outline="#35323f"):
        self.draw.rounded_rectangle(tuple(int(v * SCALE) for v in box), radius=radius * SCALE,
                                    fill=fill, outline=outline, width=SCALE)

    def line(self, box, color="#302d3c", width=1):
        self.draw.line(tuple(v * SCALE for v in box), fill=color, width=width * SCALE)

    def particles(self, mode):
        if not isinstance(mode, str) or mode not in PARTICLES or mode == "none":
            return
        rng = random.Random(715)
        # Sparse backdrop effects stay behind the avatar and readable panels.
        density = number(self.design.get("particleDensity"), 46)
        if density <= 0:
            return
        color = self.design.get("particleColor", "accent")
        if color != "accent":
            try:
                color = "#%02x%02x%02x" % ImageColor.getrgb(str(color))[:3]
            except (TypeError, ValueError):
                color = self.accent
        for _ in range(max(1, int(min(72, max(8, self.w*self.h//20000)) * density / 46))):
            x = rng.choice([rng.randint(20, 28), rng.randint(self.w-28, self.w-20)])
            y = rng.randint(22, self.h - 22)
            r = rng.choice([1, 2, 3])
            particle_color = color if self.design.get("particleColor", "accent") != "accent" else {
                "sparks": "#e6b6d2", "shine": "#cbc4f7", "embers": "#ba695d",
                "snow": "#ccd6e9", "petals": "#ac7790", "neon": self.accent,
            }[mode]
            if mode in {"shine", "neon"}:
                self.line((x - r * 2, y, x + r * 2, y), particle_color)
                self.line((x, y - r * 2, x, y + r * 2), particle_color)
            elif mode == "sparks":
                self.line((x, y, x + 3, y - 6), particle_color)
            else:
                self.draw.ellipse(((x-r)*SCALE, (y-r)*SCALE, (x+r)*SCALE,
                                   (y+r*(2 if mode == "petals" else 1))*SCALE), fill=particle_color)

    def avatar(self, xy, diameter, data, progress=None):
        x, y = xy
        ring = Image.new("RGBA", self.image.size)
        rd = ImageDraw.Draw(ring)
        bounds = tuple(int(v * SCALE) for v in (x-6, y-6, x+diameter+6, y+diameter+6))
        rd.ellipse(bounds, outline=self.accent, width=5*SCALE)
        self.image.paste(ring.filter(ImageFilter.GaussianBlur(9*SCALE)), (0, 0),
                         ring.filter(ImageFilter.GaussianBlur(9*SCALE)))
        self.draw = ImageDraw.Draw(self.image)
        self.draw.ellipse(bounds, outline="#484153", width=2*SCALE)
        if progress is not None and progress > 0:
            self.draw.arc(bounds, -90, -90 + progress * 360, fill=self.accent, width=5*SCALE)
        elif progress is None:
            self.draw.ellipse(bounds, outline=self.accent, width=2*SCALE)
        evolution = self.design.get("evolution")
        if isinstance(evolution, dict):
            evolution_color = evolution.get("color", self.accent)
            outer = tuple(int(v * SCALE) for v in (x-12, y-12, x+diameter+12, y+diameter+12))
            self.draw.arc(outer, -88, 268, fill=evolution_color, width=2*SCALE)
            notch_count = max(1, min(5, number(evolution.get("rank"), 1)))
            center_x, center_y = (x + diameter / 2) * SCALE, (y + diameter / 2) * SCALE
            radius = (diameter / 2 + 12) * SCALE
            for notch in range(notch_count):
                angle = math.radians(-90 + notch * 360 / notch_count)
                px = center_x + math.cos(angle) * radius
                py = center_y + math.sin(angle) * radius
                self.draw.ellipse((int(px-3*SCALE), int(py-3*SCALE),
                                   int(px+3*SCALE), int(py+3*SCALE)), fill=evolution_color)
        size = (diameter*SCALE, diameter*SCALE)
        if data:
            with Image.open(io.BytesIO(data)) as source:
                avatar = ImageOps.fit(source.convert("RGB"), size, method=Image.Resampling.LANCZOS)
            # Discord avatars can arrive compressed; restore local contrast and detail after cropping.
            avatar = ImageEnhance.Color(avatar).enhance(1.12)
            avatar = ImageEnhance.Contrast(avatar).enhance(1.08)
            avatar = avatar.filter(ImageFilter.UnsharpMask(radius=1.5 * SCALE, percent=145, threshold=3))
        else:
            avatar = Image.new("RGB", size, "#292137")
            ad = ImageDraw.Draw(avatar)
            d = diameter*SCALE
            ad.ellipse((d*.35, d*.2, d*.65, d*.5), fill="#b8a4cc")
            ad.ellipse((d*.2, d*.55, d*.8, d*1.13), fill="#b8a4cc")
        mask = Image.new("L", size)
        ImageDraw.Draw(mask).ellipse((0, 0, size[0]-1, size[1]-1), fill=255)
        self.image.paste(avatar, (x*SCALE, y*SCALE), mask)
        self.draw = ImageDraw.Draw(self.image)
        # A crisp inner rim separates the portrait from dark or photo-based backgrounds.
        self.draw.ellipse(
            (x*SCALE + SCALE, y*SCALE + SCALE,
             (x + diameter)*SCALE - SCALE, (y + diameter)*SCALE - SCALE),
            outline="#d8e5f8", width=max(1, SCALE),
        )

    def bar(self, box, progress, animated=False, style="gradient", direction="ltr"):
        x, y, w, h = box
        rtl = direction == "rtl"
        self.panel((x, y, x+w, y+h), radius=h//2, fill="#23212e")
        fill_width = max(0, int(w*progress))
        fill_left = w - fill_width if rtl else 0
        if fill_width:
            # Clip the fill to a rounded mask; never draw a false minimum progress.
            mask = Image.new("L", (w*SCALE, h*SCALE))
            md = ImageDraw.Draw(mask)
            md.rounded_rectangle((fill_left*SCALE, 0,
                                  (fill_left+fill_width)*SCALE-1, h*SCALE-1),
                                 radius=min(h, fill_width)*SCALE//2, fill=255)
            gradient = Image.new("RGB", (w*SCALE, h*SCALE))
            gd = ImageDraw.Draw(gradient)
            start = ImageColor.getrgb(self.accent)
            for column in range(w*SCALE):
                t = column / max(1, w*SCALE-1)
                if rtl:
                    t = 1 - t
                if style == "solid":
                    color = start
                else:
                    color = tuple(int(c*(1-t*.35)+245*t*.35) for c in start)
                gd.line((column, 0, column, h*SCALE), fill=color)
            if animated:
                # Static PNG equivalent: narrow specular sheen, never GIF/APNG.
                gd.polygon([(fill_width*SCALE*.6, 0), (fill_width*SCALE*.67, 0),
                            (fill_width*SCALE*.54, h*SCALE), (fill_width*SCALE*.47, h*SCALE)],
                           fill="#fff0fb")
            self.image.paste(gradient, (x*SCALE, y*SCALE), mask)
            self.draw = ImageDraw.Draw(self.image)
            if style == "segmented":
                segment = max(12, int(w / 12)) * SCALE
                for xpos in range(segment, fill_width * SCALE, segment):
                    position = fill_width*SCALE - xpos if rtl else xpos
                    position += fill_left*SCALE
                    self.draw.line((x*SCALE+position, y*SCALE+1,
                                    x*SCALE+position, (y+h)*SCALE-1),
                                   fill="#11111a", width=2*SCALE)
            elif style == "neon":
                halo = Image.new("RGBA", self.image.size)
                ImageDraw.Draw(halo).rounded_rectangle(
                    ((x+fill_left)*SCALE, y*SCALE,
                     (x+fill_left+fill_width)*SCALE, (y+h)*SCALE),
                    radius=h*SCALE//2, outline=self.accent, width=5*SCALE,
                )
                halo = halo.filter(ImageFilter.GaussianBlur(6*SCALE))
                self.image = Image.alpha_composite(self.image.convert("RGBA"), halo).convert("RGB")
                self.draw = ImageDraw.Draw(self.image)

    def stat(self, box, label, value):
        self.panel(box)
        x, y, right, _ = box
        self.text((x+18, y+13), label.upper(), 11, MUTED, width=right-x-30)
        self.text((x+18, y+35), value, 22, bold=True, width=right-x-30)

    def compact_stat(self, box, label, value):
        self.panel(box, radius=12, fill="#111522", outline="#273044")
        x, y, right, _ = box
        width = right - x - 18
        self.text((x+9, y+7), label.upper(), 8, MUTED, width=width)
        self.text((x+9, y+27), value, 13, WHITE, True, width=width)


def _render(
    name, handle, level, xp, required, rank, total, settings, avatar, background,
    background_frame=0,
):
    layout = settings.get("card_layout", "vertical")
    if not isinstance(layout, str) or layout not in LAYOUTS:
        layout = "vertical"
    raw_design = settings.get("card_design")
    design = {**CARD_DESIGN_DEFAULTS, **(raw_design if isinstance(raw_design, dict) else {})}
    design["stats"] = {**CARD_DESIGN_DEFAULTS["stats"],
                       **(design.get("stats") if isinstance(design.get("stats"), dict) else {})}
    try:
        rgb = ImageColor.getrgb(str(settings.get("card_color", "#f2aacb")))
        color = "#%02x%02x%02x" % rgb[:3]
    except (ValueError, TypeError):
        color = "#f2aacb"
    mode = settings.get("card_particles", "none")
    card = Card(LAYOUTS[layout], color, background, mode, design, background_frame)
    earned, cost, progress = calculate_progress(level, xp, required)
    rank_text = f"#{compact(rank)}" if rank else "—"
    xp_text = f"{compact(earned)} / {compact(cost)} XP"
    animated = settings.get("card_animated_bar") in (True, 1, "1", "true")
    show_stats = settings.get("card_show_stats", True) not in (False, 0, "0", "false")
    show_stat = lambda key: show_stats and design["stats"].get(key, True) is not False
    bar_style = design.get("barStyle", "gradient")
    frame = design.get("frame", "auto")
    auto_frame = frame == "auto"
    evolution = level_evolution_stage(level) if design.get("evolutionEnabled", True) is not False else None
    design["evolution"] = evolution
    if frame == "auto":
        frame = (
            "bronze" if number(level) < 10 else
            "silver" if number(level) < 25 else
            "gold" if number(level) < 50 else
            "diamond" if number(level) < 100 else "mythic"
        )
    voice = settings.get("total_voice_seconds")
    stats = [
        ("Messages", compact(settings["total_messages"]) if settings.get("total_messages") is not None else "—"),
        ("Voice time", f"{number(voice)//3600}h {(number(voice)%3600)//60}m" if voice is not None else "—"),
        ("Streak", f"{compact(settings['current_streak'])} days" if settings.get("current_streak") is not None else "—"),
    ]
    if layout == "vertical":
        card.text((36, 33), "PRIME / LEVELS", 13, color, True)
        if show_stat("serverRank"):
            card.panel((424, 27, 524, 69), radius=15)
            card.text((474, 35), rank_text, 20, bold=True, center=True, width=85)
        card.avatar((158, 104), 244, avatar, progress)
        card.text((280, 382), name, 30, bold=True, width=475, center=True)
        card.text((280, 422), handle, 15, MUTED, width=465, center=True)
        card.panel((32, 467, 528, 635), radius=24)
        card.text((56, 488), "LEVEL", 12, MUTED)
        card.text((55, 510), compact(level), 48, bold=True)
        card.text((468, 531), f"{progress:.0%}", 22, color, True, center=True)
        card.bar((56, 579, 448, 12), progress, animated, bar_style)
        card.text((56, 603), xp_text, 14, MUTED, width=445)
        if show_stat("messages"): card.stat((32, 659, 270, 749), *stats[0])
        if show_stat("voice"): card.stat((290, 659, 528, 749), *stats[1])
        if show_stat("streak"): card.stat((32, 765, 270, 855), *stats[2])
        if show_stat("serverRank"): card.stat((290, 765, 528, 855), "Server rank", f"{rank_text} / {compact(total)}")
        card.text((280, 873), "GROW AT YOUR OWN PACE", 9, MUTED, center=True)
    elif layout == "stats":
        card.panel((26, 54, 254, 286), radius=28, fill="#101827", outline="#3b4d68")
        card.text((34, 30), "PRIME / MEMBER STATISTICS", 12, color, True)
        card.avatar((38, 78), 206, avatar, progress)
        card.text((276, 75), name, 32, bold=True, width=470)
        card.text((278, 122), handle, 15, MUTED, width=430)
        if show_stat("serverRank"):
            card.stat((782, 55, 963, 147), "Server rank", f"{rank_text} / {compact(total)}")
        card.text((277, 176), f"Level {compact(level)}", 27, bold=True)
        card.text((277, 219), xp_text, 17, MUTED)
        card.text((944, 219), f"{progress:.0%}", 17, color, center=True)
        card.bar((278, 252, 685, 14), progress, animated, bar_style)
        if show_stats:
            for index, pair in enumerate(stats):
                x = 34 + index*317
                if show_stat(("messages", "voice", "streak")[index]):
                    card.stat((x, 304, x+298, 394), *pair)
    elif layout == "minimal":
        card.panel((14, 18, 886, 145), radius=23, fill="#0d1625", outline="#2d4057")
        card.panel((18, 35, 154, 140), radius=21, fill="#111d2b", outline="#344b62")
        card.avatar((28, 45), 116, avatar, progress)
        card.text((166, 23), name, 25, bold=True, width=680)
        card.text((166, 56), handle, 14, MUTED, width=500)
        rank_label = f"  ·  RANK {rank_text} OF {compact(total)}" if show_stat("serverRank") else ""
        card.text((166, 80), f"LEVEL {compact(level)}{rank_label}", 14, color, True, width=650)
        card.text((166, 105), xp_text, 15, MUTED, width=550)
        card.text((846, 105), f"{progress:.0%}", 15, color, True, center=True, width=70)
        card.bar((166, 130, 650, 9), progress, animated, bar_style)
        if show_stat("messages"): card.compact_stat((24, 157, 294, 218), *stats[0])
        if show_stat("voice"): card.compact_stat((315, 157, 585, 218), *stats[1])
        if show_stat("streak"): card.compact_stat((606, 157, 876, 218), *stats[2])
    elif layout == "ring":
        card.text((310, 31), "PRIME / PROGRESSION", 12, color, True, center=True)
        card.avatar((180, 55), 260, avatar, progress)
        card.panel((246, 290, 374, 339), radius=20, outline=color)
        card.text((310, 300), f"LVL {compact(level)}", 22, bold=True, center=True, width=115)
        card.text((310, 369), name, 28, bold=True, width=530, center=True)
        card.text((310, 408), handle, 14, MUTED, center=True, width=530)
        card.text((310, 447), f"{progress:.0%}", 32, color, True, center=True)
        card.text((310, 489), xp_text, 15, MUTED, center=True, width=510)
        if show_stat("serverRank"):
            card.text((310, 531), f"RANK {rank_text}  /  {compact(total)} MEMBERS",
                      13, WHITE, True, center=True, width=540)
        if show_stat("messages"): card.compact_stat((18, 576, 205, 656), *stats[0])
        if show_stat("voice"): card.compact_stat((216, 576, 403, 656), *stats[1])
        if show_stat("streak"): card.compact_stat((414, 576, 601, 656), *stats[2])
    elif layout == "banner":
        card.panel((20, 26, 768, 268), radius=27, fill="#0d1625", outline="#2f425b")
        card.panel((775, 29, 978, 265), radius=22, fill="#0e1829", outline="#2c4058")
        card.avatar((25, 58), 180, avatar, progress)
        card.text((232, 51), name, 30, bold=True, width=520)
        card.text((234, 94), handle, 14, MUTED, width=480)
        rank_label = f"  ·  RANK {rank_text}" if show_stat("serverRank") else ""
        card.text((234, 133), f"LEVEL {compact(level)}{rank_label}", 17, color, True, width=500)
        card.text((234, 168), xp_text, 14, MUTED, width=490)
        card.bar((234, 201, 710, 13), progress, animated, bar_style)
        if show_stat("messages"): card.compact_stat((790, 43, 966, 103), *stats[0])
        if show_stat("voice"): card.compact_stat((790, 117, 966, 177), *stats[1])
        if show_stat("streak"): card.compact_stat((790, 191, 966, 251), *stats[2])
    elif layout == "square":
        card.panel((26, 25, 614, 455), radius=30, fill="#0d1422", outline="#303f56")
        card.panel((200, 49, 440, 280), radius=120, fill="#121d2d", outline="#3c516b")
        card.text((320, 31), "PRIME / LEVEL", 12, color, True, center=True)
        card.avatar((211, 58), 218, avatar, progress)
        card.text((320, 292), name, 29, bold=True, width=530, center=True)
        card.text((320, 330), handle, 15, MUTED, width=500, center=True)
        rank_label = f"  ·  RANK {rank_text}" if show_stat("serverRank") else ""
        card.text((320, 365), f"LEVEL {compact(level)}{rank_label}", 17, color, True, center=True)
        card.text((320, 399), xp_text, 15, MUTED, center=True, width=500)
        card.bar((82, 435, 476, 13), progress, animated, bar_style)
        if show_stat("messages"): card.compact_stat((32, 480, 216, 570), *stats[0])
        if show_stat("voice"): card.compact_stat((228, 480, 412, 570), *stats[1])
        if show_stat("streak"): card.compact_stat((424, 480, 608, 570), *stats[2])
    elif layout == "spotlight":
        card.panel((26, 26, 974, 474), radius=32, fill="#101019")
        card.avatar((62, 89), 260, avatar, progress)
        card.text((374, 67), "LEVEL UP YOUR GAME", 12, color, True)
        card.text((374, 107), name, 34, bold=True, width=510)
        card.text((376, 157), handle, 16, MUTED, width=450)
        card.text((376, 209), f"LEVEL {compact(level)}", 29, color, True)
        card.text((376, 254), xp_text, 16, MUTED, width=430)
        card.bar((376, 292, 493, 15), progress, animated, bar_style)
        if show_stat("serverRank"):
            card.text((895, 286), rank_text, 32, color, True, center=True, width=100)
            card.text((895, 327), "SERVER RANK", 10, MUTED, center=True, width=130)
        if show_stat("messages"): card.compact_stat((58, 384, 330, 452), *stats[0])
        if show_stat("voice"): card.compact_stat((354, 384, 626, 452), *stats[1])
        if show_stat("streak"): card.compact_stat((650, 384, 922, 452), *stats[2])
    elif layout == "showcase":
        card.panel((24, 24, 1076, 436), radius=30, fill="#10131f", outline="#303246")
        card.text((52, 39), "PRIME / MEMBER SHOWCASE", 11, color, True)
        card.text((1040, 39), "LEVEL PROFILE", 9, MUTED, True, width=180, center=True)
        card.line((52, 70, 1048, 70), "#303348")
        card.avatar((52, 92), 218, avatar, progress)
        card.text((161, 326), "PRIME MEMBER", 11, color, True, center=True, width=190)
        card.text((306, 94), name, 37, bold=True, width=460)
        card.text((308, 141), handle, 16, MUTED, width=455)
        card.panel((306, 175, 548, 255), radius=18, fill="#151827", outline="#38415d")
        card.text((325, 187), "LEVEL", 11, MUTED, True)
        card.text((325, 207), compact(level), 30, color, True, width=100)
        card.text((508, 195), f"{progress:.0%}", 19, color, True, center=True, width=68)
        card.text((508, 224), "COMPLETE", 10, MUTED, True, center=True, width=82)
        card.text((306, 273), xp_text, 15, MUTED, width=455)
        card.bar((306, 303, 450, 15), progress, animated, bar_style)
        card.text((306, 329), "PROGRESS TO NEXT LEVEL", 11, MUTED, True, width=300)
        if show_stat("serverRank"):
            card.panel((795, 83, 1048, 174), radius=18, fill="#151827", outline="#30364c")
            card.text((921, 94), "SERVER RANK", 9, MUTED, True, center=True, width=190)
            card.text((921, 114), rank_text, 29, color, True, center=True, width=205)
            card.text((921, 150), f"OF {compact(total)} MEMBERS", 9, MUTED, center=True, width=200)
        if show_stat("messages"): card.compact_stat((795, 190, 1048, 244), *stats[0])
        if show_stat("voice"): card.compact_stat((795, 256, 1048, 310), *stats[1])
        if show_stat("streak"): card.compact_stat((795, 322, 1048, 376), *stats[2])
        card.text((1040, 404), "BUILT TO PROGRESS", 8, MUTED, True, center=True, width=190)
    else:
        card.panel((30, 30, 970, 260), radius=25, fill="#101019")
        card.text((260, 53), "PRIME / RANK CARD", 11, color, True)
        card.avatar((48, 76), 185, avatar, progress)
        card.text((260, 89), name, 31, bold=True, width=490)
        card.text((261, 135), handle, 15, MUTED, width=455)
        if show_stat("serverRank"):
            card.text((934, 70), rank_text, 27, color, True, center=True, width=85)
            card.text((900, 112), f"of {compact(total)}", 12, MUTED, center=True, width=120)
        card.line((261, 174, 938, 174))
        card.text((261, 194), f"Level {compact(level)}", 23, bold=True)
        card.text((670, 199), xp_text, 16, MUTED, width=265)
        card.bar((262, 225, 675, 15), progress, animated, bar_style)
        card.text((261, 244), f"{progress:.0%} TO NEXT LEVEL", 9, MUTED)
        if show_stat("messages"): card.compact_stat((42, 270, 336, 328), *stats[0])
        if show_stat("voice"): card.compact_stat((353, 270, 647, 328), *stats[1])
        if show_stat("streak"): card.compact_stat((664, 270, 958, 328), *stats[2])
    if frame in {"bronze", "silver", "gold", "diamond", "mythic"}:
        frame_color = (
            evolution["color"] if auto_frame and evolution
            else {"bronze": "#c88754", "silver": "#d3d9e6",
                  "gold": "#ffd166", "diamond": "#77e4f2",
                  "mythic": "#c084fc"}[frame]
        )
        card.draw.rounded_rectangle(
            (8*SCALE, 8*SCALE, (card.w-8)*SCALE, (card.h-8)*SCALE),
            radius=30*SCALE, outline=frame_color, width=3*SCALE,
        )
    result = io.BytesIO()
    card.image.resize(LAYOUTS[layout], Image.Resampling.LANCZOS).save(result, format="PNG")
    result.seek(0)
    return result


def _avatar_url(user):
    try:
        asset = getattr(user, "display_avatar", None)
        if hasattr(asset, "with_size"):
            asset = asset.with_size(512).with_static_format("png")
        return str(asset.url) if asset else None
    except (AttributeError, ValueError):
        return None


async def _card_assets(user, settings):
    design = settings.get("card_design")
    design = design if isinstance(design, dict) else {}
    # A guild-scoped upload token avoids an external round trip for media
    # uploaded through the dashboard. Legacy HTTPS backgrounds remain supported.
    background_reference = (
        design.get("backgroundAsset") or settings.get("card_bg_url")
    )
    avatar, background = await asyncio.gather(
        fetch_image(_avatar_url(user)),
        fetch_image(background_reference),
    )
    return avatar, background


def _animation_gate():
    loop = asyncio.get_running_loop()
    return _gates.setdefault(loop, asyncio.Semaphore(3))


def _animated_background_info(background):
    if not background:
        return 1, [85], 0
    with Image.open(io.BytesIO(background)) as source:
        if source.format != "GIF" or getattr(source, "n_frames", 1) <= 1:
            return 1, [85], 0
        frame_count = min(source.n_frames, MAX_ANIMATED_FRAMES)
        durations = []
        for index in range(frame_count):
            source.seek(index)
            source.convert("RGB").load()
            try:
                duration = int(source.info.get("duration", 85))
            except (TypeError, ValueError):
                duration = 85
            durations.append(max(10, duration))
        return frame_count, durations, source.info.get("loop", 0)


async def has_animated_background(settings):
    """Return whether the configured background resolves to a bounded animated GIF."""
    settings = settings if isinstance(settings, dict) else {}
    design = settings.get("card_design")
    design = design if isinstance(design, dict) else {}
    background_reference = (
        design.get("backgroundAsset") or settings.get("card_bg_url")
    )
    background = await fetch_image(background_reference)
    try:
        return _animated_background_info(background)[0] > 1
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


async def should_use_animated_card(settings):
    """Rank cards animate for configured light effects or an animated background."""
    settings = settings if isinstance(settings, dict) else {}
    design = settings.get("card_design")
    design = design if isinstance(design, dict) else {}
    intensity = design.get("animationIntensity", 62)
    try:
        has_motion = float(intensity) > 0
    except (TypeError, ValueError):
        has_motion = True
    animation_enabled = design.get("animationEnabled", True) not in (
        False, 0, "0", "false",
    )
    return (animation_enabled and has_motion) or await has_animated_background(settings)


def _render_xo_game_card(
    player_one: str,
    player_two: str,
    board: list[str | None],
    current_player_name: str,
    *,
    game_id: int,
    winner_name: str | None = None,
    is_draw: bool = False,
    reward: int = 0,
) -> io.BytesIO:
    """Render XO using PRIME's shared Card class, fonts, panels and glow system."""
    if len(board) != 9 or any(value not in (None, "X", "O") for value in board):
        raise ValueError("invalid XO board")
    design = {
        "glowStrength": 58,
        "bgBlur": 0,
        "bgOverlay": 10,
    }
    card = Card((960, 560), "#8B5CF6", None, "none", design)
    card.panel((28, 26, 932, 534), radius=28, fill="#090D19", outline="#2C3150")
    card.text((52, 42), "PRIME / MINI-GAMES", 13, "#9D8CFF", True)
    card.text((52, 66), f"XO MATCH  #{int(game_id)}", 10, "#7E8AA8", True)

    card.panel((48, 112, 436, 232), radius=20, fill="#11182A", outline="#3D4769")
    card.text((72, 132), "PLAYER X", 11, "#8A9AB8", True)
    card.text((72, 158), player_one or "Player X", 19, "#41D9FF", True, width=328)

    card.panel((48, 248, 436, 368), radius=20, fill="#11182A", outline="#3D4769")
    card.text((72, 266), "PLAYER O", 11, "#8A9AB8", True)
    card.text((72, 293), player_two or "Player O", 19, "#D18AFF", True, width=328)

    if winner_name is not None:
        card.text((54, 402), "WINNER", 12, "#8D9AB6", True)
        card.text((54, 426), winner_name, 20, "#5EE7A8", True, width=350)
        if reward > 0:
            card.text((54, 458), f"+{int(reward)} PRIME COINS", 13, "#F8CF6A", True, width=340)
    elif is_draw:
        card.text((54, 413), "DRAW", 24, "#CBD5E1", True)
        card.text((54, 451), "A perfectly matched game", 11, "#8D9AB6", width=340)
    else:
        card.text((54, 413), "YOUR TURN", 11, "#8D9AB6", True)
        card.text((54, 438), current_player_name or "Waiting for player", 17, "#FFFFFF", True, width=345)
        card.text((54, 475), "Three in a row wins", 11, "#8D9AB6")

    start_x, start_y, cell, gap = 486, 104, 132, 12
    for row in range(3):
        for column in range(3):
            index = row * 3 + column
            left = start_x + column * (cell + gap)
            top = start_y + row * (cell + gap)
            right, bottom = left + cell, top + cell
            mark = board[index]
            if mark == "X":
                fill, outline, color = "#102A3A", "#2AAECF", "#41D9FF"
            elif mark == "O":
                fill, outline, color = "#291A3C", "#9C63C7", "#D18AFF"
            else:
                fill, outline, color = "#11182A", "#2E3857", "#53627F"
            card.panel((left, top, right, bottom), radius=18, fill=fill, outline=outline)
            card.text(
                ((left + right) // 2, top + 32),
                mark if mark else str(index + 1),
                44 if mark else 25,
                color,
                True,
                center=True,
            )

    output = io.BytesIO()
    card.image.resize((card.w, card.h), Image.Resampling.LANCZOS).save(output, format="PNG")
    output.seek(0)
    return output


async def generate_xo_game_card(
    player_one: str,
    player_two: str,
    board: list[str | None],
    current_player_name: str,
    *,
    game_id: int,
    winner_name: str | None = None,
    is_draw: bool = False,
    reward: int = 0,
) -> io.BytesIO:
    """Generate a compact match graphic through PRIME's existing card renderer."""
    async with _animation_gate():
        return await asyncio.to_thread(
            _render_xo_game_card,
            str(player_one or "")[:100],
            str(player_two or "")[:100],
            list(board),
            str(current_player_name or "")[:100],
            game_id=int(game_id),
            winner_name=str(winner_name)[:100] if winner_name else None,
            is_draw=bool(is_draw),
            reward=max(0, int(reward)),
        )


async def generate_rank_card(
    user: discord.Member, text_level, text_xp, next_level_xp,
    rank_pos, total_members, settings,
) -> io.BytesIO:
    """Generate a static PNG without touching disk or querying the database.

    Await from Phase 6. CPU rendering runs in a thread with bounded concurrency.
    Missing optional statistics display an em dash, never invented values.
    """
    settings = dict(settings or {})
    name = str(getattr(user, "display_name", None) or getattr(user, "name", "Member"))
    handle = "@" + str(getattr(user, "name", "member"))
    async with _animation_gate():
        avatar, background = await _card_assets(user, settings)
        return await asyncio.to_thread(
            _render, name, handle, number(text_level), number(text_xp),
            number(next_level_xp, xp_required(number(text_level))),
            number(rank_pos), number(total_members), settings, avatar, background)


STREAK_CARD_THEMES = {
    "spark": {
        "source": "#F5C84C", "accent": "#42B9FF", "deep": "#07182D",
        "glow": "#48BFFF", "motif": "spark", "bar": "neon", "particle": "sparks",
    },
    "ember": {
        "source": "#F27A3D", "accent": "#51D9A7", "deep": "#071D1A",
        "glow": "#55F1B9", "motif": "ember", "bar": "segmented", "particle": "embers",
    },
    "flame": {
        "source": "#FF5B36", "accent": "#FFD55D", "deep": "#211906",
        "glow": "#FFE27E", "motif": "flame", "bar": "gradient", "particle": "shine",
    },
    "blaze": {
        "source": "#F04438", "accent": "#FF994A", "deep": "#2A1309",
        "glow": "#FFAD63", "motif": "blaze", "bar": "solid", "particle": "sparks",
    },
    "volcano": {
        "source": "#E94B35", "accent": "#FF4D67", "deep": "#280912",
        "glow": "#FF6478", "motif": "volcano", "bar": "segmented", "particle": "embers",
    },
    "legend": {
        "source": "#B88CFF", "accent": "#BC83FF", "deep": "#180B2A",
        "glow": "#D4A5FF", "motif": "crown", "bar": "neon", "particle": "shine",
    },
    "eternal": {
        "source": "#65D8D0", "accent": "#F18BFF", "deep": "#210B2B",
        "glow": "#F6A5FF", "motif": "eternal", "bar": "gradient", "particle": "neon",
    },
}
STREAK_CARD_MILESTONES = (
    (1, "spark", "⚡"),
    (3, "ember", "🔥"),
    (7, "flame", "🔥"),
    (14, "blaze", "🔥"),
    (30, "volcano", "🌋"),
    (100, "legend", "👑"),
    (365, "eternal", "♾️"),
)


def _streak_card_theme(stage):
    stage = stage or {}
    key = str(stage.get("stage_key") or "")
    try:
        threshold = int(stage.get("threshold") or 0)
    except (TypeError, ValueError, OverflowError):
        threshold = 0
    if key not in STREAK_CARD_THEMES:
        key = next((item[1] for item in STREAK_CARD_MILESTONES if item[0] == threshold), "")
    theme = dict(STREAK_CARD_THEMES.get(key, {
        "source": "", "accent": "#83B8FF", "deep": "#11182A",
        "glow": "#9BC7FF", "motif": "spark", "bar": "gradient",
        "particle": "sparks",
    }))
    configured_color = stage.get("color")
    if configured_color:
        try:
            rgb = ImageColor.getrgb(str(configured_color))
            configured_color = "#%02x%02x%02x" % rgb[:3]
            if not theme["source"] or configured_color.upper() != theme["source"].upper():
                theme["accent"] = configured_color
                theme["glow"] = configured_color
        except (TypeError, ValueError):
            pass
    return theme


def _mix_hex(first, second, amount):
    left, right = ImageColor.getrgb(first), ImageColor.getrgb(second)
    return "#%02x%02x%02x" % tuple(
        int(a * (1 - amount) + b * amount) for a, b in zip(left[:3], right[:3])
    )


def _draw_streak_backdrop(card, theme):
    scale = SCALE
    width, height = card.image.size
    deep, accent = theme["deep"], theme["accent"]
    edge = _mix_hex(deep, accent, 0.2)
    start = ImageColor.getrgb(deep)
    end = ImageColor.getrgb(edge)
    gradient = Image.new("RGB", (width, height))
    gradient_draw = ImageDraw.Draw(gradient)
    for x in range(width):
        t = x / max(1, width - 1)
        color = tuple(int(a * (1 - t) + b * t) for a, b in zip(start[:3], end[:3]))
        gradient_draw.line((x, 0, x, height), fill=color)
    # Keep the existing Card renderer's texture particles under the stage tint.
    card.image = Image.blend(card.image.convert("RGB"), gradient, 0.9)
    card.draw = ImageDraw.Draw(card.image)
    draw = card.draw

    # Oversized, low-contrast stage motifs make each earned stage recognizable
    # even when no optional stage artwork URL has been configured.
    motif_color = _mix_hex(deep, theme["glow"], 0.27)
    if theme["motif"] == "spark":
        points = [(840, 24), (795, 130), (838, 130), (810, 270), (910, 104), (864, 104)]
        draw.polygon([(x * scale, y * scale) for x, y in points], fill=motif_color)
    elif theme["motif"] == "ember":
        for x, y, radius in ((820, 55, 22), (900, 95, 13), (850, 205, 18), (945, 215, 11)):
            draw.ellipse(((x-radius)*scale, (y-radius)*scale,
                          (x+radius)*scale, (y+radius)*scale), fill=motif_color)
        draw.arc((770*scale, 36*scale, 974*scale, 270*scale), 205, 340,
                 fill=_mix_hex(deep, accent, 0.38), width=5*scale)
    elif theme["motif"] == "flame":
        for points in (
            [(790, 260), (815, 125), (846, 181), (861, 57), (914, 172), (948, 261)],
            [(830, 260), (853, 187), (875, 220), (894, 151), (926, 260)],
        ):
            draw.polygon([(x*scale, y*scale) for x, y in points], fill=motif_color)
    elif theme["motif"] == "blaze":
        for offset in range(-100, 180, 52):
            draw.line(((770+offset)*scale, 275*scale, (900+offset)*scale, 20*scale),
                      fill=motif_color, width=11*scale)
    elif theme["motif"] == "volcano":
        draw.polygon([(770*scale, 272*scale), (829*scale, 137*scale),
                      (858*scale, 177*scale), (902*scale, 87*scale),
                      (974*scale, 272*scale)], fill=motif_color)
        draw.line((829*scale, 137*scale, 858*scale, 177*scale),
                  fill=_mix_hex(deep, accent, 0.45), width=5*scale)
        draw.line((858*scale, 177*scale, 902*scale, 87*scale),
                  fill=_mix_hex(deep, accent, 0.45), width=5*scale)
    elif theme["motif"] == "crown":
        points = [(785, 102), (814, 151), (844, 77), (879, 151),
                  (922, 59), (947, 151), (970, 105), (951, 204), (807, 204)]
        draw.polygon([(x*scale, y*scale) for x, y in points], fill=motif_color)
        for x in (844, 922, 970):
            draw.ellipse(((x-7)*scale, 48*scale, (x+7)*scale, 62*scale),
                         fill=_mix_hex(deep, accent, 0.52))
    else:
        draw.arc((782*scale, 83*scale, 898*scale, 222*scale), 35, 325,
                 fill=motif_color, width=19*scale)
        draw.arc((862*scale, 83*scale, 978*scale, 222*scale), 215, 145,
                 fill=motif_color, width=19*scale)
    card.draw = ImageDraw.Draw(card.image)


def _draw_streak_emblem(card, center, diameter, stage_image, reaction, theme):
    x, y = center
    scale = SCALE
    radius = diameter // 2
    halo = Image.new("RGBA", card.image.size)
    hd = ImageDraw.Draw(halo)
    bounds = ((x-radius-7)*scale, (y-radius-7)*scale,
              (x+radius+7)*scale, (y+radius+7)*scale)
    hd.ellipse(bounds, fill=(*ImageColor.getrgb(theme["glow"])[:3], 88))
    halo = halo.filter(ImageFilter.GaussianBlur(12*scale))
    card.image = Image.alpha_composite(card.image.convert("RGBA"), halo).convert("RGB")
    card.draw = ImageDraw.Draw(card.image)
    card.draw.ellipse(((x-radius)*scale, (y-radius)*scale,
                       (x+radius)*scale, (y+radius)*scale),
                      fill=theme["deep"], outline=theme["accent"], width=3*scale)
    if stage_image:
        try:
            size = (diameter*scale, diameter*scale)
            with Image.open(io.BytesIO(stage_image)) as source:
                image = ImageOps.fit(
                    source.convert("RGBA"), size, method=Image.Resampling.LANCZOS,
                )
            mask = Image.new("L", size)
            ImageDraw.Draw(mask).ellipse((1, 1, size[0]-2, size[1]-2), fill=255)
            card.image.paste(image, ((x-radius)*scale, (y-radius)*scale), mask)
            card.draw = ImageDraw.Draw(card.image)
            return
        except (OSError, ValueError, Image.DecompressionBombError):
            pass
    if reaction:
        card.text((x, y-diameter//3), reaction, max(18, diameter//2), WHITE,
                  True, center=True, width=diameter-8)


def format_streak_days_remaining(remaining):
    days = number(remaining)
    if days == 1:
        return "يوم واحد"
    if days == 2:
        return "يومين"
    if 3 <= days <= 10:
        return f"{days} أيام"
    return f"{days} يومًا"


def _streak_card_milestones(stages):
    if stages is None:
        return [
            {"threshold": threshold, "stage_key": key, "reaction": icon, "name": ""}
            for threshold, key, icon in STREAK_CARD_MILESTONES
        ]
    milestones = []
    for stage in stages:
        if not isinstance(stage, dict) or not stage.get("enabled", 1):
            continue
        threshold = number(stage.get("threshold"))
        if threshold <= 0:
            continue
        milestone = dict(stage)
        milestone["threshold"] = threshold
        milestone["stage_key"] = str(milestone.get("stage_key") or "")
        milestone["name"] = str(milestone.get("name") or "")
        milestone["reaction"] = str(milestone.get("reaction") or "")
        milestones.append(milestone)
    return sorted(
        milestones,
        key=lambda milestone: (milestone["threshold"], milestone["stage_key"]),
    )


def _draw_streak_milestone_track(
    card, current, active_stage, next_stage, progress, stages=None
):
    milestones = _streak_card_milestones(stages)
    if not milestones:
        card.text((500, 131), "لا توجد مراحل مفعّلة", 10, MUTED,
                  center=True, width=450)
        return

    active_key = str((active_stage or {}).get("stage_key") or "")
    next_key = str((next_stage or {}).get("stage_key") or "")
    active_index = next(
        (i for i, milestone in enumerate(milestones)
         if milestone["stage_key"] == active_key and active_key),
        None,
    )
    next_index = next(
        (i for i, milestone in enumerate(milestones)
         if milestone["stage_key"] == next_key and next_key),
        None,
    )

    max_visible = 7
    if len(milestones) > max_visible:
        anchor = active_index if active_index is not None else (next_index or 0)
        start = max(0, min(anchor - max_visible // 2, len(milestones) - max_visible))
        milestones = milestones[start:start + max_visible]

    # PRIME cards read right-to-left: the earliest milestone starts on the right.
    visible = list(reversed(milestones))
    left, right, y, radius = 264, 736, 127, 13
    if len(visible) == 1:
        positions = [(left + right) / 2]
    else:
        gap = (right - left) / (len(visible) - 1)
        positions = [left + index * gap for index in range(len(visible))]

    for index in range(len(visible) - 1):
        left_stage, right_stage = visible[index], visible[index + 1]
        x1, x2 = positions[index], positions[index + 1]
        card.line((x1, y, x2, y), "#29334A", 5)
        if left_stage["threshold"] <= current:
            card.line((x1, y, x2, y), card.accent, 5)
        elif active_key and right_stage["stage_key"] == active_key:
            portion = min(1.0, max(0.0, progress))
            if portion > 0:
                card.line((x2, y, x2 - (x2 - x1) * portion, y), card.accent, 5)

    focus_key = active_key or next_key
    for milestone, x in zip(visible, positions):
        threshold = milestone["threshold"]
        key = milestone["stage_key"]
        reached = current >= threshold
        focused = bool(focus_key) and key == focus_key
        theme = _streak_card_theme(milestone)
        if focused:
            ring = Image.new("RGBA", card.image.size)
            ImageDraw.Draw(ring).ellipse(
                ((x-radius-5)*SCALE, (y-radius-5)*SCALE,
                 (x+radius+5)*SCALE, (y+radius+5)*SCALE),
                fill=(*ImageColor.getrgb(theme["glow"])[:3], 90),
            )
            ring = ring.filter(ImageFilter.GaussianBlur(5*SCALE))
            card.image = Image.alpha_composite(card.image.convert("RGBA"), ring).convert("RGB")
            card.draw = ImageDraw.Draw(card.image)
        card.draw.ellipse(
            ((x-radius)*SCALE, (y-radius)*SCALE,
             (x+radius)*SCALE, (y+radius)*SCALE),
            fill=theme["accent"] if reached else "#111725",
            outline=theme["accent"] if reached or focused else "#485064",
            width=2*SCALE,
        )
        reaction = milestone["reaction"]
        if reaction:
            card.text((x, y-8), reaction, 10, "#10121c" if reached else MUTED,
                      True, center=True, width=radius*2)
        card.text((x, y+19), str(threshold), 9,
                  theme["accent"] if focused else MUTED, focused,
                  center=True, width=70)
        if milestone["name"]:
            card.text((x, y+32), milestone["name"], 8, MUTED,
                      center=True, width=70)


def _draw_streak_progress(card, progress, theme):
    card.bar((294, 203, 444, 11), progress, False, theme["bar"], direction="rtl")


def _render_streak_card(
    user_name, handle, state, stage, next_stage, ranks, avatar, stage_image, stages=None
):
    stage = stage or {}
    theme = _streak_card_theme(stage)
    accent = theme["accent"]
    glow = number(stage.get("glow"), 60)
    particle = str(stage.get("particle") or theme["particle"])
    if particle not in PARTICLES:
        particle = theme["particle"]
    design = {
        **CARD_DESIGN_DEFAULTS,
        "glowStrength": glow,
        "particleDensity": 35,
        "animationEnabled": False,
        "stats": {"messages": False, "voice": False, "streak": False, "serverRank": False},
    }
    card = Card((1000, 300), accent, None, particle, design)
    _draw_streak_backdrop(card, theme)

    current = number(state.get("current_streak"))
    best = number(state.get("best_streak"))
    remaining = number(state.get("remaining"))
    try:
        progress = min(1.0, max(0.0, float(state.get("progress", 0))))
    except (TypeError, ValueError, OverflowError):
        progress = 0.0
    stage_name = str(stage.get("name") or "—")
    next_name = str(next_stage.get("name") or "أعلى مرحلة") if next_stage else "أعلى مرحلة"
    reaction = str(stage.get("reaction") or "")
    stage_description = str(stage.get("description") or "")

    # The right-to-left hierarchy follows the approved PRIME streak-card layout.
    card.panel((28, 33, 228, 268), radius=20, fill="#0A101C", outline=_mix_hex("#334052", accent, 0.36))
    card.panel((244, 33, 756, 268), radius=20, fill="#0A101C", outline=_mix_hex("#334052", accent, 0.36))
    card.panel((772, 33, 972, 268), radius=20, fill="#0A101C", outline=_mix_hex("#334052", accent, 0.36))

    card.text((46, 49), "PRIME / DAILY STREAK", 9, accent, True, width=164)
    card.text((46, 77), "أفضل ستريك", 11, MUTED, True)
    card.text((46, 99), f"{best:,}", 22, WHITE, True)
    card.panel((129, 94, 211, 122), radius=14, fill=theme["deep"], outline=accent)
    card.text((170, 101), f"{reaction} {stage_name}".strip(), 9, WHITE, True,
              center=True, width=76)
    card.line((46, 136, 210, 136), "#30384A")
    card.text((46, 151), "ترتيب السيرفر", 9, MUTED, True, width=76)
    card.text((46, 171), f"#{number(ranks.get('server_rank'), 1)}", 18, WHITE, True)
    card.text((128, 151), "الترتيب العالمي", 9, MUTED, True, width=82)
    card.text((128, 171), f"#{number(ranks.get('global_rank'), 1)}", 18, WHITE, True)
    card.text((46, 222), stage_description, 9, MUTED, width=164)

    headline = (
        f"باقي {format_streak_days_remaining(remaining)} على {next_name}"
        if next_stage else f"بلغت أعلى مرحلة · {stage_name}"
    )
    card.text((500, 52), headline, 14, WHITE, True, center=True, width=466)
    _draw_streak_milestone_track(card, current, stage, next_stage, progress, stages)
    _draw_streak_progress(card, progress, theme)
    card.text((270, 201), f"{progress:.0%}", 9, MUTED, center=True, width=32)

    card.avatar((784, 101), 70, avatar, progress=progress)
    card.text((908, 52), user_name, 12, WHITE, True, center=True, width=108)
    card.text((908, 86), f"{current:,}", 34, accent, True, center=True, width=108)
    card.text((908, 126), "يوم متتالٍ", 11, MUTED, True, center=True, width=108)
    card.panel((858, 154, 958, 192), radius=17, fill=theme["deep"], outline=accent)
    card.text((908, 163), f"{reaction} {stage_name}".strip(), 11, WHITE, True,
              center=True, width=94)
    _draw_streak_emblem(card, (908, 226), 34, stage_image, reaction, theme)

    output = io.BytesIO()
    card.image.resize((1000, 300), Image.Resampling.LANCZOS).save(output, format="PNG")
    output.seek(0)
    return output


async def generate_streak_card(user, state, stage, next_stage, ranks, stages=None):
    """Render a streak progress image through the existing bounded card pipeline."""
    user_name = str(getattr(user, "display_name", None) or getattr(user, "name", "Member"))
    handle = "@" + str(getattr(user, "name", "member"))
    async with _animation_gate():
        avatar, stage_image = await asyncio.gather(
            fetch_image(_avatar_url(user)),
            fetch_image((stage or {}).get("image")),
        )
        return await asyncio.to_thread(
            _render_streak_card,
            user_name,
            handle,
            dict(state or {}),
            dict(stage or {}),
            dict(next_stage) if next_stage else None,
            dict(ranks or {}),
            avatar,
            stage_image,
            [dict(item) for item in stages] if stages is not None else None,
        )


def _animate_png_frames(png_frames, design, durations=None, loop=0):
    bases = []
    for png_bytes in png_frames:
        with Image.open(io.BytesIO(png_bytes)) as source:
            bases.append(source.convert("RGBA"))
    if not bases:
        raise ValueError("at least one rendered rank-card frame is required")
    width, height = bases[0].size
    intensity = number(design.get("animationIntensity"), 62)
    if design.get("animationEnabled") is False or intensity == 0:
        frames = [
            base.convert("RGB").convert(
                "P", palette=Image.Palette.ADAPTIVE, colors=128,
            )
            for base in bases
        ]
        output = io.BytesIO()
        frame_durations = durations or [85] * len(frames)
        duration_arg = frame_durations if len(frames) > 1 else frame_durations[0]
        frames[0].save(
            output, format="GIF", save_all=len(frames) > 1,
            append_images=frames[1:], duration=duration_arg,
            loop=loop, disposal=1, optimize=True,
        )
        output.seek(0)
        return output
    try:
        accent = ImageColor.getrgb(str(design.get("particleColor") if
                                       design.get("particleColor") != "accent"
                                       else "#38bdf8"))
    except (TypeError, ValueError):
        accent = (56, 189, 248)
    strength = intensity / 100
    style = design.get("animationStyle", "beam")
    frames = []
    frame_count = max(18, len(bases)) if len(bases) > 1 else 18
    for index in range(frame_count):
        base = bases[index % len(bases)]
        phase = index / frame_count
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        if style == "aurora":
            offset = int(math.sin(phase * math.tau) * height * 0.13)
            draw.polygon([
                (-width * .1, height * .25 + offset),
                (width * .55, height * .12 - offset),
                (width * 1.1, height * .32 + offset),
                (width * 1.1, height * .43 + offset),
                (width * .5, height * .27 - offset),
                (-width * .1, height * .39 + offset),
            ], fill=(*accent, int(38 + 70 * strength)))
            draw.polygon([
                (-width * .1, height * .58 - offset),
                (width * .6, height * .46 + offset),
                (width * 1.1, height * .62 - offset),
                (width * 1.1, height * .7 - offset),
                (width * .55, height * .57 + offset),
                (-width * .1, height * .69 - offset),
            ], fill=(180, 130, 255, int(24 + 50 * strength)))
            layer = layer.filter(ImageFilter.GaussianBlur(max(4, int(height * .035))))
        elif style == "burst":
            cx = int(width * (0.2 + phase * .6))
            cy = int(height * (0.28 + .08 * math.sin(phase * math.tau)))
            radius = int(min(width, height) * (.10 + .04 * math.sin(phase * math.tau)))
            for ray in range(12):
                angle = ray * math.tau / 12 + phase * math.tau * .25
                outer = radius * (1.7 if ray % 2 == 0 else 1.1)
                draw.line((cx, cy, cx + math.cos(angle) * outer,
                           cy + math.sin(angle) * outer),
                          fill=(*accent, int(55 * strength)), width=max(2, int(width * .004)))
            draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius),
                         fill=(*accent, int(54 * strength)))
            layer = layer.filter(ImageFilter.GaussianBlur(max(5, int(height * .025))))
        else:
            travel = -width * .35 + phase * width * 1.7
            beam_width = max(22, int(width * (.10 + .08 * strength)))
            draw.polygon([
                (travel, 0), (travel + beam_width, 0),
                (travel - height * .22 + beam_width, height),
                (travel - height * .22, height),
            ], fill=(255, 255, 255, int(48 + 100 * strength)))
            layer = layer.filter(ImageFilter.GaussianBlur(max(3, int(width * .012))))
        # A second soft accent glow keeps the motion visible without obscuring text.
        glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        gx = int(width * (.18 + .64 * phase))
        gy = int(height * (.28 + .07 * math.sin(phase * math.tau)))
        radius = max(18, int(min(width, height) * .12))
        gd.ellipse((gx-radius, gy-radius, gx+radius, gy+radius),
                   fill=(*accent, int(50 * strength)))
        glow = glow.filter(ImageFilter.GaussianBlur(max(8, int(radius * .7))))
        frame = Image.alpha_composite(base, glow)
        frame = Image.alpha_composite(frame, layer).convert("RGB")
        frames.append(frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=128))
    output = io.BytesIO()
    frame_durations = durations or [85] * frame_count
    if len(frame_durations) != frame_count:
        frame_durations = [
            frame_durations[min(len(frame_durations) - 1,
                                index * len(frame_durations) // frame_count)]
            for index in range(frame_count)
        ]
    duration_arg = frame_durations if len(frames) > 1 else frame_durations[0]
    frames[0].save(
        output, format="GIF", save_all=True, append_images=frames[1:],
        duration=duration_arg, loop=loop, disposal=1, optimize=True,
    )
    output.seek(0)
    return output


def _animate_png(png_bytes, design):
    return _animate_png_frames([png_bytes], design)


MAX_LEVEL_CARD_RENDER_FRAMES = 18


def _sample_background_frames(frame_count, durations, max_frames=MAX_LEVEL_CARD_RENDER_FRAMES):
    """Sample long backgrounds evenly while preserving total playback duration."""
    count = max(1, int(frame_count))
    timings = list(durations or [85] * count)
    if len(timings) < count:
        timings.extend([85] * (count - len(timings)))
    timings = timings[:count]
    if count <= max(1, int(max_frames)):
        return list(range(count)), timings
    target_count = max(2, int(max_frames))
    indices = [
        round(index * (count - 1) / (target_count - 1))
        for index in range(target_count)
    ]
    sampled_durations = []
    for index, start in enumerate(indices):
        stop = indices[index + 1] if index + 1 < len(indices) else count
        sampled_durations.append(max(10, sum(timings[start:stop])))
    return indices, sampled_durations


def _render_animated_card(
    name, handle, level, xp, required, rank, total, settings, avatar, background,
    design,
):
    frame_count, durations, loop = _animated_background_info(background)
    frame_indices, frame_durations = _sample_background_frames(frame_count, durations)
    # Rendering each Pillow frame is the expensive part. Keep output motion smooth
    # while avoiding 24-30 full card renders for long GIF/video backgrounds.
    png_frames = [
        _render(
            name, handle, level, xp, required, rank, total, settings, avatar,
            background, frame_index,
        ).getvalue()
        for frame_index in frame_indices
    ]
    return _animate_png_frames(png_frames, design, frame_durations, loop)


async def generate_level_up_gif(
    user: discord.Member, text_level, text_xp, next_level_xp,
    rank_pos, total_members, settings,
) -> io.BytesIO:
    """Create animated rank cards while preserving animated GIF backgrounds."""
    settings = dict(settings or {})
    name = str(getattr(user, "display_name", None) or getattr(user, "name", "Member"))
    handle = "@" + str(getattr(user, "name", "member"))
    design = settings.get("card_design")
    if not isinstance(design, dict):
        design = {}
    async with _animation_gate():
        avatar, background = await _card_assets(user, settings)
        return await asyncio.to_thread(
            _render_animated_card, name, handle, number(text_level),
            number(text_xp), number(next_level_xp, xp_required(number(text_level))),
            number(rank_pos), number(total_members), settings, avatar, background,
            design,
        )