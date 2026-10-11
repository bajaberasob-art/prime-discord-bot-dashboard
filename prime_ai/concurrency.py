"""Cancellation-safe concurrency primitives without Discord/storage dependencies."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from contextlib import asynccontextmanager

import runtime_diagnostics
from .errors import AIProviderUnavailable


class TrackedLock(asyncio.Lock):
    """Include queued acquirers in lifecycle checks, not only the current holder."""

    def __init__(self):
        super().__init__()
        self._users = 0

    async def acquire(self):
        self._users += 1
        try:
            return await super().acquire()
        except BaseException:
            self._users -= 1
            raise

    def release(self):
        super().release()
        self._users -= 1

    def idle(self) -> bool:
        return self._users == 0 and not self.locked()


class BoundedProviderGate:
    """FIFO admission with bounded active requests, queue size and queue latency."""

    def __init__(
        self, *, max_active: int = 8, max_waiting: int = 32,
        queue_timeout: float = 8.0,
    ):
        self.max_active = max(1, int(max_active))
        self.max_waiting = max(0, int(max_waiting))
        self.queue_timeout = max(0.1, float(queue_timeout))
        self._active = 0
        self._waiters: deque[asyncio.Future] = deque()
        self._lock = asyncio.Lock()

    def _publish_gauges(self) -> None:
        """Expose queue pressure without recording request-specific information."""
        runtime_diagnostics.set_gauge("ai.provider_gate.active", self._active)
        runtime_diagnostics.set_gauge("ai.provider_gate.waiters", len(self._waiters))

    async def acquire(self) -> None:
        started = time.perf_counter()
        try:
            loop = asyncio.get_running_loop()
            async with self._lock:
                if self._active < self.max_active and not self._waiters:
                    self._active += 1
                    self._publish_gauges()
                    return
                if len(self._waiters) >= self.max_waiting:
                    runtime_diagnostics.adjust_gauge("ai.provider_gate.rejections", 1)
                    raise AIProviderUnavailable("provider_busy", status_code=503)
                waiter = loop.create_future()
                self._waiters.append(waiter)
                self._publish_gauges()
            try:
                # wait_for can swallow cancellation when its inner future completes
                # concurrently. The timeout context preserves caller cancellation.
                async with asyncio.timeout(self.queue_timeout):
                    await asyncio.shield(waiter)
            except asyncio.TimeoutError as error:
                await self._remove_waiter(waiter)
                runtime_diagnostics.adjust_gauge("ai.provider_gate.timeouts", 1)
                raise AIProviderUnavailable("provider_queue_timeout", status_code=503) from error
            except asyncio.CancelledError:
                await self._remove_waiter(waiter)
                raise
        finally:
            # Includes immediate admissions, queue waits, timeouts and rejections.
            runtime_diagnostics.record_timing(
                "ai.provider_gate_acquire",
                (time.perf_counter() - started) * 1000.0,
            )

    async def _remove_waiter(self, waiter: asyncio.Future) -> None:
        async with self._lock:
            try:
                self._waiters.remove(waiter)
                waiter.cancel()
                self._publish_gauges()
            except ValueError:
                # A successful handoff raced timeout/cancellation: return its slot.
                if waiter.done() and not waiter.cancelled():
                    self._release_locked()

    def _release_locked(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                self._publish_gauges()
                return
        self._active = max(0, self._active - 1)
        self._publish_gauges()

    async def release(self) -> None:
        async with self._lock:
            self._release_locked()

    @asynccontextmanager
    async def slot(self):
        await self.acquire()
        try:
            yield
        finally:
            await self.release()
