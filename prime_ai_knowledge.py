"""Server-scoped PRIME AI knowledge sources and lexical retrieval.

Sources are supplied by a server manager. URLs are citation metadata only; this
module never fetches user-supplied URLs, which avoids turning source import into
an SSRF-capable network proxy.
"""
from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit

import aiosqlite
import database

MAX_SOURCES_PER_GUILD = 50
MAX_TITLE_CHARS = 160
MAX_SOURCE_URL_CHARS = 500
MAX_CONTENT_CHARS = 30_000
MAX_RESULTS = 4
MAX_CHUNK_CHARS = 1_600
_CHUNK_OVERLAP = 160
_SCHEMA_LOCK = asyncio.Lock()
_SCHEMA_DATABASE = None

_ARABIC_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u06d6-\u06ed]")
_TOKEN_RE = re.compile(r"[a-zA-Z0-9\u0600-\u06ff]+")
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
    "i", "in", "is", "it", "of", "on", "or", "the", "to", "what", "when",
    "where", "which", "who", "why", "with", "you", "your",
    "الى", "إلى", "عن", "على", "في", "من", "هو", "هي", "هم", "هن", "هذا",
    "هذه", "ذلك", "تلك", "ما", "ماذا", "كيف", "هل", "لماذا", "متى", "أين",
    "ايش", "وش", "وين", "كم", "كان", "كانت", "يكون", "تكون", "و", "او",
    "ثم", "إن", "أن", "اذا", "إذا", "مع", "كل", "قد", "بس", "لكن",
}


class KnowledgeBaseError(ValueError):
    """A safe, user-displayable knowledge-base validation error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dict(row) -> dict:
    return dict(row) if row is not None else {}


async def _ensure_schema() -> None:
    global _SCHEMA_DATABASE
    database_key = str(getattr(database, "DB_NAME", "default"))
    if _SCHEMA_DATABASE == database_key:
        return
    async with _SCHEMA_LOCK:
        if _SCHEMA_DATABASE == database_key:
            return
        async with database.connect(aiosqlite.Row) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS prime_ai_knowledge_sources (
                    source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source_url TEXT NOT NULL DEFAULT '',
                    content TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_prime_ai_knowledge_guild "
                "ON prime_ai_knowledge_sources(guild_id, enabled, updated_at)"
            )
            await db.commit()
        _SCHEMA_DATABASE = database_key


def _validated_url(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise KnowledgeBaseError("invalid_knowledge_source_url")
    value = value.strip()
    if len(value) > MAX_SOURCE_URL_CHARS:
        raise KnowledgeBaseError("knowledge_source_url_too_long")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        # A URL is shown as a citation only; the server never requests it.
        if (
            parsed.scheme.lower() not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError
        _ = parsed.port
    except (TypeError, ValueError):
        raise KnowledgeBaseError("invalid_knowledge_source_url") from None
    return value


def validate_source(title, content, source_url="") -> dict:
    if not isinstance(title, str):
        raise KnowledgeBaseError("invalid_knowledge_title")
    clean_title = " ".join(title.split()).strip()
    if not clean_title or len(clean_title) > MAX_TITLE_CHARS:
        raise KnowledgeBaseError("invalid_knowledge_title")
    if not isinstance(content, str):
        raise KnowledgeBaseError("invalid_knowledge_content")
    clean_content = content.replace("\r\n", "\n").replace("\r", "\n")
    clean_content = clean_content.replace("\x00", "").strip()
    if not clean_content:
        raise KnowledgeBaseError("invalid_knowledge_content")
    if len(clean_content) > MAX_CONTENT_CHARS:
        raise KnowledgeBaseError("knowledge_content_too_long")
    return {
        "title": clean_title,
        "content": clean_content,
        "source_url": _validated_url(source_url),
    }


def _public(row, *, include_content=False) -> dict:
    item = _dict(row)
    source = {
        "source_id": int(item["source_id"]),
        "title": str(item["title"]),
        "source_url": str(item.get("source_url") or ""),
        "content_length": len(str(item.get("content") or "")),
        "excerpt": str(item.get("content") or "")[:220],
        "enabled": bool(item.get("enabled", 1)),
        "created_by": str(item.get("created_by") or ""),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    if include_content:
        source["content"] = str(item.get("content") or "")
    return source


async def list_sources(guild_id: int, *, limit: int = MAX_SOURCES_PER_GUILD) -> list[dict]:
    await _ensure_schema()
    safe_limit = max(1, min(int(limit), MAX_SOURCES_PER_GUILD))
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT source_id, title, source_url, content, enabled, created_by,
                   created_at, updated_at
              FROM prime_ai_knowledge_sources
             WHERE guild_id = ?
             ORDER BY updated_at DESC, source_id DESC
             LIMIT ?
            """,
            (int(guild_id), safe_limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_public(row) for row in rows]


