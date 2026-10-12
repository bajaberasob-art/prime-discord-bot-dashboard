"""Budget, per-guild usage and reliability alert contracts for PRIME AI."""

import asyncio
import tempfile
import unittest
from pathlib import Path

import database
import prime_ai_control


class PrimeAIUsageReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_db_name = database.DB_NAME
        self.temp_dir = tempfile.TemporaryDirectory()
        database.DB_NAME = str(Path(self.temp_dir.name) / "prime-usage.sqlite")
        await database.init_db()

    async def asyncTearDown(self):
        database.DB_NAME = self.old_db_name
        self.temp_dir.cleanup()

    async def test_monthly_cost_is_estimated_and_budget_alert_is_persisted(self):
        guild_id = 123456789012345
        await prime_ai_control.save_usage_settings(
            guild_id, monthly_limit_usd=1.0,
            cost_per_million_tokens=2.0, request_spike_count=100,
            request_spike_window_minutes=10, provider_failure_count=5,
            failure_window_minutes=10, alerts_enabled=True,
        )
        await prime_ai_control.record_request(
            guild_id, 111111111111111, 222222222222222,
            skill="chat", mode="CHAT", result="success", latency_ms=50,
            tokens_used=1_000_000,
        )
        analytics = await prime_ai_control.get_analytics(guild_id)
        self.assertEqual(analytics["budget"]["monthly_tokens"], 1_000_000)
        self.assertEqual(analytics["budget"]["estimated_cost_usd"], 2.0)
        self.assertTrue(analytics["budget"]["exceeded"])
        self.assertTrue(any(item["alert_type"] == "budget_exceeded" for item in analytics["alerts"]))
        self.assertTrue((await prime_ai_control.check_usage_budget(guild_id))["exceeded"])

    async def test_request_spike_and_provider_failures_create_separate_alerts(self):
        guild_id = 123456789012346
        await prime_ai_control.save_usage_settings(
            guild_id, monthly_limit_usd=100.0,
            cost_per_million_tokens=1.0, request_spike_count=2,
            request_spike_window_minutes=10, provider_failure_count=2,
            failure_window_minutes=10, alerts_enabled=True,
        )
        for reason in ("gemini_timeout", "gemini_connection_failed"):
            await prime_ai_control.record_request(
                guild_id, 111111111111111, 222222222222222,
                skill="chat", mode="CHAT", result="failed", latency_ms=500,
                error_reason=reason,
            )
        analytics = await prime_ai_control.get_analytics(guild_id)
        alert_types = {item["alert_type"] for item in analytics["alerts"]}
        self.assertIn("request_spike", alert_types)
        self.assertIn("provider_failures", alert_types)
        self.assertEqual(len(analytics["failure_reasons"]), 2)

    async def test_concurrent_reservations_cannot_spend_the_same_budget(self):
        guild_id = 123456789012349
        await prime_ai_control.save_usage_settings(
            guild_id, monthly_limit_usd=1.0,
            cost_per_million_tokens=1.0, request_spike_count=100,
            request_spike_window_minutes=10, provider_failure_count=5,
            failure_window_minutes=10, alerts_enabled=True,
        )
        first, second = await asyncio.gather(
            prime_ai_control.reserve_usage_budget(guild_id, 600_000),
            prime_ai_control.reserve_usage_budget(guild_id, 600_000),
        )
        self.assertEqual(sum(bool(item["allowed"]) for item in (first, second)), 1)
        accepted = first if first["allowed"] else second
        self.assertEqual(accepted["reserved_cost_usd"], 0.0)
        self.assertGreater(accepted["remaining_usd"], 0)
        # Settling the accepted request atomically releases the reservation and
        # records only the usage reported by the provider.
        await prime_ai_control.record_request(
            guild_id, 111111111111111, 222222222222222,
            skill="chat", mode="CHAT", result="success", latency_ms=150,
            tokens_used=400_000, provider="Google Gemini", model="gemini-test",
            correlation_id="test-correlation", reservation_id=accepted["reservation_id"],
        )
        analytics = await prime_ai_control.get_analytics(guild_id)
        self.assertEqual(analytics["budget"]["monthly_tokens"], 400_000)
        self.assertEqual(analytics["budget"]["reserved_tokens"], 0)
        self.assertEqual(analytics["providers"][0]["provider"], "Google Gemini")
        self.assertEqual(analytics["models"][0]["model"], "gemini-test")
        self.assertEqual(analytics["totals"]["p95_latency_ms"], 150)
        self.assertEqual(analytics["totals"]["success_rate"], 100.0)

    async def test_usage_isolated_by_guild(self):
        await prime_ai_control.record_request(
            123456789012347, 111111111111111, None,
            skill="chat", mode="CHAT", result="success", latency_ms=10,
            tokens_used=500,
        )
        other = await prime_ai_control.get_analytics(123456789012348)
        self.assertEqual(other["budget"]["monthly_tokens"], 0)
        self.assertEqual(other["totals"]["requests"], 0)


if __name__ == "__main__":
    unittest.main()
