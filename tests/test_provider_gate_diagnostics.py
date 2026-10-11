import unittest

import runtime_diagnostics
from prime_ai.concurrency import BoundedProviderGate
from prime_ai.errors import AIProviderUnavailable


class ProviderGateDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        runtime_diagnostics.clear_metrics()

    async def test_immediate_acquire_and_release_updates_active_gauge(self):
        gate = BoundedProviderGate(max_active=1, max_waiting=0)
        await gate.acquire()
        self.assertEqual(
            runtime_diagnostics.snapshot()["gauges"]["ai.provider_gate.active"],
            1.0,
        )
        await gate.release()
        self.assertEqual(
            runtime_diagnostics.snapshot()["gauges"]["ai.provider_gate.active"],
            0.0,
        )

    async def test_queue_rejection_is_counted(self):
        gate = BoundedProviderGate(max_active=1, max_waiting=0)
        await gate.acquire()
        with self.assertRaises(AIProviderUnavailable):
            await gate.acquire()
        self.assertEqual(
            runtime_diagnostics.snapshot()["gauges"]["ai.provider_gate.rejections"],
            1.0,
        )
        await gate.release()

    async def test_queue_timeout_is_counted_and_waiter_removed(self):
        gate = BoundedProviderGate(max_active=1, max_waiting=1, queue_timeout=0.1)
        await gate.acquire()
        with self.assertRaises(AIProviderUnavailable):
            await gate.acquire()
        gauges = runtime_diagnostics.snapshot()["gauges"]
        self.assertEqual(gauges["ai.provider_gate.timeouts"], 1.0)
        self.assertEqual(gauges["ai.provider_gate.waiters"], 0.0)
        await gate.release()


if __name__ == "__main__":
    unittest.main()
