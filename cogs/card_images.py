"""Bounded, memory-only image downloads for rank cards (including SSRF guards)."""
import asyncio
import io
import ipaddress
import logging
import os
import re
import socket
import threading
import time
import warnings
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp
from PIL import Image, ImageOps

logger = logging.getLogger("RankCard")
MAX_BYTES = 6 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_ANIMATED_FRAMES = 30
MAX_ANIMATED_PIXELS = 24_000_000
CACHE_BYTES = 24 * 1024 * 1024
CACHE_ITEMS = 64
CACHE_TTL = 3600
# Upload references are opaque, guild-scoped identifiers, never filesystem paths.
# Files live under PRIME_LEVEL_CARD_UPLOAD_DIR (or data/level_card_uploads).
_UPLOAD_TOKEN_RE = re.compile(
    r"^upload:(\d{15,22}):([a-f0-9]{32}\.(?:png|gif))$"
)
_cache = OrderedDict()
_cache_lock = threading.Lock()
_pending = {}


def upload_root():
    """Resolve a private upload directory beside the configured database.

    Keeping uploads beside the database makes the default follow the same persistent
    mount (for example DB_PATH=/data/bot.db on Fly). Deployments can override the
    directory explicitly without exposing it to dashboard clients.
    """
    configured = (os.environ.get("PRIME_LEVEL_CARD_UPLOAD_DIR") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    db_path = (os.environ.get("DB_PATH") or "").strip()
    if not db_path:
        # Match database.DB_NAME's legacy filename fallback so existing deployments
        # don't split their durable database and uploaded backgrounds across roots.
        if os.path.exists("bot_database.db") and not os.path.exists("data/bot.db"):
            db_path = "bot_database.db"
        else:
            db_path = "data/bot.db"
    return (Path(db_path).expanduser().resolve().parent / "level_card_uploads").resolve()


def uploaded_background_path(reference, guild_id=None):
    """Resolve a validated upload token to a file owned by exactly one guild."""
    match = _UPLOAD_TOKEN_RE.fullmatch(str(reference or ""))
    if not match:
        raise ValueError("invalid uploaded card background reference")
    owner_id = int(match.group(1))
    if guild_id is not None and owner_id != int(guild_id):
        raise ValueError("uploaded card background belongs to another guild")
    root = upload_root()
    path = (root / f"{owner_id}-{match.group(2)}").resolve()
    if path.parent != root or not path.is_file():
        raise ValueError("uploaded card background is missing")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("uploaded card background exceeds the byte limit")
    return path


def is_uploaded_background(reference, guild_id=None, require_exists=True):
    """Check token shape and, optionally, that its guild-scoped file still exists."""
    match = _UPLOAD_TOKEN_RE.fullmatch(str(reference or ""))
    if not match:
        return False
    if guild_id is not None and int(match.group(1)) != int(guild_id):
        return False
    if not require_exists:
        return True
    try:
        uploaded_background_path(reference, guild_id)
    except (OSError, TypeError, ValueError, OverflowError):
        return False
    return True


def validate_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("image URL must be HTTP(S)")
    if parts.username or parts.password or parts.port not in {None, 80, 443}:
        raise ValueError("unsupported image URL authority")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        if parts.hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("private image host")
    else:
        if not address.is_global or address.is_multicast:
            raise ValueError("private image address")
    return url


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate DNS answers used by the connector, avoiding DNS-rebinding races."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, family=family, type=socket.SOCK_STREAM)
        answers = []
        for af, _, proto, _, address in records:
            ip = address[0]
            address = ipaddress.ip_address(ip)
            if not address.is_global or address.is_multicast:
                raise ValueError("image DNS resolved to a private address")
            answers.append({"hostname": host, "host": ip, "port": port,
                            "family": af, "proto": proto, "flags": socket.AI_NUMERICHOST})
        return answers

    async def close(self):
        pass


def normalize_image(data):
    """Validate images and preserve GIF motion within bounded render limits.

    Animated GIFs that exceed the renderer's frame/pixel budget are sampled and
    resized while remaining animated. They are never silently flattened to PNG.
    """
    if not data or len(data) > MAX_BYTES:
        raise ValueError("image byte limit")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise ValueError("unsupported image format")
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("image pixel limit")

            frame_count = int(getattr(source, "n_frames", 1) or 1)
            if source.format == "GIF" and frame_count > 1:
                target_count = min(frame_count, MAX_ANIMATED_FRAMES)
                indices = [
                    round(index * (frame_count - 1) / (target_count - 1))
                    for index in range(target_count)
                ] if target_count > 1 else [0]
                durations = []
                for index in range(frame_count):
                    source.seek(index)
                    try:
                        durations.append(max(10, int(source.info.get("duration", 85))))
                    except (TypeError, ValueError):
                        durations.append(85)

                target_pixels = max(1, min(MAX_PIXELS, MAX_ANIMATED_PIXELS // target_count))
                scale = min(1.0, (target_pixels / (source.width * source.height)) ** 0.5)
                size = (
                    max(1, int(source.width * scale)),
                    max(1, int(source.height * scale)),
                )
                frames = []
                sampled_durations = []
                for position, index in enumerate(indices):
                    source.seek(index)
                    frame = source.convert("RGB")
                    if frame.size != size:
                        frame = frame.resize(size, Image.Resampling.LANCZOS)
                    frames.append(frame.copy())
                    stop = indices[position + 1] if position + 1 < len(indices) else frame_count
                    sampled_durations.append(max(10, sum(durations[index:stop])))

                output = io.BytesIO()
                frames[0].save(
                    output,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=sampled_durations,
                    loop=source.info.get("loop", 0),
                    disposal=2,
                    optimize=True,
                )
                normalized = output.getvalue()
                if len(normalized) > MAX_BYTES:
                    raise ValueError(
                        "animated GIF remains too large after safe optimization; "
                        "try a shorter or lower-resolution GIF"
                    )
                return normalized

            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            result = io.BytesIO()
            image.save(result, format="PNG")
            return result.getvalue()


async def _download(url):
    connector = aiohttp.TCPConnector(resolver=PublicResolver(), limit=4)
    timeout = aiohttp.ClientTimeout(total=8, connect=3)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout, trust_env=False) as session:
        for _ in range(4):
            validate_url(url)
            async with session.get(url, allow_redirects=False) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                if response.content_length and response.content_length > MAX_BYTES:
                    raise ValueError("image byte limit")
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > MAX_BYTES:
                        raise ValueError("image byte limit")
                return await asyncio.to_thread(normalize_image, bytes(data))
    raise ValueError("too many image redirects")


async def _load(url):
    try:
        if _UPLOAD_TOKEN_RE.fullmatch(str(url or "")):
            path = uploaded_background_path(url)
            raw = await asyncio.to_thread(path.read_bytes)
            # Validate the stored bytes just as rigorously as remote assets; this
            # work happens once per cache miss, not for every rendered card.
            data = await asyncio.to_thread(normalize_image, raw)
        else:
            validate_url(url)
            data = await _download(url)
        ttl = CACHE_TTL
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        # Do not log arbitrary URLs (which can contain signed query strings).
        logger.debug("Rank-card image unavailable; using generated fallback", exc_info=False)
        data, ttl = None, 30
    with _cache_lock:
        _cache[url] = (time.monotonic() + ttl, data)
        _cache.move_to_end(url)
        while len(_cache) > CACHE_ITEMS or sum(len(item[1] or b"") for item in _cache.values()) > CACHE_BYTES:
            _cache.popitem(last=False)
    return data


async def fetch_image(url):
    if not isinstance(url, str) or not url or len(url) > 4096:
        return None
    with _cache_lock:
        cached = _cache.get(url)
        if cached and cached[0] > time.monotonic():
            _cache.move_to_end(url)
            return cached[1]
        _cache.pop(url, None)
    key = (asyncio.get_running_loop(), url)
    if key not in _pending:
        task = asyncio.create_task(_load(url))
        _pending[key] = task
        task.add_done_callback(lambda completed: _pending.pop(key, None))
    return await asyncio.shield(_pending[key])