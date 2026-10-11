import asyncio
import unittest

import runtime_diagnostics


class RuntimeDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        runtime_diagnostics.clear_metrics()

    async def test_timed_async_records_success(self):
        @runtime_diagnostics.timed_async("test.success")
        async def work():
            await asyncio.sleep(0.001)
            return "ok"

        self.assertEqual(await work(), "ok")
        sample = runtime_diagnostics.snapshot()["timings"]["test.success"]
        self.assertEqual(sample["samples"], 1)
        self.assertGreaterEqual(sample["max_ms"], 0)

    async def test_timed_async_records_failure_without_swallowing_it(self):
        @runtime_diagnostics.timed_async("test.failure")
        async def work():
            raise RuntimeError("expected")

        with self.assertRaisesRegex(RuntimeError, "expected"):
            await work()
        self.assertEqual(
            runtime_diagnostics.snapshot()["timings"]["test.failure"]["samples"],
            1,
        )

    def test_sample_window_is_bounded_and_gauges_are_numeric(self):
        for value in range(runtime_diagnostics.SAMPLE_CAPACITY + 20):
            runtime_diagnostics.record_timing("test.bounded", value)
        runtime_diagnostics.set_gauge("test.queue", 7)
        summary = runtime_diagnostics.snapshot()["timings"]["test.bounded"]
        self.assertEqual(summary["samples"], runtime_diagnostics.SAMPLE_CAPACITY)
        self.assertEqual(summary["max_ms"], float(runtime_diagnostics.SAMPLE_CAPACITY + 19))
        self.assertEqual(runtime_diagnostics.snapshot()["gauges"]["test.queue"], 7.0)


if __name__ == "__main__":
    unittest.main()
