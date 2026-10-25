"""Render customizable PRIME profile cards in independent, variable-size layouts.

Unlike rank cards, profile cards use a store-selected template asset, canvas size,
background, flag, and equipped badges. Templates may be repository-owned raster PNG
artwork or legacy SVG overlays. Raster artwork must be composited in RGBA without
flattening its alpha channel; a checkerboard preview must never be used as an asset.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import arabic_reshaper
import cairosvg
from bidi.algorithm import get_display
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from cogs.card_images import fetch_image
import database

logger = logging.getLogger("PrimeProfileCard")
ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = (ROOT / "assets" / "profile_templates").resolve()
DEFAULT_ASSET_LIMIT = 8 * 1024 * 1024
MAX_CANVAS_PIXELS = 2_500_000
_FONT_ROOT = ROOT / "assets" / "fonts"
_RENDER_GATES: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, result))


def _safe_color(value: Any, fallback: str) -> str:
    try:
        rgb = ImageColor.getrgb(str(value))
        return "#{:02X}{:02X}{:02X}".format(*rgb[:3])
    except (TypeError, ValueError):
        return fallback


def _parse_object(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _safe_metadata(template: dict) -> dict:
    metadata = _parse_object(template.get("metadata") or template.get("metadata_json"))
    width = _clamp(metadata.get("width"), 640, 2048, 1280)
    height = _clamp(metadata.get("height"), 320, 1600, 460)
    if width * height > MAX_CANVAS_PIXELS:
        ratio = (MAX_CANVAS_PIXELS / float(width * height)) ** 0.5
        width, height = max(640, int(width * ratio)), max(320, int(height * ratio))
    metadata["width"], metadata["height"] = width, height
    layout = str(metadata.get("layout") or "wide").lower()
    if layout not in {"wide", "portrait", "square", "compact", "split"}:
        layout = "portrait" if height > width else ("square" if width == height else "wide")
    metadata["layout"] = layout
    metadata.setdefault("orientation", "portrait" if height > width else ("square" if width == height else "landscape"))
    metadata["avatar"] = metadata.get("avatar") if isinstance(metadata.get("avatar"), dict) else {}
    metadata["name"] = metadata.get("name") if isinstance(metadata.get("name"), dict) else {}
    metadata["handle"] = metadata.get("handle") if isinstance(metadata.get("handle"), dict) else {}
    metadata["level"] = metadata.get("level") if isinstance(metadata.get("level"), dict) else {}
    metadata["xp_bar"] = metadata.get("xp_bar") if isinstance(metadata.get("xp_bar"), dict) else {}
    metadata["badges"] = metadata.get("badges") if isinstance(metadata.get("badges"), dict) else {}
    metadata["flag"] = metadata.get("flag") if isinstance(metadata.get("flag"), dict) else {}
    stats = metadata.get("stats")
    metadata["stats"] = stats if isinstance(stats, list) else []
    return metadata


def _asset_roots() -> tuple[Path, ...]:
    configured = (os.environ.get("PROFILE_STORE_ASSET_DIR") or "").strip()
    if configured:
        store_root = Path(configured).expanduser().resolve()
    else:
        db_name = getattr(database, "DB_NAME", "data/bot.db")
        store_root = (Path(db_name).expanduser().resolve().parent / "profile_store_assets").resolve()
    return TEMPLATE_ROOT, store_root


def _resolve_local_asset(raw_path: Any, *, allow_svg: bool = False) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip() or len(raw_path) > 1024:
        return None
    value = raw_path.strip()
    roots = _asset_roots()
    candidate = None

    if value.replace("\\", "/").startswith("assets/profile_templates/"):
        candidate = (ROOT / value).resolve()
    else:
        supplied = Path(value).expanduser()
        if supplied.is_absolute():
            candidate = supplied.resolve()
        else:
            candidate = (roots[1] / supplied).resolve()

    if not any(candidate == root or root in candidate.parents for root in roots):
        logger.warning("Blocked profile asset path outside approved asset roots")
        return None
    suffix = candidate.suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    if allow_svg and candidate.is_relative_to(TEMPLATE_ROOT):
        allowed.add(".svg")
    if suffix not in allowed or not candidate.is_file():
        return None
    try:
        if candidate.stat().st_size > DEFAULT_ASSET_LIMIT:
            return None
    except OSError:
        return None
    return candidate


def _read_local_asset(raw_path: Any, *, template_asset: bool = False) -> bytes | None:
    path = _resolve_local_asset(raw_path, allow_svg=template_asset)
    if path is None:
        return None
    try:
        raw = path.read_bytes()
        if path.suffix.lower() == ".svg":
            # Only repository-owned, allowlisted template SVGs reach this branch.
            return cairosvg.svg2png(bytestring=raw, output_width=None, output_height=None)
        return raw
    except Exception:
        logger.exception("Could not decode a local profile template asset: %s", path.name)
        return None


async def _read_store_asset(
    item: dict | None,
    *,
    template_asset: bool = False,
    color_style: dict | None = None,
) -> bytes | None:
    if not isinstance(item, dict):
        return None
    asset_url = item.get("asset_url")
    if isinstance(asset_url, str) and asset_url:
        # Existing downloader enforces public HTTP(S), DNS, body-size, and image checks.
        return await fetch_image(asset_url)

    path = _resolve_local_asset(item.get("asset_path"), allow_svg=template_asset)
    if path is None:
        return None
    if template_asset and path.suffix.lower() == ".svg":
        try:
            # SVG template frames are separate, repo-owned transparent assets.
            # Replace only their known palette colors, leaving geometry untouched.
            svg = path.read_text(encoding="utf-8")
            source_style = _parse_object(item.get("style") or item.get("style_json"))
            selected = _parse_object(color_style)
            original_accent = _safe_color(source_style.get("accent"), "#B34CFF")
            original_secondary = _safe_color(
                source_style.get("accent2") or source_style.get("glow"),
                original_accent,
            )
            target_accent = _safe_color(selected.get("accent"), original_accent)
            target_secondary = _safe_color(
                selected.get("divider") or selected.get("accent2"),
                original_secondary,
            )
            if original_accent != target_accent:
                svg = re.sub(re.escape(original_accent), target_accent, svg, flags=re.IGNORECASE)
            if original_secondary != target_secondary:
                svg = re.sub(re.escape(original_secondary), target_secondary, svg, flags=re.IGNORECASE)
            return cairosvg.svg2png(bytestring=svg.encode("utf-8"))
        except Exception:
            logger.exception("Could not render profile template SVG: %s", path.name)
            return None
    return _read_local_asset(str(path), template_asset=template_asset)


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(_FONT_ROOT / filename), max(9, int(size)))
    except OSError:
        return ImageFont.load_default()


def _display_text(value: Any) -> str:
    text = str(value if value is not None else "")
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _number(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(int(value), 10**18))
    except (TypeError, ValueError, OverflowError):
        return default


def _scale_position(box: dict, key: str, default: int, upper: int) -> int:
    return _clamp(box.get(key), 0, upper, default)


def _metadata_for_native_frame(metadata: dict, raw_frame: bytes | None) -> dict:
    """Use the PNG's real canvas size and scale element coordinates without distorting artwork."""
    if not raw_frame:
        return metadata
    try:
        with Image.open(io.BytesIO(raw_frame)) as frame:
            native_width, native_height = frame.size
    except Exception:
        return metadata
    if (
        native_width <= 0 or native_height <= 0
        or native_width * native_height > MAX_CANVAS_PIXELS
        or (native_width, native_height) == (metadata["width"], metadata["height"])
    ):
        return metadata

    old_width, old_height = metadata["width"], metadata["height"]
    sx, sy = native_width / old_width, native_height / old_height
    scaled = dict(metadata)
    scaled["width"], scaled["height"] = native_width, native_height
    scaled["orientation"] = (
        "portrait" if native_height > native_width
        else ("square" if native_width == native_height else "landscape")
    )
    if scaled.get("layout") not in {"compact", "split"}:
        scaled["layout"] = (
            "portrait" if native_height > native_width
            else ("square" if native_width == native_height else "wide")
        )

    for key in ("avatar", "name", "handle", "level", "xp_bar", "badges", "flag"):
        box = scaled.get(key)
        if not isinstance(box, dict):
            continue
        box = dict(box)
        for coordinate in ("x", "width"):
            if coordinate in box:
                box[coordinate] = int(box[coordinate] * sx)
        for coordinate in ("y", "height"):
            if coordinate in box:
                box[coordinate] = int(box[coordinate] * sy)
        if "size" in box:
            box["size"] = int(box["size"] * min(sx, sy))
        scaled[key] = box

    stats = []
    for stat in scaled.get("stats") or []:
        if not isinstance(stat, dict):
            continue
        item = dict(stat)
        for key in ("x", "w"):
            if key in item:
                item[key] = int(item[key] * sx)
        for key in ("y", "h"):
            if key in item:
                item[key] = int(item[key] * sy)
        stats.append(item)
    scaled["stats"] = stats
    return scaled


