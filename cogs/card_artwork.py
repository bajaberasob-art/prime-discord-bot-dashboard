"""Bundled, authored SVG scenes for PRIME rank-card backgrounds.

Artwork is local and deterministic: rendering does not make network requests.
Add an SVG under assets/card_artwork and map one or more layouts below.
"""
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image

_ROOT = Path(__file__).resolve().parents[1]
_ART = _ROOT / "assets" / "card_artwork"
_LAYOUT_ART = {
    "showcase": "showcase-celestial.svg",
    "vertical": "vertical-aurora-tower.svg",
    "spotlight": "spotlight-astral.svg",
    "ring": "ring-eclipse.svg",
    "square": "square-prism-city.svg",
    "banner": "banner-neon-ridge.svg",
    "classic": "classic-gold-horizon.svg",
    "minimal": "minimal-ice-line.svg",
    "stats": "stats-golden-nebula.svg",
}


@lru_cache(maxsize=24)
def _rasterize_artwork(filename, width, height, modified_ns):
    """Cache immutable PNG bytes, not mutable PIL images, for safe reuse."""
    del modified_ns  # Included in the cache key to invalidate changed SVG assets.
    path = _ART / filename
    return cairosvg.svg2png(
        url=str(path),
        output_width=width,
        output_height=height,
    )


def render_card_artwork(layout, size):
    """Return a rasterized local artwork image, or None for an unknown/missing asset."""
    filename = _LAYOUT_ART.get(str(layout))
    if not filename:
        return None
    path = _ART / filename
    try:
        modified_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    width, height = (max(1, int(size[0])), max(1, int(size[1])))
    try:
        png = _rasterize_artwork(filename, width, height, modified_ns)
        with Image.open(BytesIO(png)) as rendered:
            return rendered.convert("RGB").copy()
    except (OSError, ValueError, cairosvg.CairoSVGError):
        return None