async def get_source(guild_id: int, source_id: int) -> dict | None:
    await _ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT source_id, title, source_url, content, enabled, created_by,
                   created_at, updated_at
              FROM prime_ai_knowledge_sources
             WHERE guild_id = ? AND source_id = ?
            """,
            (int(guild_id), int(source_id)),
        ) as cur:
            row = await cur.fetchone()
    return _public(row, include_content=True) if row else None


async def create_source(
    guild_id: int, actor_id: int, title, content, source_url="", *, enabled=True
) -> dict:
    clean = validate_source(title, content, source_url)
    if not isinstance(enabled, bool):
        raise KnowledgeBaseError("invalid_knowledge_enabled")
    await _ensure_schema()
    now = _now()
    async with database.connect(aiosqlite.Row) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_knowledge_sources WHERE guild_id = ?",
            (int(guild_id),),
        ) as cur:
            count = int((await cur.fetchone())[0])
        if count >= MAX_SOURCES_PER_GUILD:
            await db.rollback()
            raise KnowledgeBaseError("knowledge_source_limit_reached")
        cursor = await db.execute(
            """
            INSERT INTO prime_ai_knowledge_sources
                (guild_id, title, source_url, content, enabled, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(guild_id), clean["title"], clean["source_url"], clean["content"],
                int(enabled), int(actor_id), now, now,
            ),
        )
        source_id = int(cursor.lastrowid)
        await db.commit()
    result = await get_source(guild_id, source_id)
    if not result:
        raise RuntimeError("knowledge_source_insert_not_found")
    return result


async def update_source(
    guild_id: int, source_id: int, title, content, source_url="", *, enabled=True
) -> dict | None:
    clean = validate_source(title, content, source_url)
    if not isinstance(enabled, bool):
        raise KnowledgeBaseError("invalid_knowledge_enabled")
    await _ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            """
            UPDATE prime_ai_knowledge_sources
               SET title = ?, source_url = ?, content = ?, enabled = ?, updated_at = ?
             WHERE guild_id = ? AND source_id = ?
            """,
            (
                clean["title"], clean["source_url"], clean["content"], int(enabled),
                _now(), int(guild_id), int(source_id),
            ),
        )
        await db.commit()
        if cursor.rowcount != 1:
            return None
    return await get_source(guild_id, source_id)


async def delete_source(guild_id: int, source_id: int) -> bool:
    await _ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        cursor = await db.execute(
            "DELETE FROM prime_ai_knowledge_sources WHERE guild_id = ? AND source_id = ?",
            (int(guild_id), int(source_id)),
        )
        await db.commit()
    return cursor.rowcount == 1


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    value = _ARABIC_DIACRITICS.sub("", value)
    return value.translate(str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي",
    }))


def _terms(value: str) -> list[str]:
    return [
        token for token in _TOKEN_RE.findall(_normalize_text(value))
        if token not in _STOP_WORDS and len(token) > 1
    ]