def _draw_gradient(width: int, height: int, base: str, accent: str) -> Image.Image:
    """Build a smooth dark gradient in O(height), then add a blurred accent glow."""
    base_rgb = ImageColor.getrgb(base)
    accent_rgb = ImageColor.getrgb(accent)
    image = Image.new("RGB", (width, height), base_rgb)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        fraction = y / max(1, height - 1)
        blend = 0.06 + fraction * 0.10
        color = tuple(
            int(base_rgb[channel] * (1 - blend) + accent_rgb[channel] * blend)
            for channel in range(3)
        )
        draw.line((0, y, width, y), fill=color)
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.ellipse(
        (int(width * .47), -int(height * .25), int(width * 1.05), int(height * .72)),
        fill=(*accent_rgb, 48),
    )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(max(18, width // 22)))
    return Image.alpha_composite(image.convert("RGBA"), glow_layer)


def _image_from_bytes(raw: bytes | None) -> Image.Image | None:
    if not raw:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width <= 0 or source.height <= 0 or source.width * source.height > 16_000_000:
                return None
            source.seek(0)
            return ImageOps.exif_transpose(source.convert("RGBA")).copy()
    except Exception:
        logger.warning("Ignoring an invalid profile image asset", exc_info=True)
        return None


def _fit_avatar(raw: bytes | None, size: int, accent: str) -> Image.Image:
    image = _image_from_bytes(raw)
    if image is None:
        image = Image.new("RGBA", (size, size), "#1D2030")
        draw = ImageDraw.Draw(image)
        draw.ellipse((size * .2, size * .1, size * .8, size * .7), fill="#A3A6BA")
        draw.rounded_rectangle((size * .1, size * .6, size * .9, size * 1.1), radius=size * .25, fill="#666A80")
    image = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS, centering=(.5, .45))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((1, 1, size - 2, size - 2), fill=255)
    image.putalpha(mask)
    border = Image.new("RGBA", (size + 18, size + 18), (0, 0, 0, 0))
    draw = ImageDraw.Draw(border)
    draw.ellipse((2, 2, size + 16, size + 16), outline=accent, width=7)
    draw.ellipse((8, 8, size + 10, size + 10), outline=(255, 255, 255, 180), width=2)
    border.alpha_composite(image, (9, 9))
    return border


def _draw_fallback_frame(image: Image.Image, style: dict, metadata: dict) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    accent = _safe_color(style.get("accent"), "#F58FB2")
    accent_rgb = ImageColor.getrgb(accent)
    layout = metadata["layout"]
    margin = max(12, int(min(width, height) * .035))
    radius = max(18, int(min(width, height) * .045))
    draw.rounded_rectangle(
        (margin, margin, width - margin, height - margin),
        radius=radius, outline=(*accent_rgb, 235), width=max(3, margin // 4),
        fill=(8, 9, 17, 70),
    )
    avatar = metadata.get("avatar") or {}
    x = _scale_position(avatar, "x", width - int(min(width, height) * .27), width)
    y = _scale_position(avatar, "y", margin * 2, height)
    size = _clamp(avatar.get("size"), 72, min(width, height) // 2, int(min(width, height) * .2))
    draw.ellipse((x - 10, y - 10, x + size + 10, y + size + 10), outline=(*accent_rgb, 230), width=max(4, margin // 3))
    stats = metadata.get("stats") or []
    for stat in stats:
        try:
            sx, sy = int(stat["x"]), int(stat["y"])
            sw, sh = int(stat["w"]), int(stat["h"])
        except (KeyError, TypeError, ValueError):
            continue
        if sx < 0 or sy < 0 or sw <= 0 or sh <= 0 or sx + sw > width or sy + sh > height:
            continue
        draw.rounded_rectangle((sx, sy, sx + sw, sy + sh), radius=max(8, sh // 5),
                               fill=(5, 7, 14, 190), outline=(*accent_rgb, 210), width=2)


def _draw_stat_text(draw: ImageDraw.ImageDraw, stat: dict, value: str, scale: float, text_color: str, muted: str) -> None:
    try:
        x, y, width, height = (int(stat[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return
    if min(x, y, width, height) < 0 or width <= 0 or height <= 0:
        return
    label_font = _load_font(max(11, int(19 * scale)))
    value_font = _load_font(max(14, int(28 * scale)), bold=True)
    center_x = x + width // 2
    draw.text((center_x, y + int(height * .36)), _display_text(stat.get("label", "")),
              font=label_font, fill=muted, anchor="mm", stroke_width=0)
    shown = str(value)
    max_chars = max(5, int(width / max(8, 14 * scale)))
    if len(shown) > max_chars:
        shown = shown[:max_chars - 1] + "…"
    draw.text((center_x, y + int(height * .70)), _display_text(shown),
              font=value_font, fill=text_color, anchor="mm", stroke_width=0)


def render_profile_card(
    profile: dict,
    *,
    avatar_bytes: bytes | None = None,
    background_bytes: bytes | None = None,
    template_bytes: bytes | None = None,
    flag_bytes: bytes | None = None,
    badge_bytes: list[bytes | None] | None = None,
    template: dict | None = None,
) -> io.BytesIO:
    """Render one PNG using the selected template's own dimensions and layout."""
    profile = dict(profile or {})
    template = dict(template or {})
    metadata = _metadata_for_native_frame(
        _safe_metadata(template), template_bytes
    )
    style = _parse_object(template.get("style") or template.get("style_json"))
    color_style = _parse_object(profile.get("color_style"))
    style.update(color_style)
    requested_accent = profile.get("accent_color")
    requested_divider = profile.get("divider_color")
    requested_xp = profile.get("xp_color")
    accent_source = (
        requested_accent
        if requested_accent and requested_accent != "#F58FB2"
        else style.get("accent")
    )
    divider_source = (
        requested_divider
        if requested_divider and requested_divider != "#F58FB2"
        else style.get("divider")
    )
    xp_source = (
        requested_xp
        if requested_xp and requested_xp != "#F58FB2"
        else style.get("xp")
    )
    accent = _safe_color(accent_source, "#B34CFF")
    divider_color = _safe_color(divider_source, style.get("accent2") or accent)
    xp_color = _safe_color(xp_source, accent)
    style["accent"] = accent
    style["divider"] = divider_color
    style["xp"] = xp_color
    width, height = metadata["width"], metadata["height"]
    scale = max(.55, min(1.25, width / 1000))
    deep = _safe_color(style.get("deep"), "#090B15")
    text_color = _safe_color(profile.get("font_color"), "#F9F7FF")
    muted = "#B8B6C7"

    bg = _image_from_bytes(background_bytes)
    if bg is None:
        canvas = _draw_gradient(width, height, deep, accent)
    else:
        fitted = ImageOps.fit(bg, (width, height), method=Image.Resampling.LANCZOS, centering=(.5, .4))
        canvas = fitted
    # Mask the background to the card shape so transparent template corners stay transparent.
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    inset = max(8, int(min(width, height) * .022))
    radius = max(18, int(min(width, height) * .04))
    mask_draw.rounded_rectangle((inset, inset, width-inset, height-inset), radius=radius, fill=255)
    canvas.putalpha(mask)

    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    shade_draw.rounded_rectangle((inset, inset, width-inset, height-inset), radius=radius,
                                 fill=(3, 4, 10, 95))
    canvas = Image.alpha_composite(canvas, shade)

    frame = _image_from_bytes(template_bytes)
    if frame is not None:
        if frame.size != (width, height):
            frame = frame.resize((width, height), Image.Resampling.LANCZOS)
        canvas = Image.alpha_composite(canvas, frame)
    else:
        _draw_fallback_frame(canvas, style, metadata)

    draw = ImageDraw.Draw(canvas, "RGBA")
    avatar_box = metadata.get("avatar") or {}
    avatar_size = _clamp(avatar_box.get("size"), 72, min(width, height) // 2,
                         max(76, int(min(width, height) * .2)))
    avatar_x = _scale_position(avatar_box, "x", width-avatar_size-44, width-avatar_size)
    avatar_y = _scale_position(avatar_box, "y", inset * 2, height-avatar_size)
    avatar = _fit_avatar(avatar_bytes, avatar_size, accent)
    avatar_dest = (
        max(0, min(width-avatar.width, avatar_x - 9)),
        max(0, min(height-avatar.height, avatar_y - 9)),
    )
    canvas.alpha_composite(avatar, avatar_dest)

    # Optional flag is always rendered as a separate element, never baked into a sprite sheet.
    flag_box = metadata.get("flag") or {}
    flag_w = _clamp(flag_box.get("width"), 24, 120, 60)
    flag_h = _clamp(flag_box.get("height"), 18, 80, 40)
    flag_x = _scale_position(flag_box, "x", inset * 2, width-flag_w)
    flag_y = _scale_position(flag_box, "y", inset * 2, height-flag_h)
    flag = _image_from_bytes(flag_bytes)
    if flag is not None:
        flag = ImageOps.fit(flag, (flag_w, flag_h), method=Image.Resampling.LANCZOS)
        flag_mask = Image.new("L", (flag_w, flag_h), 0)
        ImageDraw.Draw(flag_mask).rounded_rectangle((0, 0, flag_w-1, flag_h-1), radius=max(3, flag_h//5), fill=255)
        flag.putalpha(flag_mask)
        canvas.alpha_composite(flag, (flag_x, flag_y))
        ImageDraw.Draw(canvas).rounded_rectangle((flag_x, flag_y, flag_x+flag_w-1, flag_y+flag_h-1),
                                                 radius=max(3, flag_h//5), outline=accent, width=2)

    draw = ImageDraw.Draw(canvas, "RGBA")
    name_box = metadata.get("name") or {}
    name_x = _scale_position(name_box, "x", width - avatar_size - 50, width)
    name_y = _scale_position(name_box, "y", int(height * .56), height)
    handle_box = metadata.get("handle") or {}
    handle_x = _scale_position(handle_box, "x", name_x, width)
    handle_y = _scale_position(handle_box, "y", name_y + int(32*scale), height)
    name = str(profile.get("display_name") or profile.get("name") or "Member").strip()[:48]
    handle = "@" + str(profile.get("username") or "member").strip()[:42].lstrip("@")
    name_font = _load_font(max(20, int(39 * scale)), bold=True)
    handle_font = _load_font(max(13, int(21 * scale)))
    draw.text((name_x, name_y), _display_text(name), font=name_font, fill=text_color,
              anchor=str(name_box.get("anchor") or "ra"), stroke_width=1,
              stroke_fill=(0, 0, 0, 100))
    draw.text((handle_x, handle_y), _display_text(handle), font=handle_font, fill=muted,
              anchor=str(handle_box.get("anchor") or "ra"))

    level_box = metadata.get("level") or {}
    level_x = _scale_position(level_box, "x", inset * 3, width)
    level_y = _scale_position(level_box, "y", int(height * .48), height)
    level = _number(profile.get("level"), 0)
    xp = _number(profile.get("xp"), 0)
    required = max(1, _number(profile.get("required_xp"), 1))
    progress_font = _load_font(max(16, int(26 * scale)), bold=True)
    draw.text((level_x, level_y), _display_text(f"المستوى {level}"),
              font=progress_font, fill=text_color,
              anchor=str(level_box.get("anchor") or "la"))
    xp_box = metadata.get("xp_bar") or {}
    bar_x = _scale_position(xp_box, "x", level_x, width-20)
    bar_y = _scale_position(xp_box, "y", level_y + int(36*scale), height-10)
    bar_w = _clamp(xp_box.get("width"), 80, width, max(100, width - bar_x - 50))
    bar_h = _clamp(xp_box.get("height"), 6, 28, max(8, int(12*scale)))
    bar_w = min(bar_w, width - bar_x - 12)
    draw.rounded_rectangle((bar_x, bar_y, bar_x+bar_w, bar_y+bar_h),
                           radius=bar_h//2, fill=(5, 7, 14, 220),
                           outline=(*ImageColor.getrgb(xp_color), 180), width=1)
    fill_w = int(bar_w * min(1.0, xp / required))
    if fill_w > 0:
        draw.rounded_rectangle((bar_x, bar_y, bar_x+max(bar_h, fill_w), bar_y+bar_h),
                               radius=bar_h//2, fill=xp_color)
    xp_font = _load_font(max(10, int(15*scale)))
    draw.text((bar_x, min(height-2, bar_y+bar_h+int(17*scale))),
              _display_text(f"{xp:,} / {required:,} XP"),
              font=xp_font, fill=muted, anchor="la")

    stat_values = {
        "balance": f"{_number(profile.get('balance')):,}",
        "messages": f"{_number(profile.get('messages')):,}",
        "rank": f"#{_number(profile.get('rank'))}" if profile.get("rank") else "—",
        "voice": f"{_number(profile.get('voice_seconds')) // 3600}h",
    }
    for stat in metadata.get("stats") or []:
        if not isinstance(stat, dict):
            continue
        key = str(stat.get("key") or "")
        if key in stat_values:
            _draw_stat_text(draw, stat, stat_values[key], scale, text_color, muted)

    # Badges are separate image files/records and are composited individually.
    badges = badge_bytes or []
    badge_cfg = metadata.get("badges") or {}
    badge_size = _clamp(badge_cfg.get("size"), 20, 72, max(24, int(32*scale)))
    badge_x = _scale_position(badge_cfg, "x", inset * 3, width)
    badge_y = _scale_position(badge_cfg, "y", int(height * .65), height-badge_size)
    badge_limit = _clamp(badge_cfg.get("limit"), 0, 8, 5)
    for index, raw in enumerate(badges[:badge_limit]):
        icon = _image_from_bytes(raw)
        if icon is not None:
            icon = ImageOps.fit(icon, (badge_size, badge_size), method=Image.Resampling.LANCZOS)
            mask_icon = Image.new("L", icon.size, 0)
            ImageDraw.Draw(mask_icon).ellipse((0, 0, badge_size-1, badge_size-1), fill=255)
            icon.putalpha(mask_icon)
            canvas.alpha_composite(icon, (min(width-badge_size, badge_x+index*(badge_size+8)), badge_y))
        else:
            x = badge_x + index*(badge_size+8)
            if x + badge_size > width:
                break
            draw = ImageDraw.Draw(canvas, "RGBA")
            draw.ellipse((x, badge_y, x+badge_size, badge_y+badge_size),
                         fill=(10, 11, 24, 235), outline=accent, width=2)
            draw.text((x+badge_size//2, badge_y+badge_size//2), "✦",
                      font=_load_font(max(12, int(badge_size*.58)), bold=True),
                      fill=text_color, anchor="mm")

    # A clean, high-resolution PNG avoids accidentally treating template sheets as cards.
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


async def generate_profile_card(user: Any, profile: dict, configuration: dict) -> io.BytesIO:
    """Fetch member/template assets with the existing SSRF-safe downloader and render off-loop."""
    config = dict(configuration or {})
    template = config.get("template") if isinstance(config.get("template"), dict) else {}
    color_style = config.get("color_style") or {}
    merged_profile = dict(profile or {})
    merged_profile["color_style"] = color_style
    if config.get("accent_color") and config.get("accent_color") != "#F58FB2":
        merged_profile["accent_color"] = config["accent_color"]
    if config.get("divider_color") and config.get("divider_color") != "#F58FB2":
        merged_profile["divider_color"] = config["divider_color"]
    if config.get("xp_color") and config.get("xp_color") != "#F58FB2":
        merged_profile["xp_color"] = config["xp_color"]
    if config.get("font_color"):
        merged_profile["font_color"] = config["font_color"]

    avatar_url = None
    try:
        avatar_url = str(user.display_avatar.url) if getattr(user, "display_avatar", None) else None
    except Exception:
        avatar_url = None
    background_item = config.get("background")
    flag_item = config.get("flag")
    badges = config.get("badges") or []
    tint_style = dict(color_style) if isinstance(color_style, dict) else {}
    if config.get("accent_color") and config.get("accent_color") != "#F58FB2":
        tint_style["accent"] = config["accent_color"]
    if config.get("divider_color") and config.get("divider_color") != "#F58FB2":
        tint_style["divider"] = config["divider_color"]
    template_bytes, background_bytes, flag_bytes, avatar_bytes = await asyncio.gather(
        _read_store_asset(template, template_asset=True, color_style=tint_style),
        _read_store_asset(background_item),
        _read_store_asset(flag_item),
        fetch_image(avatar_url) if avatar_url else asyncio.sleep(0, result=None),
    )
    badge_bytes = await asyncio.gather(*[_read_store_asset(item) for item in badges[:5]]) if badges else []

    loop = asyncio.get_running_loop()
    semaphore = _RENDER_GATES.setdefault(loop, asyncio.Semaphore(3))
    async with semaphore:
        return await asyncio.to_thread(
            render_profile_card,
            merged_profile,
            avatar_bytes=avatar_bytes,
            background_bytes=background_bytes,
            template_bytes=template_bytes,
            flag_bytes=flag_bytes,
            badge_bytes=badge_bytes,
            template=template,
        )
