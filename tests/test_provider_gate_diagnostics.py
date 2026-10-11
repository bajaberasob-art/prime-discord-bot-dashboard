import unittest

import runtime_diagnostics
from prime_ai.concurrency import BoundedProviderGate


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


if __name__ == "__main__":
    unittest.main()
