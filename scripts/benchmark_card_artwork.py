"""Compare uncached SVG rasterization with PRIME's cached artwork renderer.

Run from the repository root:
    python scripts/benchmark_card_artwork.py --iterations 20

This is a diagnostic benchmark, not a timing-sensitive CI test. Results vary by
host, CPU load, and filesystem cache state.
"""
import argparse
import statistics
import time
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image

import cogs.card_artwork as artwork
from cogs.card_generator import LAYOUTS


def uncached_render(layout, size):
    """Perform the previous uncached SVG-to-PIL path for a fair comparison."""
    filename = artwork._LAYOUT_ART[layout]
    path = artwork._ART / filename
    png = cairosvg.svg2png(
        url=str(path),
        output_width=max(1, int(size[0])),
        output_height=max(1, int(size[1])),
    )
    with Image.open(BytesIO(png)) as rendered:
        return rendered.convert("RGB").copy()


def measure(call, iterations):
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        image = call()
        elapsed = time.perf_counter() - start
        if image is None:
            raise RuntimeError("Artwork render unexpectedly returned None")
        samples.append(elapsed)
    return statistics.median(samples) * 1000


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20,
                        help="iterations per measurement (default: 20)")
    parser.add_argument("--layout", choices=sorted(artwork._LAYOUT_ART),
                        help="benchmark one layout; default benchmarks all layouts")
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("--iterations must be at least 2")

    layouts = [args.layout] if args.layout else sorted(artwork._LAYOUT_ART)
    print("PRIME rank-card artwork benchmark")
    print(f"Iterations per measurement: {args.iterations}")
    print("Times are median milliseconds per render; lower is better.\n")
    print(f"{'Layout':<12} {'Size':<12} {'Uncached ms':>13} {'Cached warm ms':>16} {'Speedup':>10}")
    print("-" * 68)

    for layout in layouts:
        size = LAYOUTS[layout]
        artwork._rasterize_artwork.cache_clear()

        # Warm the cache once, outside the timed warm-cache samples.
        artwork.render_card_artwork(layout, size)
        uncached_ms = measure(lambda: uncached_render(layout, size), args.iterations)
        cached_ms = measure(lambda: artwork.render_card_artwork(layout, size), args.iterations)
        speedup = uncached_ms / cached_ms if cached_ms else float("inf")
        print(f"{layout:<12} {size[0]}x{size[1]:<7} {uncached_ms:>13.3f} "
              f"{cached_ms:>16.3f} {speedup:>9.2f}x")

    info = artwork._rasterize_artwork.cache_info()
    print(f"\nCache summary: hits={info.hits}, misses={info.misses}, "
          f"entries={info.currsize}/{info.maxsize}")
    print("Note: compare results on the same machine under similar load; "
          "this script intentionally does not enforce a speedup threshold.")


if __name__ == "__main__":
    main()
