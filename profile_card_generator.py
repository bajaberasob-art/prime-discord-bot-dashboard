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
import math
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
# This module is at the repository root, so .parent is the project root.
ROOT = Path(__file__).resolve().parent
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


_FONT_FAMILY_FILES = {
    # Try bundled fonts first, then common Linux fonts if the host provides them.
    "modern": {
        False: ("DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        True: ("DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    },
    "tech": {
        False: ("DejaVuSansCondensed.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "DejaVuSans.ttf"),
        True: ("DejaVuSansCondensed-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
               "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"),
    },
    "elegant": {
        False: ("DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
                "/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf", "DejaVuSans.ttf"),
        True: ("DejaVuSerif-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
               "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf", "DejaVuSans-Bold.ttf"),
    },
    "soft": {
        False: ("/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf", "DejaVuSans.ttf"),
        True: ("/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf", "DejaVuSans-Bold.ttf"),
    },
}


def _normalise_font_family(value: Any) -> str:
    family = str(value or "modern").strip().lower()
    return family if family in _FONT_FAMILY_FILES else "modern"


def _load_font(
    size: int, bold: bool = False, font_family: str = "modern"
) -> ImageFont.ImageFont:
    """Load the requested template font, with portable fallbacks for minimal hosts."""
    family = _normalise_font_family(font_family)
    for candidate in _FONT_FAMILY_FILES[family][bool(bold)]:
        path = Path(candidate)
        if not path.is_absolute():
            path = _FONT_ROOT / candidate
        try:
            if path.is_file():
                return ImageFont.truetype(str(path), max(9, int(size)))
        except OSError:
            continue
    # Modern/DejaVu is bundled, so this fallback should normally be available.
    fallback = _FONT_ROOT / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(str(fallback), max(9, int(size)))
    except OSError:
        return ImageFont.load_default()


def _fit_font(
    text: Any,
    max_width: int,
    preferred_size: int,
    *,
    minimum_size: int = 8,
    bold: bool = False,
    font_family: str = "modern",
) -> ImageFont.ImageFont:
    """Return the largest available font that fits the rendered text in its box."""
    shown = _display_text(text)
    width = max(1, int(max_width))
    size = max(minimum_size, int(preferred_size))
    # Use bundled DejaVu Sans Arabic glyphs for consistent readability while retaining
    # each template's selected family for Latin names and values.
    resolved_family = "modern" if _contains_arabic(text) else font_family
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    while True:
        font = _load_font(size, bold=bold, font_family=resolved_family)
        bounds = probe.textbbox((0, 0), shown, font=font, stroke_width=0)
        measured = max(0, bounds[2] - bounds[0])
        if measured <= width or size <= minimum_size:
            return font
        size -= 1


def _mix_hex(left: Any, right: Any, right_weight: float = 0.5) -> str:
    """Blend two colors without losing the selected template's palette."""
    try:
        left_rgb = ImageColor.getrgb(str(left))
        right_rgb = ImageColor.getrgb(str(right))
    except (TypeError, ValueError):
        return "#F8FAFF"
    weight = max(0.0, min(1.0, float(right_weight)))
    rgb = tuple(round(a * (1 - weight) + b * weight) for a, b in zip(left_rgb[:3], right_rgb[:3]))
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _region_luminance(image: Image.Image, bounds: tuple[int, int, int, int]) -> float | None:
    """Sample a small region behind an overlay to choose readable contrast automatically."""
    left, top, right, bottom = bounds
    left = max(0, min(image.width, int(left)))
    right = max(left, min(image.width, int(right)))
    top = max(0, min(image.height, int(top)))
    bottom = max(top, min(image.height, int(bottom)))
    if right <= left or bottom <= top:
        return None
    try:
        crop = image.convert("RGB").crop((left, top, right, bottom))
        crop.thumbnail((20, 20), Image.Resampling.BILINEAR)
        pixels = list(crop.getdata())
        if not pixels:
            return None
        return sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in pixels) / len(pixels)
    except Exception:
        return None


def _contrast_text_color(
    image: Image.Image,
    box: dict,
    light_color: str,
    dark_color: str,
    *,
    stat_cell: bool = False,
    line_height: int | None = None,
) -> str:
    """Choose a light or dark text color from the actual template pixels underneath it."""
    try:
        x, y = int(box.get("x", 0)), int(box.get("y", 0))
        if stat_cell:
            width = int(box.get("w", 0))
            height = int(box.get("h", 0))
            # Sample the center rather than the bright ornamental border of a stat cell.
            inset_x, inset_y = int(width * 0.18), int(height * 0.12)
            bounds = (x + inset_x, y + inset_y, x + width - inset_x, y + height - inset_y)
        else:
            width = int(box.get("max_width", box.get("width", image.width // 2)))
            anchor = str(box.get("anchor") or "la")
            if anchor.startswith("r"):
                left = x - width
            elif anchor.startswith("m"):
                left = x - width // 2
            else:
                left = x
            height = max(10, int(line_height or image.height * 0.04))
            bounds = (left + int(width * 0.10), y - height, left + int(width * 0.90), y + max(2, height // 5))
        luminance = _region_luminance(image, bounds)
    except (TypeError, ValueError, OverflowError):
        luminance = None
    if luminance is None:
        return light_color
    return dark_color if luminance >= 155 else light_color


def _display_text(value: Any) -> str:
    text = str(value if value is not None else "")
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _contains_arabic(value: Any) -> bool:
    """Return whether text contains Arabic-script characters, including Arabic supplements."""
    text = str(value if value is not None else "")
    return any(0x0600 <= ord(char) <= 0x08FF or 0xFB50 <= ord(char) <= 0xFEFF for char in text)


def _draw_display_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: Any,
    **kwargs: Any,
) -> None:
    """Draw Arabic with Pillow's bidi/shaping engine; use reshaping only as a fallback."""
    raw_text = str(value if value is not None else "")
    if _contains_arabic(raw_text):
        try:
            # RAQM handles mixed Arabic, digits and Latin tokens as a single bidi paragraph.
            draw.text(xy, raw_text, direction="rtl", language="ar", **kwargs)
            return
        except (TypeError, ValueError, KeyError):
            pass
        shown = _display_text(raw_text)
        try:
            draw.text(xy, shown, direction="ltr", **kwargs)
        except (TypeError, ValueError, KeyError):
            draw.text(xy, shown, **kwargs)
        return
    draw.text(xy, raw_text, **kwargs)


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
        for coordinate in ("x", "width", "max_width"):
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


def _detect_template_avatar_slot(raw_frame: bytes | Image.Image | None) -> dict | None:
    """Infer a likely empty avatar ring from a custom template image.

    This is a local image-analysis fallback for user-uploaded templates without explicit
    layout metadata. The seven bundled PRIME templates use curated coordinates instead.
    The detector looks for a high-contrast circular contour, favouring the lower-right
    area where profile templates commonly reserve an avatar slot.
    """
    frame = raw_frame.copy() if isinstance(raw_frame, Image.Image) else _image_from_bytes(raw_frame)
    if frame is None:
        return None
    native_width, native_height = frame.size
    if native_width < 160 or native_height < 160:
        return None

    # Keep the detector bounded in both CPU and memory for uploaded assets.
    analysis = frame.copy()
    analysis.thumbnail((320, 320), Image.Resampling.LANCZOS)
    width, height = analysis.size
    scale_x, scale_y = native_width / width, native_height / height

    # Transparent templates often reveal the avatar opening as a dark hole. Composite
    # transparency on a neutral dark surface before measuring edges and ring contrast.
    backdrop = Image.new("RGBA", analysis.size, (10, 13, 22, 255))
    backdrop.alpha_composite(analysis)
    grayscale = backdrop.convert("L")
    edge_image = grayscale.filter(ImageFilter.FIND_EDGES)
    gray_pixels = grayscale.load()
    edge_pixels = edge_image.load()
    min_dimension = min(width, height)
    if min_dimension < 100:
        return None

    # A compact angular sample keeps the scan cheap enough for previews, even on mobile.
    unit_circle = tuple(
        (math.cos(2.0 * math.pi * index / 32), math.sin(2.0 * math.pi * index / 32))
        for index in range(32)
    )
    radii = list(range(max(10, int(min_dimension * 0.075)),
                       max(12, int(min_dimension * 0.235)), 2))
    if not radii:
        return None

    def mean_ring(cx: int, cy: int, radius: int, pixels, radial_offset: int = 0) -> float:
        values = []
        r = radius + radial_offset
        for unit_x, unit_y in unit_circle:
            x = int(round(cx + r * unit_x))
            y = int(round(cy + r * unit_y))
            if 0 <= x < width and 0 <= y < height:
                values.append(pixels[x, y])
        return sum(values) / len(values) if values else 0.0

    best_score = 0.0
    best = None
    min_radius = min(radii)
    max_radius = max(radii)
    step_x = max(3, width // 72)
    step_y = max(3, height // 72)
    # Search the canvas using the smallest radius, then validate each radius against the
    # image bounds individually. Using max_radius for the scan bounds misses rings near
    # the right/bottom edges, which are common avatar locations in profile templates.
    for cy in range(min_radius + 4, height - min_radius - 3, step_y):
        for cx in range(min_radius + 4, width - min_radius - 3, step_x):
            for radius in radii:
                if (
                    cx - radius < 3 or cx + radius >= width - 3
                    or cy - radius < 3 or cy + radius >= height - 3
                ):
                    continue
                edge_score = mean_ring(cx, cy, radius, edge_pixels) / 255.0
                if edge_score < 0.075:
                    continue
                on_ring = mean_ring(cx, cy, radius, gray_pixels)
                inside_ring = mean_ring(cx, cy, radius, gray_pixels, -4)
                outside_ring = mean_ring(cx, cy, radius, gray_pixels, 4)
                contrast = (abs(on_ring - inside_ring) + abs(on_ring - outside_ring)) / 510.0
                inner_mean = mean_ring(cx, cy, max(3, int(radius * 0.58)), gray_pixels)
                outer_mean = mean_ring(cx, cy, radius + 8, gray_pixels)
                hole_contrast = max(0.0, outer_mean - inner_mean) / 255.0
                # Soft prior, never strong enough to override a clearly better circle elsewhere.
                location_bonus = 0.0
                nx, ny = cx / width, cy / height
                if 0.52 <= nx <= 0.96 and 0.38 <= ny <= 0.94:
                    location_bonus = 0.045
                score = (
                    0.57 * min(1.0, edge_score)
                    + 0.25 * min(1.0, contrast)
                    + 0.18 * min(1.0, hole_contrast)
                    + location_bonus
                )
                if score > best_score:
                    best_score = score
                    best = (cx, cy, radius, edge_score, contrast)

    if best is None:
        return None
    cx, cy, radius, edge_score, contrast = best
    # Reject weak circles in highly ornamented art; don't turn a random curve into an avatar slot.
    if edge_score < 0.13 or contrast < 0.075 or best_score < 0.22:
        return None

    # x/y are the top-left of the actual avatar image; _fit_avatar adds its own 9px ring pad.
    # _fit_avatar adds a 9px ring pad on every side, so subtract that padding
    # from the detected opening diameter to keep the avatar ring inside the artwork's hole.
    slot_size = int(round(2 * radius * min(scale_x, scale_y))) - 18
    center_x = int(round(cx * scale_x))
    center_y = int(round(cy * scale_y))
    slot_size = max(72, min(slot_size, int(min(native_width, native_height) * 0.34)))
    x = max(0, min(native_width - slot_size, center_x - slot_size // 2))
    y = max(0, min(native_height - slot_size, center_y - slot_size // 2))
    return {
        "x": x, "y": y, "size": slot_size,
        "confidence": round(best_score, 3),
        "detected_center": [center_x, center_y],
        "frame_width": native_width,
        "frame_height": native_height,
    }


_TEMPLATE_VISION_CACHE: dict[tuple[int, int], tuple[dict | None, dict | None]] = {}


def _prefix_sum_grid(values: list[float], width: int, height: int) -> list[list[float]]:
    """Build an integral image so thousands of candidate rectangles score in O(1)."""
    prefix = [[0.0] * (width + 1) for _ in range(height + 1)]
    for y in range(1, height + 1):
        row_total = 0.0
        offset = (y - 1) * width
        prev = prefix[y - 1]
        current = prefix[y]
        for x in range(1, width + 1):
            row_total += values[offset + x - 1]
            current[x] = prev[x] + row_total
    return prefix


def _sum_integral(
    prefix: list[list[float]], x0: int, y0: int, x1: int, y1: int,
) -> float:
    return prefix[y1][x1] - prefix[y0][x1] - prefix[y1][x0] + prefix[y0][x0]


def _build_template_analysis(raw_frame: bytes | Image.Image | None) -> dict | None:
    """Create a small alpha-aware visual feature map for fast candidate-region scoring."""
    frame = raw_frame.copy() if isinstance(raw_frame, Image.Image) else _image_from_bytes(raw_frame)
    if frame is None:
        return None
    source_width, source_height = frame.size
    if source_width < 80 or source_height < 80:
        return None

    frame.thumbnail((192, 192), Image.Resampling.LANCZOS)
    # Transparent pixels frequently contain arbitrary hidden RGB values. Composite on the
    # same kind of dark surface used by the renderer before analysing edges or texture.
    backdrop = Image.new("RGBA", frame.size, (13, 17, 29, 255))
    backdrop.alpha_composite(frame.convert("RGBA"))
    gray = backdrop.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    width, height = gray.size
    gray_values = [value / 255.0 for value in gray.getdata()]
    edge_values = [value / 255.0 for value in edges.getdata()]
    gray_squared = [value * value for value in gray_values]
    return {
        "source_width": source_width,
        "source_height": source_height,
        "width": width,
        "height": height,
        "gray": _prefix_sum_grid(gray_values, width, height),
        "gray_squared": _prefix_sum_grid(gray_squared, width, height),
        "edges": _prefix_sum_grid(edge_values, width, height),
    }


def _score_analyzed_region(
    analysis: dict | None,
    rect: tuple[int, int, int, int],
) -> float:
    """Lower is better: favour visually calm areas, including truly transparent space."""
    if not analysis:
        return 1.0
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return 1.0
    thumb_w, thumb_h = analysis["width"], analysis["height"]
    source_w, source_h = analysis["source_width"], analysis["source_height"]
    x0 = max(0, min(thumb_w - 1, int(left * thumb_w / source_w)))
    y0 = max(0, min(thumb_h - 1, int(top * thumb_h / source_h)))
    x1 = max(x0 + 1, min(thumb_w, int(math.ceil(right * thumb_w / source_w))))
    y1 = max(y0 + 1, min(thumb_h, int(math.ceil(bottom * thumb_h / source_h))))
    area = max(1, (x1 - x0) * (y1 - y0))
    mean_edge = _sum_integral(analysis["edges"], x0, y0, x1, y1) / area
    mean_gray = _sum_integral(analysis["gray"], x0, y0, x1, y1) / area
    mean_square = _sum_integral(analysis["gray_squared"], x0, y0, x1, y1) / area
    variance = max(0.0, mean_square - mean_gray * mean_gray)
    contrast_variation = min(1.0, math.sqrt(variance) / 0.376)
    return min(1.0, mean_edge * 0.74 + contrast_variation * 0.26)


def _template_region_score(
    raw_frame: bytes | Image.Image | None,
    rect: tuple[int, int, int, int],
) -> float:
    """Compatibility wrapper around the alpha-aware template analysis."""
    return _score_analyzed_region(_build_template_analysis(raw_frame), rect)


def _rect_overlaps(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return not (
        left[2] <= right[0] or right[2] <= left[0]
        or left[3] <= right[1] or right[3] <= left[1]
    )


def _inflate_rect(
    rect: tuple[int, int, int, int], padding: int, width: int, height: int,
) -> tuple[int, int, int, int]:
    return (
        max(0, rect[0] - padding), max(0, rect[1] - padding),
        min(width, rect[2] + padding), min(height, rect[3] + padding),
    )


def _choose_template_text_box(
    raw_frame: bytes | None,
    *,
    width: int,
    height: int,
    box_width: int,
    box_height: int,
    reserved: list[tuple[int, int, int, int]],
    y_range: tuple[float, float],
    x_centers: tuple[float, ...] = (0.28, 0.42, 0.58, 0.72),
) -> dict | None:
    """Compatibility helper for fixed-size boxes; uses a precomputed visual feature map."""
    analysis = _build_template_analysis(raw_frame)
    if analysis is None:
        return None
    best: tuple[float, dict] | None = None
    margin = max(8, int(min(width, height) * 0.018))
    max_x = max(margin, width - box_width - margin)
    y_start = max(margin, int(height * y_range[0]))
    y_end = min(height - box_height - margin, int(height * y_range[1]))
    y_step = max(8, int(height * 0.025))
    x_step = max(10, int(width * 0.025))
    x_positions = range(margin, max_x + 1, x_step)
    for y in range(y_start, max(y_start + 1, y_end + 1), y_step):
        for x in x_positions:
            rect = (x, y, x + box_width, y + box_height)
            if any(_rect_overlaps(rect, item) for item in reserved):
                continue
            texture = _score_analyzed_region(analysis, rect)
            prior_x = width * 0.5
            if x_centers:
                prior_x = min(x_centers, key=lambda center: abs((x + box_width / 2) / width - center)) * width
            prior_penalty = abs(x + box_width / 2 - prior_x) / max(1, width) * 0.025
            score = texture + prior_penalty
            if best is None or score < best[0]:
                best = (score, {"x": x + box_width // 2, "y": y + box_height // 2,
                                "max_width": box_width, "anchor": "mm",
                                "_rect": rect, "_score": round(score, 4)})
    if best is None or best[0] > 0.45:
        return None
    return best[1]


def _font_height(font: ImageFont.ImageFont, text: str) -> int:
    try:
        bounds = font.getbbox(_display_text(text))
        return max(int(getattr(font, "size", 16)), int(bounds[3] - bounds[1]))
    except Exception:
        return max(16, int(getattr(font, "size", 16)))


def _text_width(font: ImageFont.ImageFont, text: str) -> int:
    try:
        return max(1, int(math.ceil(font.getlength(_display_text(text)))))
    except Exception:
        bounds = font.getbbox(_display_text(text))
        return max(1, int(bounds[2] - bounds[0]))


def _prior_box_center(
    box: dict | None,
    text: str,
    font: ImageFont.ImageFont,
    default_width: int,
    default_height: int,
) -> tuple[float, float] | None:
    if not isinstance(box, dict) or "x" not in box or "y" not in box:
        return None
    try:
        x, y = float(box["x"]), float(box["y"])
        anchor = str(box.get("anchor") or "la").lower()
        visible_width = min(
            max(1, int(box.get("max_width") or default_width)),
            _text_width(font, text),
        )
        if anchor.startswith("r"):
            center_x = x - visible_width / 2
        elif anchor.startswith("m"):
            center_x = x
        else:
            center_x = x + visible_width / 2
        # Existing y coordinates describe the upper text anchor, not the search rectangle.
        center_y = y + default_height / 2
        return center_x, center_y
    except (TypeError, ValueError, OverflowError):
        return None


def _find_best_identity_block(
    analysis: dict | None,
    *,
    width: int,
    height: int,
    display_name: str,
    handle: str,
    name_metadata: dict | None,
    handle_metadata: dict | None,
    font_family: str,
    reserved: list[tuple[int, int, int, int]],
) -> dict | None:
    """Place name and handle as one measured block; never optimise each line independently."""
    display_name = (display_name or "Member").strip()[:48]
    handle = (handle or "@member").strip()[:43]
    minimum = min(width, height)
    preferred_name_size = max(28, min(64, int(minimum * 0.056)))
    preferred_handle_size = max(20, min(36, int(minimum * 0.034)))
    gap = max(5, int(minimum * 0.008))
    pad_y = max(5, int(minimum * 0.008))
    margin = max(12, int(minimum * 0.018))
    step_x = max(12, int(width * 0.025))
    step_y = max(10, int(height * 0.025))
    name_prior_font = _fit_font(display_name, max(140, int(width * 0.50)), preferred_name_size,
                                minimum_size=18, bold=True, font_family=font_family)
    handle_prior_font = _fit_font(handle, max(140, int(width * 0.50)), preferred_handle_size,
                                  minimum_size=13, font_family=font_family)
    name_prior = _prior_box_center(name_metadata, display_name, name_prior_font,
                                   int(width * 0.42), _font_height(name_prior_font, display_name))
    handle_prior = _prior_box_center(handle_metadata, handle, handle_prior_font,
                                     int(width * 0.42), _font_height(handle_prior_font, handle))
    prior_center = None
    if name_prior and handle_prior:
        prior_center = ((name_prior[0] + handle_prior[0]) / 2,
                        (name_prior[1] + handle_prior[1]) / 2)
    elif name_prior:
        prior_center = name_prior

    best: tuple[float, dict] | None = None
    width_fractions = (0.28, 0.36, 0.44, 0.52, 0.60, 0.66)
    top_min = max(margin, int(height * 0.10))
    top_max = min(height - margin, int(height * 0.70))
    for fraction in width_fractions:
        box_width = min(width - margin * 2, max(140, int(width * fraction)))
        text_width = max(120, box_width - max(12, int(box_width * 0.055)))
        name_font = _fit_font(display_name, text_width, preferred_name_size,
                              minimum_size=18, bold=True, font_family=font_family)
        handle_font = _fit_font(handle, text_width, preferred_handle_size,
                                minimum_size=13, font_family=font_family)
        name_height = _font_height(name_font, display_name)
        handle_height = _font_height(handle_font, handle)
        block_height = pad_y * 2 + name_height + gap + handle_height
        if top_max - top_min < block_height:
            continue
        x_max = width - box_width - margin
        for top in range(top_min, max(top_min + 1, top_max - block_height + 1), step_y):
            for left in range(margin, max(margin + 1, x_max + 1), step_x):
                rect = (left, top, left + box_width, top + block_height)
                if any(_rect_overlaps(rect, item) for item in reserved):
                    continue
                visual = _score_analyzed_region(analysis, rect)
                prior_penalty = 0.0
                if prior_center:
                    center_x, center_y = left + box_width / 2, top + block_height / 2
                    distance = math.hypot(
                        (center_x - prior_center[0]) / max(1, width),
                        (center_y - prior_center[1]) / max(1, height),
                    )
                    prior_penalty = min(0.10, distance * 0.12)
                readability_penalty = (
                    max(0, preferred_name_size - int(getattr(name_font, "size", preferred_name_size)))
                    / max(1, preferred_name_size) * 0.025
                    + max(0, preferred_handle_size - int(getattr(handle_font, "size", preferred_handle_size)))
                    / max(1, preferred_handle_size) * 0.015
                )
                edge_penalty = 0.05 if (
                    left < margin * 1.25 or left + box_width > width - margin * 1.25
                ) else 0.0
                score = visual + prior_penalty + readability_penalty + edge_penalty
                if best is None or score < best[0]:
                    name_y = top + pad_y + name_height // 2
                    handle_y = top + pad_y + name_height + gap + handle_height // 2
                    best = (score, {
                        "rect": rect,
                        "score": round(score, 4),
                        "name": {"x": left + box_width // 2, "y": name_y,
                                 "max_width": text_width, "anchor": "mm"},
                        "handle": {"x": left + box_width // 2, "y": handle_y,
                                   "max_width": text_width, "anchor": "mm"},
                        "font_sizes": {
                            "name": int(getattr(name_font, "size", preferred_name_size)),
                            "handle": int(getattr(handle_font, "size", preferred_handle_size)),
                        },
                    })
    if best is None or best[0] > 0.48:
        return None
    return best[1]


def _find_best_level_box(
    analysis: dict | None,
    *,
    width: int,
    height: int,
    text: str,
    metadata_box: dict | None,
    font_family: str,
    reserved: list[tuple[int, int, int, int]],
) -> dict | None:
    """Find a visually calm line for the level, excluding identity, avatar, stats and XP."""
    minimum = min(width, height)
    preferred_size = max(30, min(60, int(minimum * 0.052)))
    margin = max(12, int(minimum * 0.018))
    step_x = max(12, int(width * 0.025))
    step_y = max(10, int(height * 0.025))
    best: tuple[float, dict] | None = None
    prior_font = _fit_font(text, max(140, int(width * 0.40)), preferred_size,
                           minimum_size=18, bold=True, font_family=font_family)
    prior = _prior_box_center(metadata_box, text, prior_font,
                              int(width * 0.40), _font_height(prior_font, text))
    for fraction in (0.22, 0.28, 0.34, 0.40, 0.48, 0.56):
        box_width = min(width - margin * 2, max(140, int(width * fraction)))
        text_width = max(120, box_width - max(10, int(box_width * 0.05)))
        font = _fit_font(text, text_width, preferred_size, minimum_size=18,
                         bold=True, font_family=font_family)
        text_height = _font_height(font, text)
        pad = max(5, int(minimum * 0.01))
        box_height = text_height + pad * 2
        top_min = max(margin, int(height * 0.22))
        top_max = min(height - box_height - margin, int(height * 0.77))
        for top in range(top_min, max(top_min + 1, top_max + 1), step_y):
            for left in range(margin, max(margin + 1, width - box_width - margin + 1), step_x):
                rect = (left, top, left + box_width, top + box_height)
                if any(_rect_overlaps(rect, item) for item in reserved):
                    continue
                visual = _score_analyzed_region(analysis, rect)
                prior_penalty = 0.0
                if prior:
                    center_x, center_y = left + box_width / 2, top + box_height / 2
                    distance = math.hypot(
                        (center_x - prior[0]) / max(1, width),
                        (center_y - prior[1]) / max(1, height),
                    )
                    prior_penalty = min(0.08, distance * 0.09)
                readability_penalty = max(
                    0, preferred_size - int(getattr(font, "size", preferred_size))
                ) / max(1, preferred_size) * 0.025
                score = visual + prior_penalty + readability_penalty
                if best is None or score < best[0]:
                    best = (score, {
                        "x": left + box_width // 2,
                        "y": top + pad + text_height // 2,
                        "max_width": text_width,
                        "anchor": "mm",
                        "_rect": rect,
                        "_score": round(score, 4),
                        "_font_size": int(getattr(font, "size", preferred_size)),
                    })
    if best is None or best[0] > 0.48:
        return None
    return best[1]


def _default_stat_boxes(
    width: int, height: int, avatar: dict | None = None,
) -> list[dict]:
    """Create stat boxes below the avatar when a custom template has no stat metadata."""
    fields = (
        ("balance", "الرصيد"), ("messages", "الرسائل"),
        ("rank", "الترتيب"), ("voice", "الصوت"),
    )
    result = []
    avatar = avatar if isinstance(avatar, dict) else {}
    avatar_bottom = int(avatar.get("y", height * 0.62)) + int(
        avatar.get("size", min(width, height) * 0.18)
    )
    margin = max(8, int(min(width, height) * 0.018))
    if width >= height * 1.18:
        top, cell_h, cell_w = int(height * 0.82), max(28, int(height * 0.105)), int(width * 0.205)
        xs = [int(width * ratio) for ratio in (0.035, 0.275, 0.515, 0.755)]
        if avatar_bottom + margin > top and avatar_bottom + cell_h + margin < height:
            top = max(top, avatar_bottom + margin)
        top = min(top, height - cell_h - margin)
        for (key, label), x in zip(fields, xs):
            result.append({"key": key, "label": label, "x": x, "y": top,
                           "w": min(cell_w, width - x), "h": min(cell_h, height - top)})
    else:
        cell_w, cell_h = int(width * 0.41), max(26, int(height * 0.065))
        first_top = max(int(height * 0.77), avatar_bottom + margin)
        second_top = max(int(height * 0.87), first_top + cell_h + margin)
        if second_top + cell_h > height - margin:
            second_top = height - cell_h - margin
            first_top = min(first_top, second_top - cell_h - margin)
        for (key, label), x, top in zip(
            fields, (0.06, 0.535, 0.06, 0.535),
            (first_top, first_top, second_top, second_top),
        ):
            left = int(width * x)
            result.append({"key": key, "label": label, "x": left, "y": top,
                           "w": min(cell_w, width - left), "h": min(cell_h, height - top)})
    return result


def _get_template_vision(
    raw_frame: bytes | None,
) -> tuple[dict | None, dict | None]:
    """Cache expensive pixel analysis and avatar detection per immutable template asset."""
    if not raw_frame:
        return None, None
    key = (len(raw_frame), hash(raw_frame))
    cached = _TEMPLATE_VISION_CACHE.get(key)
    if cached is not None:
        # Move the most recently used entry to the end without retaining the image bytes.
        _TEMPLATE_VISION_CACHE.pop(key, None)
        _TEMPLATE_VISION_CACHE[key] = cached
        return cached
    frame = _image_from_bytes(raw_frame)
    if frame is None:
        return None, None
    analysis = _build_template_analysis(frame)
    detected = _detect_template_avatar_slot(frame)
    value = (analysis, detected)
    _TEMPLATE_VISION_CACHE[key] = value
    while len(_TEMPLATE_VISION_CACHE) > 12:
        _TEMPLATE_VISION_CACHE.pop(next(iter(_TEMPLATE_VISION_CACHE)))
    return value


def _apply_smart_template_layout(
    metadata: dict,
    raw_frame: bytes | None,
    *,
    profile: dict | None = None,
    style: dict | None = None,
) -> dict:
    """Place profile text using the actual template pixels, text lengths, and collision bounds."""
    result = dict(metadata or {})
    width = _clamp(result.get("width"), 320, 2048, 1280)
    height = _clamp(result.get("height"), 320, 2048, 720)
    result["width"], result["height"] = width, height
    profile = dict(profile or {})
    style = dict(style or {})
    font_family = _normalise_font_family(style.get("font_family") or "modern")
    display_name = str(profile.get("display_name") or profile.get("name") or "Member").strip()[:48]
    handle = "@" + str(profile.get("username") or "member").strip()[:42].lstrip("@")
    level_text = f"المستوى {_number(profile.get('level'), 0)}"

    analysis, detected = _get_template_vision(raw_frame)
    if analysis is None:
        result["layout_engine"] = "prime-vision-layout-v4"
        result["layout_analysis"] = {
            "method": "template-metadata-fallback",
            "name_region_optimized": False,
            "level_region_optimized": False,
            "reason": "template image unavailable",
        }
        return result

    avatar = result.get("avatar") if isinstance(result.get("avatar"), dict) else {}
    has_curated_avatar = all(key in avatar for key in ("x", "y", "size"))
    if detected and detected.get("confidence", 0) >= 0.28:
        detected_center = (
            int(detected["x"]) + int(detected["size"]) / 2,
            int(detected["y"]) + int(detected["size"]) / 2,
        )
        current_center = (
            int(avatar.get("x", 0)) + int(avatar.get("size", 0)) / 2,
            int(avatar.get("y", 0)) + int(avatar.get("size", 0)) / 2,
        )
        disagreement = math.hypot(
            detected_center[0] - current_center[0], detected_center[1] - current_center[1]
        ) if has_curated_avatar else float("inf")
        if not has_curated_avatar or disagreement >= max(24, int(avatar.get("size", 0) * 0.30)):
            source_width = max(1, int(detected.get("frame_width") or width))
            source_height = max(1, int(detected.get("frame_height") or height))
            scale_x, scale_y = width / source_width, height / source_height
            target_size = max(48, int(detected["size"] * min(scale_x, scale_y)))
            center_x = (detected["x"] + detected["size"] / 2) * scale_x
            center_y = (detected["y"] + detected["size"] / 2) * scale_y
            result["avatar"] = {
                "x": max(0, min(width - target_size, int(center_x - target_size / 2))),
                "y": max(0, min(height - target_size, int(center_y - target_size / 2))),
                "size": min(target_size, width, height),
            }
            result["avatar_detection"] = {
                "method": "circular-contour-override" if has_curated_avatar else "circular-contour",
                "confidence": detected["confidence"],
            }
    elif not has_curated_avatar:
        avatar_size = max(48, int(min(width, height) * 0.18))
        result["avatar"] = {
            "x": max(0, int(width * 0.78 - avatar_size / 2)),
            "y": max(0, int(height * 0.63 - avatar_size / 2)),
            "size": min(avatar_size, width, height),
        }
        result["avatar_detection"] = {"method": "lower-right-fallback", "confidence": 0.0}

    avatar = result.get("avatar") if isinstance(result.get("avatar"), dict) else {}
    avatar_size = _clamp(avatar.get("size"), 48, min(width, height) // 2, int(min(width, height) * 0.18))
    avatar_x = _scale_position(avatar, "x", int(width * 0.70), width - avatar_size)
    avatar_y = _scale_position(avatar, "y", int(height * 0.60), height - avatar_size)
    avatar_rect = _inflate_rect(
        (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
        max(14, int(min(width, height) * 0.018)), width, height,
    )

    stats = result.get("stats") if isinstance(result.get("stats"), list) else []
    if not stats:
        stats = _default_stat_boxes(width, height, result.get("avatar"))
        result["stats"] = stats

    margin = max(12, int(min(width, height) * 0.018))
    reserved: list[tuple[int, int, int, int]] = [avatar_rect]
    # Keep all known stat cells, badge/flag slots, and the illustrated XP track clear.
    for stat in stats:
        if not isinstance(stat, dict):
            continue
        try:
            sx, sy, sw, sh = (int(stat[k]) for k in ("x", "y", "w", "h"))
            reserved.append(_inflate_rect((sx, sy, sx + sw, sy + sh), margin // 2, width, height))
        except (KeyError, TypeError, ValueError):
            continue
    for key in ("badges", "flag"):
        box = result.get(key) if isinstance(result.get(key), dict) else {}
        try:
            bx = int(box.get("x", -1))
            by = int(box.get("y", -1))
            bw = int(box.get("width", box.get("size", 0)))
            bh = int(box.get("height", box.get("size", 0)))
            if bx >= 0 and by >= 0 and bw > 0 and bh > 0:
                reserved.append(_inflate_rect((bx, by, bx + bw, by + bh), margin // 2, width, height))
        except (TypeError, ValueError):
            pass
    xp_box = result.get("xp_bar") if isinstance(result.get("xp_bar"), dict) else {}
    if not xp_box:
        # This metadata reserves a likely lower-middle track for custom templates only.
        # The renderer never paints a second XP bar over the template artwork.
        xp_box = {
            "x": int(width * 0.07), "y": int(height * 0.57),
            "width": int(width * 0.64), "height": max(6, int(min(width, height) * 0.012)),
        }
        result["xp_bar"] = xp_box
    try:
        xp_x, xp_y = int(xp_box.get("x", -1)), int(xp_box.get("y", -1))
        xp_w, xp_h = int(xp_box.get("width", 0)), int(xp_box.get("height", 0))
        if xp_x >= 0 and xp_y >= 0 and xp_w > 0 and xp_h > 0:
            reserved.append(_inflate_rect((xp_x, xp_y, xp_x + xp_w, xp_y + xp_h),
                                          max(8, margin // 2), width, height))
    except (TypeError, ValueError):
        pass
    # Reserve the ornamental rails so text cannot sit directly over the card border.
    rail = max(8, int(min(width, height) * 0.025))
    reserved.extend([
        (0, 0, width, rail), (0, height - rail, width, height),
        (0, 0, rail, height), (width - rail, 0, width, height),
    ])

    identity = _find_best_identity_block(
        analysis, width=width, height=height,
        display_name=display_name, handle=handle,
        name_metadata=result.get("name"), handle_metadata=result.get("handle"),
        font_family=font_family, reserved=reserved,
    )
    accepted_level = None
    identity_rect = None
    level_rect = None
    level_score = None
    level_font_size = None

    # Commit new positions only as a pair. If either field has no safe region, retain the
    # existing art-directed coordinates rather than applying a half-layout that may collide.
    if identity:
        trial_reserved = reserved + [
            _inflate_rect(identity["rect"], max(6, margin // 2), width, height)
        ]
        candidate_level = _find_best_level_box(
            analysis, width=width, height=height, text=level_text,
            metadata_box=result.get("level"), font_family=font_family,
            reserved=trial_reserved,
        )
        if candidate_level:
            identity_rect = identity["rect"]
            result["name"] = identity["name"]
            result["handle"] = identity["handle"]
            accepted_level = {
                "x": candidate_level["x"], "y": candidate_level["y"],
                "max_width": candidate_level["max_width"],
                "anchor": candidate_level["anchor"],
            }
            result["level"] = accepted_level
            level_rect = candidate_level.get("_rect")
            level_score = candidate_level.get("_score")
            level_font_size = candidate_level.get("_font_size")
            reserved = trial_reserved
            if level_rect:
                reserved.append(_inflate_rect(level_rect, max(6, margin // 2), width, height))
        else:
            identity = None

    if not identity:
        identity = None
        # Preserve existing text positions if the image has no pair of safe boxes.
        # This makes the failure visible in diagnostics instead of silently misplacing labels.

    result["layout_engine"] = "prime-vision-layout-v4"
    result["layout_analysis"] = {
        "method": "alpha-aware-multiscale-region-scoring",
        "avatar_slot": result.get("avatar_detection", {}).get("method", "template-metadata"),
        "name_region_optimized": bool(identity and accepted_level),
        "level_region_optimized": bool(accepted_level),
        "layout_applied": bool(identity and accepted_level),
        "identity_region_score": identity.get("score") if identity and accepted_level else None,
        "level_region_score": level_score if accepted_level else None,
        "identity_font_sizes": identity.get("font_sizes") if identity and accepted_level else None,
        "level_font_size": level_font_size if accepted_level else None,
        "identity_rect": identity_rect if accepted_level else None,
        "level_rect": level_rect if accepted_level else None,
        "reserved_region_count": len(reserved),
        "uses_profile_text_lengths": True,
    }
    return result

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


def _draw_stat_text(
    draw: ImageDraw.ImageDraw,
    stat: dict,
    value: str,
    scale: float,
    text_color: str,
    muted: str,
    *,
    image: Image.Image | None = None,
    dark_text: str = "#101522",
    font_family: str = "modern",
    auto_contrast: bool = True,
) -> None:
    """Draw stat values inside their cells; retain the legacy positional API."""
    try:
        x, y, width, height = (int(stat[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return
    if min(x, y, width, height) < 0 or width <= 0 or height <= 0:
        return
    padding = max(3, int(width * .06))
    available_width = max(1, width - 2 * padding)
    center_x = x + width // 2
    label = str(stat.get("label", ""))
    label_font = _fit_font(
        label, available_width, max(28, min(64, int(height * .36))),
        minimum_size=15, font_family=font_family,
    )
    value_text = str(value)
    value_font = _fit_font(
        value_text, available_width, max(32, min(72, int(height * .43))),
        minimum_size=14, bold=True, font_family=font_family,
    )
    can_sample = image is not None and auto_contrast
    label_color = (
        _contrast_text_color(image, stat, muted, dark_text, stat_cell=True)
        if can_sample else muted
    )
    value_color = (
        _contrast_text_color(image, stat, text_color, dark_text, stat_cell=True)
        if can_sample else text_color
    )
    _draw_display_text(draw, (center_x, y + int(height * .27)), label,
                       font=label_font, fill=label_color, anchor="mm",
                       stroke_width=2, stroke_fill=(0, 0, 0, 175))
    # Never replace a wallet/message/rank value with an ellipsis: fit the font instead.
    _draw_display_text(draw, (center_x, y + int(height * .74)), value_text,
                       font=value_font, fill=value_color, anchor="mm",
                       stroke_width=2, stroke_fill=(0, 0, 0, 190))


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
    style = _parse_object(template.get("style") or template.get("style_json"))
    color_style = _parse_object(profile.get("color_style"))
    style.update(color_style)
    metadata = _apply_smart_template_layout(
        _metadata_for_native_frame(_safe_metadata(template), template_bytes),
        template_bytes,
        profile=profile,
        style=style,
    )
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
    accent2 = _safe_color(style.get("accent2") or style.get("glow") or accent, "#F9F7FF")
    # The settings-table default is white; treat it as auto-contrast unless a
    # real custom color was selected. A template may opt out via style.text_color.
    configured_font_color = str(profile.get("font_color") or "").strip().upper()
    explicit_text_color = bool(style.get("text_color")) or (
        configured_font_color not in {"", "#FFFFFF", "#F9F7FF"}
    )
    light_text = _safe_color(
        profile.get("font_color") or style.get("text_color"),
        _mix_hex(accent2, "#FFFFFF", 0.34),
    )
    muted_light = _mix_hex(style.get("muted") or accent2, "#DCE5F3", 0.42)
    dark_text = _safe_color(style.get("deep"), "#101522")
    font_family = _normalise_font_family(style.get("font_family") or "modern")
    auto_contrast = not explicit_text_color

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
    name_available = _clamp(name_box.get("max_width"), 120, width, int(width * .42))
    name_font = _fit_font(
        name, name_available, max(28, min(64, int(min(width, height) * .056))),
        minimum_size=18, bold=True, font_family=font_family,
    )
    handle_text = handle
    handle_available = _clamp(handle_box.get("max_width"), 100, width, name_available)
    handle_font = _fit_font(
        handle_text, handle_available, max(20, min(36, int(min(width, height) * .034))),
        minimum_size=13, font_family=font_family,
    )
    name_color = (
        light_text if not auto_contrast else _contrast_text_color(
            canvas, name_box, light_text, dark_text,
            line_height=max(18, int(min(width, height) * .065)),
        )
    )
    handle_color = (
        muted_light if not auto_contrast else _contrast_text_color(
            canvas, handle_box, muted_light, dark_text,
            line_height=max(14, int(min(width, height) * .045)),
        )
    )
    name_stroke = (0, 0, 0, 175) if name_color == light_text else (255, 255, 255, 125)
    _draw_display_text(draw, (name_x, name_y), name, font=name_font, fill=name_color,
                        anchor=str(name_box.get("anchor") or "ra"), stroke_width=2,
                        stroke_fill=name_stroke)
    _draw_display_text(draw, (handle_x, handle_y), handle_text, font=handle_font, fill=handle_color,
                        anchor=str(handle_box.get("anchor") or "ra"), stroke_width=1,
                        stroke_fill=(0, 0, 0, 135))

    level_box = metadata.get("level") or {}
    level_x = _scale_position(level_box, "x", inset * 3, width)
    level_y = _scale_position(level_box, "y", int(height * .48), height)
    level = _number(profile.get("level"), 0)
    level_text = f"المستوى {level}"
    level_available = _clamp(level_box.get("max_width"), 100, width, int(width * .44))
    progress_font = _fit_font(
        level_text, level_available, max(30, min(60, int(min(width, height) * .052))),
        minimum_size=18, bold=True, font_family=font_family,
    )
    level_color = (
        light_text if not auto_contrast else _contrast_text_color(
            canvas, level_box, light_text, dark_text,
            line_height=max(22, int(min(width, height) * .065)),
        )
    )
    _draw_display_text(
        draw, (level_x, level_y), level_text,
        font=progress_font, fill=level_color,
        anchor=str(level_box.get("anchor") or "ra"), stroke_width=2,
        stroke_fill=(0, 0, 0, 190),
    )

    # The XP track is part of the template artwork. Do not draw a second progress bar
    # or caption over it; that duplicated the line and collided with the level/stat labels.
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
            _draw_stat_text(
                draw, stat, stat_values[key], scale,
                light_text, muted_light, image=canvas, dark_text=dark_text,
                font_family=font_family, auto_contrast=auto_contrast,
            )

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
                      font=_load_font(max(12, int(badge_size*.58)), bold=True,
                                      font_family=font_family),
                      fill=light_text, anchor="mm")

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