def _chunks(content: str):
    """Yield bounded overlapping paragraphs so retrieval retains local context."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", content) if part.strip()]
    if not paragraphs:
        paragraphs = [content.strip()]
    for paragraph in paragraphs:
        if len(paragraph) <= MAX_CHUNK_CHARS:
            yield paragraph
            continue
        start = 0
        while start < len(paragraph):
            end = min(len(paragraph), start + MAX_CHUNK_CHARS)
            if end < len(paragraph):
                boundary = paragraph.rfind(" ", start + MAX_CHUNK_CHARS // 2, end)
                if boundary > start:
                    end = boundary
            yield paragraph[start:end].strip()
            if end >= len(paragraph):
                break
            start = max(start + 1, end - _CHUNK_OVERLAP)


def rank_sources(query: str, sources: list[dict], *, limit: int = MAX_RESULTS) -> list[dict]:
    """Rank at most one relevant excerpt per source using deterministic lexical overlap."""
    query_terms = set(_terms(query))
    if not query_terms:
        return []
    minimum_matches = 1 if len(query_terms) <= 2 else 2
    ranked = []
    for source in sources:
        if not source.get("enabled", True):
            continue
        title_terms = set(_terms(str(source.get("title", ""))))
        best = None
        for chunk in _chunks(str(source.get("content", ""))):
            chunk_terms = set(_terms(chunk))
            matches = query_terms & chunk_terms
            if len(matches) < minimum_matches:
                continue
            title_matches = query_terms & title_terms
            coverage = len(matches) / max(1, len(query_terms))
            title_boost = len(title_matches) * 1.25
            normalized_query = _normalize_text(query).strip()
            phrase_boost = (
                1.25
                if len(normalized_query) >= 5 and normalized_query in _normalize_text(chunk)
                else 0
            )
            score = coverage * 4 + title_boost + phrase_boost
            candidate = {
                "source_id": int(source["source_id"]),
                "title": str(source["title"])[:MAX_TITLE_CHARS],
                "source_url": str(source.get("source_url") or ""),
                "excerpt": chunk[:MAX_CHUNK_CHARS],
                "score": round(score, 4),
            }
            if best is None or candidate["score"] > best["score"]:
                best = candidate
        if best:
            ranked.append(best)
    ranked.sort(key=lambda item: (-item["score"], item["source_id"]))
    results = ranked[:max(1, min(int(limit), MAX_RESULTS))]
    for index, item in enumerate(results, 1):
        item["citation_id"] = f"KB{index}"
    return results


async def retrieve(guild_id: int, query: str, *, limit: int = MAX_RESULTS) -> list[dict]:
    if not isinstance(query, str) or not query.strip():
        return []
    await _ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            """
            SELECT source_id, title, source_url, content, enabled
              FROM prime_ai_knowledge_sources
             WHERE guild_id = ? AND enabled = 1
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            (int(guild_id), MAX_SOURCES_PER_GUILD),
        ) as cur:
            rows = await cur.fetchall()
    candidates = [
        {
            "source_id": int(row["source_id"]),
            "title": str(row["title"]),
            "source_url": str(row["source_url"] or ""),
            "content": str(row["content"] or ""),
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]
    return rank_sources(query, candidates, limit=limit)


def _escape_title(value: str) -> str:
    return re.sub(r"([\\\[\]()*_])", r"\\\1", str(value or "")[:MAX_TITLE_CHARS])


def format_cited_answer(answer: str, sources: list[dict], *, max_length: int = 3500) -> str:
    """Resolve only known citation IDs and append a compact, source-linked footer."""
    value = str(answer or "").strip()
    if not sources:
        return value[:max(1, int(max_length))]
    by_id = {str(item.get("citation_id", "")): item for item in sources}
    cited = []

    def replace_citation(match):
        citation_id = match.group(1)
        item = by_id.get(citation_id)
        if not item:
            return ""
        if item not in cited:
            cited.append(item)
        return f"〔{_escape_title(item.get('title', 'مصدر'))}〕"

    value = re.sub(r"\[(KB\d{1,2})\]", replace_citation, value)
    displayed = cited or sources[:min(3, len(sources))]
    if not displayed:
        return value[:max(1, int(max_length))]
    lines = ["**المصادر:**" if cited else "**مصادر ذات صلة:**"]
    for item in displayed[:3]:
        title = _escape_title(item.get("title", "مصدر"))
        url = str(item.get("source_url") or "")
        number = str(item.get("citation_id") or "")
        if url:
            safe_url = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            lines.append(f"- [{title}](<{safe_url}>)" + (f" — {number}" if number else ""))
        else:
            lines.append(f"- **{title}** — مصدر من قاعدة معرفة الخادم" + (f" ({number})" if number else ""))
    footer = "\n\n" + "\n".join(lines)
    ceiling = max(256, min(int(max_length), 4096))
    available = max(128, ceiling - len(footer))
    if len(value) > available:
        value = value[:max(1, available - 1)].rstrip() + "…"
    return (value + footer)[:ceiling]
