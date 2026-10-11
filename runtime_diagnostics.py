"""Low-overhead, privacy-safe runtime telemetry for PRIME.

Metrics stay in a bounded in-memory window and periodic logs contain only metric
names and aggregate timings. Never pass message text, tokens, user IDs, or other
high-cardinality/private values to this module.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import math
import os
import re
import sys
import time
from collections import defaultdict, deque
from typing import Any, Callable

LOGGER = logging.getLogger("PrimeDiagnostics")
ENABLED = (os.getenv("PRIME_DIAGNOSTICS_ENABLED", "true").strip().lower()
           not in {"0", "false", "no", "off"})
SAMPLE_CAPACITY = 512
DEFAULT_INTERVAL_SECONDS = 5.0
DEFAULT_REPORT_SECONDS = 60.0
DEFAULT_LAG_WARNING_MS = 250.0

# (monotonic timestamp, duration_ms). Both collections have strict size limits.
_SAMPLES: dict[str, deque[tuple[float, float]]] = defaultdict(
    lambda: deque(maxlen=SAMPLE_CAPACITY)
)
_GAUGES: dict[str, float] = {}
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$")


def _valid_name(name: str) -> str | None:
    value = str(name or "")
    return value if _NAME_RE.fullmatch(value) else None


def record_timing(name: str, duration_ms: float) -> None:
    """Record a numeric duration under a fixed, non-sensitive metric name."""
    if not ENABLED:
        return
    key = _valid_name(name)
    try:
        value = float(duration_ms)
    except (TypeError, ValueError, OverflowError):
        return
    if key is None or not math.isfinite(value):
        return
    _SAMPLES[key].append((time.monotonic(), max(0.0, value)))


def set_gauge(name: str, value: float) -> None:
    """Set a low-cardinality numeric gauge, such as a queue depth."""
    if not ENABLED:
        return
    key = _valid_name(name)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return
    if key is not None and math.isfinite(number):
        _GAUGES[key] = number


def adjust_gauge(name: str, delta: float) -> None:
    """Atomically adjust a gauge on the event-loop thread."""
    key = _valid_name(name)
    if key is None:
        return
    _GAUGES[key] = _GAUGES.get(key, 0.0) + float(delta)


def clear_metrics() -> None:
    """Clear bounded samples and gauges; primarily intended for isolated tests."""
    _SAMPLES.clear()
    _GAUGES.clear()


def snapshot(window_seconds: float | None = None) -> dict[str, Any]:
    """Return aggregate values; no event-level values or identifiers are exposed."""
    now = time.monotonic()
    lower = now - max(0.0, float(window_seconds)) if window_seconds is not None else None
    result: dict[str, Any] = {"timings": {}, "gauges": dict(_GAUGES)}
    for name, entries in list(_SAMPLES.items()):
        values = [
            duration for timestamp, duration in entries
            if lower is None or timestamp >= lower
        ]
        if not values:
            continue
        values.sort()
        result["timings"][name] = {
            "samples": len(values),
            "p50_ms": round(values[int((len(values) - 1) * 0.50)], 2),
            "p95_ms": round(values[min(len(values) - 1, math.ceil(len(values) * 0.95) - 1)], 2),
            "max_ms": round(values[-1], 2),
        }
    return result


def timed_async(metric_name: str) -> Callable:
    """Measure an async function in a finally block, including early returns/errors."""
    key = _valid_name(metric_name)
    if key is None:
        raise ValueError("metric_name must be a simple non-sensitive metric name")

    def decorate(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                record_timing(key, (time.perf_counter() - started) * 1000.0)
        return wrapped
    return decorate


def _process_memory_mb() -> tuple[float | None, float | None]:
    """Return current RSS and process peak RSS in MiB where supported."""
    current_mb = peak_mb = None
    try:
        with open("/proc/self/statm", "r", encoding="ascii") as statm:
            fields = statm.read().split()
        if len(fields) > 1:
            current_mb = int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024)
    except (OSError, ValueError, IndexError, AttributeError):
        pass
    try:
        import resource
        peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        # Linux reports KiB; macOS reports bytes.
        peak_mb = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    return (
        round(current_mb, 1) if current_mb is not None else None,
        round(peak_mb, 1) if peak_mb is not None else None,
    )


async def monitor_loop(
    *,
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    report_seconds: float = DEFAULT_REPORT_SECONDS,
    lag_warning_ms: float = DEFAULT_LAG_WARNING_MS,
) -> None:
    """Sample event-loop lag and emit compact aggregate diagnostics periodically."""
    if not ENABLED:
        return
    interval = max(1.0, float(interval_seconds))
    report_interval = max(interval, float(report_seconds))
    loop = asyncio.get_running_loop()
    expected = loop.time() + interval
    report_at = time.monotonic() + report_interval
    cpu_wall_at = time.monotonic()
    cpu_at = time.process_time()
    window_started = time.monotonic()

    while True:
        await asyncio.sleep(max(0.0, expected - loop.time()))
        actual = loop.time()
        lag_ms = max(0.0, (actual - expected) * 1000.0)
        record_timing("event_loop.lag", lag_ms)
        if lag_ms >= max(1.0, float(lag_warning_ms)):
            LOGGER.warning(
                "[PERF] event_loop_lag_ms=%.1f threshold_ms=%.1f",
                lag_ms, lag_warning_ms,
            )

        expected += interval
        if expected <= actual:
            expected = actual + interval

        now = time.monotonic()
        if now < report_at:
            continue

        cpu_now = time.process_time()
        wall_delta = max(0.001, now - cpu_wall_at)
        cpu_pct = max(0.0, (cpu_now - cpu_at) / wall_delta * 100.0)
        cpu_at, cpu_wall_at = cpu_now, now
        current_rss, peak_rss = _process_memory_mb()
        metrics = snapshot(window_seconds=max(0.0, now - window_started))
        timing_parts = []
        for name, item in sorted(metrics["timings"].items()):
            timing_parts.append(
                f"{name}[n={item['samples']},p50={item['p50_ms']:.1f},"
                f"p95={item['p95_ms']:.1f},max={item['max_ms']:.1f}ms]"
            )
        gauge_parts = [
            f"{name}={value:g}" for name, value in sorted(metrics["gauges"].items())
        ]
        memory = (
            f"rss_mb={current_rss if current_rss is not None else 'n/a'} "
            f"peak_rss_mb={peak_rss if peak_rss is not None else 'n/a'}"
        )
        LOGGER.info(
            "[PERF] window_s=%.0f process_cpu_pct=%.1f %s gauges={%s} timings={%s}",
            now - window_started, cpu_pct, memory,
            ",".join(gauge_parts), ";".join(timing_parts),
        )
        window_started = now
        report_at = now + report_interval
