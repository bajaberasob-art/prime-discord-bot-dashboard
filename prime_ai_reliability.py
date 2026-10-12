"""Per-guild PRIME AI budgets, cost estimates, and reliability alerts.

All cost figures are estimates based on an administrator-configured blended
USD-per-million-token rate. No provider invoice or live billing data is implied.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite
import database
import prime_ai_control as control

DEFAULTS = {
    "monthly_request_limit": 0,
    "monthly_token_limit": 0,
    "monthly_cost_limit_usd": 0.0,
    "usd_per_million_tokens": 0.0,
    "spike_requests": 30,
    "spike_window_minutes": 15,
    "failure_count": 5,
    "failure_window_minutes": 10,
    "alert_channel_id": "",
}
MAX_LIMIT = 100_000_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="seconds")


async def ensure_schema() -> None:
    async with database.connect() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prime_ai_reliability_settings (
                guild_id INTEGER PRIMARY KEY,
                settings_json TEXT NOT NULL DEFAULT '{}',
                updated_by INTEGER,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS prime_ai_reliability_alerts (
                alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                alert_key TEXT NOT NULL,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged_at TEXT,
                UNIQUE(guild_id, alert_key)
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_prime_ai_reliability_alerts ON prime_ai_reliability_alerts(guild_id, created_at DESC)")
        async with db.execute("PRAGMA table_info(prime_ai_request_events)") as cur:
            columns = {str(row[1]) for row in await cur.fetchall()}
        if "provider" not in columns:
            await db.execute("ALTER TABLE prime_ai_request_events ADD COLUMN provider TEXT NOT NULL DEFAULT 'Google Gemini'")
        if "failure_reason" not in columns:
            await db.execute("ALTER TABLE prime_ai_request_events ADD COLUMN failure_reason TEXT NOT NULL DEFAULT ''")
        await db.commit()


def normalize_settings(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("invalid_settings")
    unknown = set(value) - set(DEFAULTS)
    if unknown:
        raise ValueError("unknown_settings_field")
    out = dict(DEFAULTS)
    out.update(value)
    for key in ("monthly_request_limit", "monthly_token_limit", "spike_requests", "spike_window_minutes", "failure_count", "failure_window_minutes"):
        try:
            number = int(out[key])
        except (TypeError, ValueError):
            raise ValueError("invalid_" + key) from None
        if number < (0 if key.startswith("monthly_") else 1) or number > MAX_LIMIT:
            raise ValueError("invalid_" + key)
        out[key] = number
    for key in ("monthly_cost_limit_usd", "usd_per_million_tokens"):
        try:
            number = float(out[key])
        except (TypeError, ValueError):
            raise ValueError("invalid_" + key) from None
        if number < 0 or number > 1_000_000_000:
            raise ValueError("invalid_" + key)
        out[key] = round(number, 8)
    channel = str(out.get("alert_channel_id") or "")
    if channel and (not channel.isdigit() or not 15 <= len(channel) <= 22):
        raise ValueError("invalid_alert_channel_id")
    out["alert_channel_id"] = channel
    return out


async def get_settings(guild_id: int) -> dict:
    await ensure_schema()
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute("SELECT settings_json FROM prime_ai_reliability_settings WHERE guild_id=?", (int(guild_id),)) as cur:
            row = await cur.fetchone()
    if not row:
        return dict(DEFAULTS)
    try:
        value = json.loads(row["settings_json"])
    except (TypeError, ValueError):
        value = {}
    return normalize_settings(value if isinstance(value, dict) else {})


async def save_settings(guild_id: int, actor_id: int, value: Any) -> dict:
    settings = normalize_settings(value)
    await ensure_schema()
    async with database.connect() as db:
        await db.execute(
            "INSERT INTO prime_ai_reliability_settings(guild_id,settings_json,updated_by,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET settings_json=excluded.settings_json,updated_by=excluded.updated_by,updated_at=excluded.updated_at",
            (int(guild_id), json.dumps(settings, ensure_ascii=False), int(actor_id), _stamp()),
        )
        await db.commit()
    return settings


async def record_request(guild_id: int, user_id: int | None, channel_id: int | None, *, skill: str, mode: str, result: str, latency_ms: int, tokens_used: int | None = None, provider: str = "Google Gemini", failure_reason: str = "") -> None:
    await ensure_schema()
    async with database.connect() as db:
        await db.execute(
            "INSERT INTO prime_ai_request_events(guild_id,user_id,channel_id,skill,mode,result,latency_ms,tokens_used,created_at,provider,failure_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (int(guild_id), int(user_id) if user_id is not None else None, int(channel_id) if channel_id is not None else None, str(skill)[:80], str(mode)[:20], str(result)[:30], max(0, min(int(latency_ms), 3_600_000)), max(0, int(tokens_used)) if tokens_used is not None else None, control.timestamp(), str(provider)[:80], str(failure_reason)[:180]),
        )
        await db.commit()


async def _insert_alert(db, guild_id: int, key: str, severity: str, title: str, detail: str) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO prime_ai_reliability_alerts(guild_id,alert_key,severity,title,detail,created_at) VALUES(?,?,?,?,?,?)",
        (int(guild_id), key[:180], severity, title[:140], detail[:500], _stamp()),
    )


async def get_analytics(guild_id: int) -> dict:
    settings = await get_settings(guild_id)
    now = _now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    spike_start = _stamp(now - timedelta(minutes=settings["spike_window_minutes"]))
    failure_start = _stamp(now - timedelta(minutes=settings["failure_window_minutes"]))
    async with database.connect(aiosqlite.Row) as db:
        async with db.execute(
            "SELECT COUNT(*) requests, SUM(CASE WHEN result='success' THEN 1 ELSE 0 END) successful, "
            "SUM(CASE WHEN result!='success' THEN 1 ELSE 0 END) failed, "
            "SUM(COALESCE(tokens_used,0)) tokens, AVG(latency_ms) avg_latency_ms "
            "FROM prime_ai_request_events WHERE guild_id=? AND created_at>=?",
            (int(guild_id), month_start),
        ) as cur:
            row = dict(await cur.fetchone())
        totals = {k: int(row.get(k) or 0) for k in ("requests", "successful", "failed", "tokens")}
        totals["avg_latency_ms"] = round(float(row.get("avg_latency_ms") or 0), 1)
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_request_events WHERE guild_id=? AND created_at>=?",
            (int(guild_id), spike_start),
        ) as cur:
            spike_count = int((await cur.fetchone())[0])
        async with db.execute(
            "SELECT COUNT(*) FROM prime_ai_request_events WHERE guild_id=? AND result!='success' AND created_at>=?",
            (int(guild_id), failure_start),
        ) as cur:
            recent_failures = int((await cur.fetchone())[0])
        async with db.execute(
            "SELECT failure_reason, provider, COUNT(*) count, MAX(created_at) last_seen FROM prime_ai_request_events "
            "WHERE guild_id=? AND result!='success' AND created_at>=? GROUP BY failure_reason,provider ORDER BY count DESC LIMIT 8",
            (int(guild_id), _stamp(now - timedelta(days=30))),
        ) as cur:
            failure_reasons = [dict(item) for item in await cur.fetchall()]
        async with db.execute(
            "SELECT skill,COUNT(*) requests FROM prime_ai_request_events WHERE guild_id=? AND created_at>=? GROUP BY skill ORDER BY requests DESC LIMIT 8",
            (int(guild_id), _stamp(now - timedelta(days=30))),
        ) as cur:
            skills = [dict(item) for item in await cur.fetchall()]
        async with db.execute(
            "SELECT alert_id, severity, title, detail, created_at, acknowledged_at FROM prime_ai_reliability_alerts WHERE guild_id=? ORDER BY alert_id DESC LIMIT 20",
            (int(guild_id),),
        ) as cur:
            alerts = [dict(item) for item in await cur.fetchall()]
        rate = settings["usd_per_million_tokens"]
        estimated_cost = round(totals["tokens"] * rate / 1_000_000, 6)
        if settings["spike_requests"] and spike_count >= settings["spike_requests"]:
            await _insert_alert(db, guild_id, f"spike:{now.strftime('%Y%m%d%H%M')}", "warning", "ارتفاع مفاجئ في الطلبات", f"{spike_count} طلب خلال {settings['spike_window_minutes']} دقيقة.")
        if settings["failure_count"] and recent_failures >= settings["failure_count"]:
            await _insert_alert(db, guild_id, f"failures:{now.strftime('%Y%m%d%H%M')}", "critical", "تكرار فشل مزوّد الذكاء", f"{recent_failures} إخفاق خلال {settings['failure_window_minutes']} دقائق.")
        for name, current, limit in (
            ("requests", totals["requests"], settings["monthly_request_limit"]),
            ("tokens", totals["tokens"], settings["monthly_token_limit"]),
            ("cost", estimated_cost, settings["monthly_cost_limit_usd"]),
        ):
            if limit and current >= limit:
                await _insert_alert(db, guild_id, f"budget:{name}:{now.strftime('%Y-%m')}", "critical", "تجاوز حد PRIME الشهري", f"{name}: {current} / {limit}.")
            elif limit and current >= limit * 0.8:
                await _insert_alert(db, guild_id, f"budget80:{name}:{now.strftime('%Y-%m')}", "warning", "اقتراب PRIME من الحد الشهري", f"{name}: {current} / {limit}.")
        await db.commit()
        async with db.execute(
            "SELECT alert_id, severity, title, detail, created_at, acknowledged_at FROM prime_ai_reliability_alerts WHERE guild_id=? ORDER BY alert_id DESC LIMIT 20",
            (int(guild_id),),
        ) as cur:
            alerts = [dict(item) for item in await cur.fetchall()]
    def percent(value, limit):
        return round(min(100.0, float(value) / float(limit) * 100), 1) if limit else None
    return {
        "period": "calendar_month",
        "month_start": month_start,
        "totals": totals,
        "estimated_cost_usd": estimated_cost,
        "cost_rate_usd_per_million_tokens": rate,
        "cost_estimate_configured": rate > 0,
        "quotas": {
            "requests": {"used": totals["requests"], "limit": settings["monthly_request_limit"], "percent": percent(totals["requests"], settings["monthly_request_limit"])},
            "tokens": {"used": totals["tokens"], "limit": settings["monthly_token_limit"], "percent": percent(totals["tokens"], settings["monthly_token_limit"])},
            "cost_usd": {"used": estimated_cost, "limit": settings["monthly_cost_limit_usd"], "percent": percent(estimated_cost, settings["monthly_cost_limit_usd"])},
        },
        "settings": settings,
        "spike": {"count": spike_count, "window_minutes": settings["spike_window_minutes"], "threshold": settings["spike_requests"]},
        "reliability": {"recent_failures": recent_failures, "window_minutes": settings["failure_window_minutes"], "threshold": settings["failure_count"], "failure_reasons": failure_reasons},
        "skills": skills,
        "alerts": alerts,
    }


async def acknowledge_alert(guild_id: int, alert_id: int) -> bool:
    await ensure_schema()
    async with database.connect() as db:
        cur = await db.execute("UPDATE prime_ai_reliability_alerts SET acknowledged_at=? WHERE guild_id=? AND alert_id=? AND acknowledged_at IS NULL", (_stamp(), int(guild_id), int(alert_id)))
        await db.commit()
    return cur.rowcount == 1
