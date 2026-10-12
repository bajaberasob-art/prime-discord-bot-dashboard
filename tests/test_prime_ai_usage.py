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
                error_reason=reason, provider="Google Gemini",
                model="gemini-test", correlation_id=reason,
            )
        analytics = await prime_ai_control.get_analytics(guild_id)
        alert_types = {item["alert_type"] for item in analytics["alerts"]}
        self.assertIn("request_spike", alert_types)
        self.assertIn("provider_failures", alert_types)
        self.assertEqual(len(analytics["failure_reasons"]), 2)
        self.assertEqual(len(analytics["recent_failures"]), 2)
        self.assertEqual(
            {item["correlation_id"] for item in analytics["recent_failures"]},
            {"gemini_timeout", "gemini_connection_failed"},
        )

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
        self.assertEqual(analytics["budget"]["unknown_cost_requests"], 0)
        self.assertEqual(analytics["providers"][0]["provider"], "Google Gemini")
        self.assertEqual(analytics["models"][0]["model"], "gemini-test")
        self.assertEqual(analytics["totals"]["p95_latency_ms"], 150)
        self.assertEqual(analytics["totals"]["success_rate"], 100.0)

    async def test_monthly_request_quota_and_unknown_cost_are_explicit(self):
        guild_id = 123456789012350
        await prime_ai_control.save_usage_settings(
            guild_id, monthly_limit_usd=0.0,
            cost_per_million_tokens=1.0, request_spike_count=100,
            request_spike_window_minutes=10, provider_failure_count=5,
            failure_window_minutes=10, alerts_enabled=True,
            monthly_request_limit=1,
        )
        reserved = await prime_ai_control.reserve_usage_budget(guild_id, 0)
        self.assertTrue(reserved["allowed"])
        denied = await prime_ai_control.reserve_usage_budget(guild_id, 0)
        self.assertFalse(denied["allowed"])
        self.assertEqual(denied["monthly_request_limit"], 1)
        await prime_ai_control.record_request(
            guild_id, 111111111111111, 222222222222222,
            skill="image_generation", mode="IMAGE", result="success",
            latency_ms=300, tokens_used=None, provider="Google Gemini",
            model="gemini-image-test", correlation_id="image-unknown-cost",
            reservation_id=reserved["reservation_id"],
        )
        analytics = await prime_ai_control.get_analytics(guild_id)
        self.assertEqual(analytics["budget"]["monthly_request_limit"], 1)
        self.assertEqual(analytics["budget"]["remaining_requests"], 0)
        self.assertEqual(analytics["budget"]["unknown_cost_requests"], 1)
        self.assertTrue(analytics["budget"]["cost_estimate_incomplete"])
        self.assertTrue(analytics["budget"]["exceeded"])
        self.assertEqual(analytics["models"][0]["unknown_cost_requests"], 1)

    async def test_configurable_budget_alert_thresholds(self):
        guild_id = 123456789012352
        await prime_ai_control.save_usage_settings(
            guild_id, monthly_limit_usd=1.0,
            cost_per_million_tokens=1.0, request_spike_count=100,
            request_spike_window_minutes=10, provider_failure_count=5,
            failure_window_minutes=10, alerts_enabled=True,
            budget_warning_percent=70, budget_critical_percent=90,
        )
        await prime_ai_control.record_request(
            guild_id, 111111111111111, 222222222222222,
            skill="chat", mode="CHAT", result="success", latency_ms=50,
            tokens_used=700_000,
        )
        analytics = await prime_ai_control.get_analytics(guild_id)
        self.assertIn("budget_warning", {item["alert_type"] for item in analytics["alerts"]})
        await prime_ai_control.record_request(
            guild_id, 111111111111111, 222222222222222,
            skill="chat", mode="CHAT", result="success", latency_ms=60,
            tokens_used=200_000,
        )
        analytics = await prime_ai_control.get_analytics(guild_id)
        self.assertIn("budget_critical_warning", {item["alert_type"] for item in analytics["alerts"]})

    async def test_legacy_usage_settings_migrate_without_losing_values(self):
        guild_id = 123456789012353
        async with database.connect() as db:
            await db.execute("""
                CREATE TABLE prime_ai_usage_settings (
                    guild_id INTEGER PRIMARY KEY,
                    monthly_limit_usd REAL NOT NULL DEFAULT 25.0,
                    cost_per_million_tokens REAL NOT NULL DEFAULT 1.0,
                    request_spike_count INTEGER NOT NULL DEFAULT 60,
                    request_spike_window_minutes INTEGER NOT NULL DEFAULT 10,
                    provider_failure_count INTEGER NOT NULL DEFAULT 5,
                    failure_window_minutes INTEGER NOT NULL DEFAULT 10,
                    alerts_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute(
                "INSERT INTO prime_ai_usage_settings "
                "(guild_id, monthly_limit_usd, cost_per_million_tokens) VALUES (?, ?, ?)",
                (guild_id, 13.5, 2.25),
            )
            await db.commit()
        analytics = await prime_ai_control.get_analytics(guild_id)
        self.assertEqual(analytics["budget"]["settings"]["monthly_limit_usd"], 13.5)
        self.assertEqual(analytics["budget"]["settings"]["cost_per_million_tokens"], 2.25)
        self.assertEqual(analytics["budget"]["settings"]["monthly_request_limit"], 0)
        self.assertEqual(analytics["budget"]["settings"]["budget_warning_percent"], 70)
        self.assertEqual(analytics["budget"]["settings"]["budget_critical_percent"], 90)

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
