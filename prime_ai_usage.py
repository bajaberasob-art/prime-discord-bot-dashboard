"""Per-guild PRIME AI quotas, cost estimates, and reliability alerts."""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
import database

_SETTING_FIELDS = {
    "monthly_request_limit", "monthly_budget_usd", "usd_per_million_tokens", "usd_per_image",
    "request_spike_threshold", "provider_failure_threshold", "alerts_enabled",
}
DEFAULT_SETTINGS = {
    "monthly_request_limit": 0,
    "monthly_budget_usd": 0.0,
    "usd_per_million_tokens": 0.0,
    "usd_per_image": 0.0,
    "request_spike_threshold": 30,
    "provider_failure_threshold": 3,
    "alerts_enabled": True,
}
_ALERT_COOLDOWN_MINUTES = 30
_SCHEMA_READY_FOR = None
_SCHEMA_LOCK = None
_SCHEMA_LOCK_LOOP = None


class UsageSettingsConflict(ValueError):
    """Settings revision changed since the dashboard loaded them."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


def month_start(now: datetime | None = None) -> str:
    current = now or _now()
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def estimate_cost(
    tokens: int | None,
    usd_per_million_tokens: float,
    images: int = 0,
    usd_per_image: float = 0.0,
) -> float | None:
    """Estimate tokens and images using administrator-supplied rates."""
    token_rate = float(usd_per_million_tokens or 0)
    image_rate = float(usd_per_image or 0)
    image_count = max(0, int(images or 0))
    if token_rate <= 0 and image_rate <= 0:
        return None
    if token_rate > 0 and tokens is None:
        return None
    token_cost = max(0, int(tokens or 0)) * token_rate / 1_000_000 if token_rate > 0 else 0.0
    image_cost = image_count * image_rate
    return round(token_cost + image_cost, 6)


def normalize_settings(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("invalid_usage_settings")
    if set(values) - _SETTING_FIELDS:
        raise ValueError("unknown_usage_setting")
    result = dict(DEFAULT_SETTINGS)
    result.update(values)

    def integer(key: str, low: int, high: int) -> int:
        value = result[key]
        if isinstance(value, bool):
            raise ValueError("invalid_" + key)
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_" + key) from None
        if str(value).strip() not in {str(number), str(number) + ".0"} or not low <= number <= high:
            raise ValueError("invalid_" + key)
        return number

    def money(key: str, low: float, high: float) -> float:
        value = result[key]
        if isinstance(value, bool):
            raise ValueError("invalid_" + key)
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("invalid_" + key) from None
        if not math.isfinite(number) or not low <= number <= high:
            raise ValueError("invalid_" + key)
        return round(number, 6)

    result["monthly_request_limit"] = integer("monthly_request_limit", 0, 10_000_000)
    result["request_spike_threshold"] = integer("request_spike_threshold", 1, 100_000)
    result["provider_failure_threshold"] = integer("provider_failure_threshold", 1, 10_000)
    result["monthly_budget_usd"] = money("monthly_budget_usd", 0, 10_000_000)
    result["usd_per_million_tokens"] = money("usd_per_million_tokens", 0, 10_000)
    result["usd_per_image"] = money("usd_per_image", 0, 100)
    if not isinstance(result["alerts_enabled"], bool):
        raise ValueError("invalid_alerts_enabled")
    return result


async def _ensure_schema(db) -> None:
    await db.execute("""CREATE TABLE IF NOT EXISTS prime_ai_usage_settings (
        guild_id INTEGER PRIMARY KEY,
        monthly_request_limit INTEGER NOT NULL DEFAULT 0,
        monthly_budget_usd REAL NOT NULL DEFAULT 0,
        usd_per_million_tokens REAL NOT NULL DEFAULT 0,
        usd_per_image REAL NOT NULL DEFAULT 0,
        request_spike_threshold INTEGER NOT NULL DEFAULT 30,
        provider_failure_threshold INTEGER NOT NULL DEFAULT 3,
        alerts_enabled INTEGER NOT NULL DEFAULT 1,
        revision INTEGER NOT NULL DEFAULT 0,
        updated_by INTEGER,
        updated_at TEXT
    )""")
    async with db.execute("PRAGMA table_info(prime_ai_usage_settings)") as cur:
        setting_columns = {str(row[1]) for row in await cur.fetchall()}
    if "usd_per_image" not in setting_columns:
        await db.execute("ALTER TABLE prime_ai_usage_settings ADD COLUMN usd_per_image REAL NOT NULL DEFAULT 0")
    await db.execute("""CREATE TABLE IF NOT EXISTS prime_ai_usage_alerts (
        alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        alert_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'warning',
        title TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        occurrences INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    )""")
    async with db.execute("PRAGMA table_info(prime_ai_request_events)") as cur:
        columns = {str(row[1]) for row in await cur.fetchall()}
    if "failure_reason" not in columns:
        await db.execute(
            "ALTER TABLE prime_ai_request_events "
            "ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''"
        )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_prime_ai_usage_alerts_guild "
        "ON prime_ai_usage_alerts(guild_id, alert_id DESC)"
    )


async def ensure_schema() -> None:
    global _SCHEMA_READY_FOR, _SCHEMA_LOCK, _SCHEMA_LOCK_LOOP
    path = str(getattr(database, "DB_NAME", ""))
    if _SCHEMA_READY_FOR == path:
        return
    loop = asyncio.get_running_loop()
    if _SCHEMA_LOCK is None or _SCHEMA_LOCK_LOOP is not loop:
        _SCHEMA_LOCK = asyncio.Lock()
        _SCHEMA_LOCK_LOOP = loop
    async with _SCHEMA_LOCK:
        if _SCHEMA_READY_FOR == path:
            return
        async with database.connect() as db:
            await _ensure_schema(db)
            await db.commit()
        _SCHEMA_READY_FOR = path


def _settings_from_row(row) -> dict[str, Any]:
    if row is None:
        result = dict(DEFAULT_SETTINGS)
        result.update({"revision": 0, "updated_by": None, "updated_at": None})
        return result
    return {
        "monthly_request_limit": int(row["monthly_request_limit"] or 0),
        "monthly_budget_usd": float(row["monthly_budget_usd"] or 0),
        "usd_per_million_tokens": float(row["usd_per_million_tokens"] or 0),
        "usd_per_image": float(row["usd_per_image"] or 0),
        "request_spike_threshold": int(row["request_spike_threshold"] or 30),
        "provider_failure_threshold": int(row["provider_failure_threshold"] or 3),
        "alerts_enabled": bool(row["alerts_enabled"]),
        "revision": int(row["revision"] or 0),
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


async def get_settings(guild_id: int) -> dict[str, Any]:
    await ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM prime_ai_usage_settings WHERE guild_id=?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
    return _settings_from_row(row)


async def save_settings(
    guild_id: int,
    values: dict[str, Any],
    *,
    updated_by: int | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    clean = normalize_settings(values)
    await ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT revision FROM prime_ai_usage_settings WHERE guild_id=?",
            (int(guild_id),),
        ) as cur:
            row = await cur.fetchone()
        revision = int(row["revision"]) if row else 0
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision != revision
        ):
            raise UsageSettingsConflict("usage_settings_conflict")
        next_revision = revision + 1
        await db.execute(
            "INSERT INTO prime_ai_usage_settings "
            "(guild_id,monthly_request_limit,monthly_budget_usd,usd_per_million_tokens,usd_per_image,"
            "request_spike_threshold,provider_failure_threshold,alerts_enabled,revision,updated_by,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET "
            "monthly_request_limit=excluded.monthly_request_limit,"
            "monthly_budget_usd=excluded.monthly_budget_usd,"
            "usd_per_million_tokens=excluded.usd_per_million_tokens,usd_per_image=excluded.usd_per_image,"
            "request_spike_threshold=excluded.request_spike_threshold,"
            "provider_failure_threshold=excluded.provider_failure_threshold,"
            "alerts_enabled=excluded.alerts_enabled,revision=excluded.revision,"
            "updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (
                int(guild_id), clean["monthly_request_limit"], clean["monthly_budget_usd"],
                clean["usd_per_million_tokens"], clean["usd_per_image"], clean["request_spike_threshold"],
                clean["provider_failure_threshold"], int(clean["alerts_enabled"]),
                next_revision, int(updated_by) if updated_by is not None else None, _stamp(),
            ),
        )
        await db.commit()
    return await get_snapshot(guild_id)


def _alert_candidates(snapshot: dict) -> list[dict[str, str]]:
    settings = snapshot["settings"]
    monthly = snapshot["monthly"]
    candidates = []
    spike_limit = settings["request_spike_threshold"]
    if spike_limit and snapshot["requests_10m"] >= spike_limit:
        candidates.append({
            "type": "request_spike", "severity": "warning",
            "title": "ارتفاع مفاجئ في طلبات PRIME",
            "detail": str(snapshot["requests_10m"]) + " طلب خلال 10 دقائق؛ حد التنبيه " + str(spike_limit) + ".",
        })
    failure_limit = settings["provider_failure_threshold"]
    if failure_limit and snapshot["provider_failures_30m"] >= failure_limit:
        candidates.append({
            "type": "provider_failures", "severity": "critical",
            "title": "تكرار فشل مزوّد الذكاء الاصطناعي",
            "detail": str(snapshot["provider_failures_30m"]) + " حالات فشل خلال 30 دقيقة؛ راجع أسباب الفشل أدناه.",
        })
    request_limit = settings["monthly_request_limit"]
    requests = int(monthly["requests"])
    if request_limit and requests >= request_limit:
        candidates.append({
            "type": "monthly_requests_limit", "severity": "critical",
            "title": "تم بلوغ الحصة الشهرية للطلبات",
            "detail": str(requests) + " من " + str(request_limit) + " طلب شهري.",
        })
    elif request_limit and requests >= math.ceil(request_limit * 0.8):
        candidates.append({
            "type": "monthly_requests_warning", "severity": "warning",
            "title": "اقترب PRIME من حصة الطلبات الشهرية",
            "detail": str(requests) + " من " + str(request_limit) + " طلب شهري (80% أو أكثر).",
        })
    budget = float(settings["monthly_budget_usd"])
    cost = monthly["estimated_cost_usd"]
    if budget and cost is not None and cost >= budget:
        candidates.append({
            "type": "monthly_budget_limit", "severity": "critical",
            "title": "تم بلوغ ميزانية PRIME الشهرية",
            "detail": "التكلفة المقدّرة $" + format(cost, ".4f") + " من ميزانية $" + format(budget, ".2f") + ".",
        })
    elif budget and cost is not None and cost >= budget * 0.8:
        candidates.append({
            "type": "monthly_budget_warning", "severity": "warning",
            "title": "اقترب PRIME من الميزانية الشهرية",
            "detail": "التكلفة المقدّرة $" + format(cost, ".4f") + " من ميزانية $" + format(budget, ".2f") + " (80% أو أكثر).",
        })
    return candidates


async def get_snapshot(guild_id: int) -> dict[str, Any]:
    await ensure_schema()
    now = _now()
    start = month_start(now)
    ten_minutes = _stamp(now - timedelta(minutes=10))
    thirty_minutes = _stamp(now - timedelta(minutes=30))
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT * FROM prime_ai_usage_settings WHERE guild_id=?",
            (int(guild_id),),
        ) as cur:
            settings = _settings_from_row(await cur.fetchone())
        async with db.execute(
            "SELECT COUNT(*) AS requests,SUM(CASE WHEN result='success' THEN 1 ELSE 0 END) AS successful,"
            "SUM(CASE WHEN result='failed' THEN 1 ELSE 0 END) AS failed,"
            "SUM(COALESCE(tokens_used,0)) AS tokens,AVG(latency_ms) AS avg_latency_ms "
            "FROM prime_ai_request_events WHERE guild_id=? AND created_at>=?",
            (int(guild_id), start),
        ) as cur:
            row = dict(await cur.fetchone())
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_request_events WHERE guild_id=? AND created_at>=?",
            (int(guild_id), ten_minutes),
        ) as cur:
            requests_10m = int((await cur.fetchone())[0] or 0)
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_request_events WHERE guild_id=? AND result='failed' AND created_at>=?",
            (int(guild_id), thirty_minutes),
        ) as cur:
            failures_30m = int((await cur.fetchone())[0] or 0)
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_request_events "
            "WHERE guild_id=? AND skill='image_generation' AND created_at>=?",
            (int(guild_id), start),
        ) as cur:
            monthly_images = int((await cur.fetchone())[0] or 0)
        async with db.execute(
            "SELECT skill,COUNT(*) AS requests FROM prime_ai_request_events "
            "WHERE guild_id=? AND created_at>=? GROUP BY skill ORDER BY requests DESC LIMIT 8",
            (int(guild_id), start),
        ) as cur:
            skills = [dict(item) for item in await cur.fetchall()]
        async with db.execute(
            "SELECT failure_reason,COUNT(*) AS occurrences FROM prime_ai_request_events "
            "WHERE guild_id=? AND result='failed' AND created_at>=? "
            "GROUP BY failure_reason ORDER BY occurrences DESC LIMIT 8",
            (int(guild_id), start),
        ) as cur:
            failures = [
                {"reason": str(item["failure_reason"] or "سبب غير مسجل"),
                 "occurrences": int(item["occurrences"] or 0)}
                for item in await cur.fetchall()
            ]
        async with db.execute(
            "SELECT operation_id,request,skill,status,error,created_at,updated_at "
            "FROM prime_ai_operations WHERE guild_id=? AND status='FAILED' "
            "ORDER BY updated_at DESC LIMIT 8",
            (int(guild_id),),
        ) as cur:
            action_failures = [dict(item) for item in await cur.fetchall()]
        async with db.execute(
            "SELECT alert_id,alert_type,severity,title,detail,occurrences,created_at,last_seen_at "
            "FROM prime_ai_usage_alerts WHERE guild_id=? ORDER BY alert_id DESC LIMIT 10",
            (int(guild_id),),
        ) as cur:
            alerts = [dict(item) for item in await cur.fetchall()]
    tokens = int(row.get("tokens") or 0)
    monthly = {
        "requests": int(row.get("requests") or 0),
        "successful": int(row.get("successful") or 0),
        "failed": int(row.get("failed") or 0),
        "tokens": tokens,
        "avg_latency_ms": round(float(row.get("avg_latency_ms") or 0), 1),
        "images": monthly_images,
        "estimated_cost_usd": estimate_cost(tokens, settings["usd_per_million_tokens"], monthly_images, settings["usd_per_image"]),
    }
    return {
        "settings": settings, "monthly": monthly, "requests_10m": requests_10m,
        "provider_failures_30m": failures_30m, "skills": skills,
        "failure_reasons": failures, "action_failures": action_failures,
        "alerts": alerts, "period_start": start, "cost_is_estimate": True,
    }


async def evaluate_alerts(guild_id: int) -> None:
    snapshot = await get_snapshot(guild_id)
    if not snapshot["settings"]["alerts_enabled"]:
        return
    now = _stamp()
    cutoff = _stamp(_now() - timedelta(minutes=_ALERT_COOLDOWN_MINUTES))
    async with database.connect(aiosqlite.Row) as db:
        for candidate in _alert_candidates(snapshot):
            async with db.execute(
                "SELECT alert_id,last_seen_at FROM prime_ai_usage_alerts "
                "WHERE guild_id=? AND alert_type=? ORDER BY alert_id DESC LIMIT 1",
                (int(guild_id), candidate["type"]),
            ) as cur:
                last = await cur.fetchone()
            if last and str(last["last_seen_at"] or "") >= cutoff:
                await db.execute(
                    "UPDATE prime_ai_usage_alerts SET occurrences=occurrences+1,last_seen_at=?,"
                    "title=?,detail=?,severity=? WHERE alert_id=?",
                    (now, candidate["title"], candidate["detail"], candidate["severity"], int(last["alert_id"])),
                )
            else:
                await db.execute(
                    "INSERT INTO prime_ai_usage_alerts "
                    "(guild_id,alert_type,severity,title,detail,occurrences,created_at,last_seen_at) "
                    "VALUES (?,?,?,?,?,1,?,?)",
                    (int(guild_id), candidate["type"], candidate["severity"], candidate["title"], candidate["detail"], now, now),
                )
        await db.commit()


async def check_quota(guild_id: int) -> str | None:
    snapshot = await get_snapshot(guild_id)
    settings, monthly = snapshot["settings"], snapshot["monthly"]
    limit = int(settings["monthly_request_limit"])
    if limit and int(monthly["requests"]) >= limit:
        return "prime_monthly_request_limit"
    budget = float(settings["monthly_budget_usd"])
    cost = monthly["estimated_cost_usd"]
    if budget and cost is not None and cost >= budget:
        return "prime_monthly_budget_limit"
    return None
