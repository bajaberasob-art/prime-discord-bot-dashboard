import asyncio

import database
import prime_ai_reliability as reliability


def test_guild_scoped_budget_alerts_and_provider_failure_reasons(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(database, "DB_NAME", str(tmp_path / "prime-reliability.sqlite"))
        await database.init_db()
        guild_id = 918273645
        await reliability.save_settings(guild_id, 123456789, {
            "monthly_request_limit": 2,
            "monthly_token_limit": 100,
            "monthly_cost_limit_usd": 0.01,
            "usd_per_million_tokens": 100,
            "spike_requests": 2,
            "spike_window_minutes": 15,
            "failure_count": 2,
            "failure_window_minutes": 10,
        })
        for index in range(2):
            await reliability.record_request(
                guild_id, 123456789, 555555555,
                skill="conversation", mode="CHAT", result="failed",
                latency_ms=1200, tokens_used=100, provider="Google Gemini",
                failure_reason="provider=Google Gemini · reason=timeout",
            )

        result = await reliability.get_analytics(guild_id)
        assert result["totals"]["requests"] == 2
        assert result["totals"]["failed"] == 2
        assert result["totals"]["tokens"] == 200
        assert result["cost_estimate_configured"] is True
        assert result["estimated_cost_usd"] == 0.02
        assert result["reliability"]["recent_failures"] == 2
        assert result["reliability"]["failure_reasons"][0]["failure_reason"].endswith("timeout")
        titles = {alert["title"] for alert in result["alerts"]}
        assert "ارتفاع مفاجئ في الطلبات" in titles
        assert "تكرار فشل مزوّد الذكاء" in titles
        assert "تجاوز حد PRIME الشهري" in titles

        other_guild = await reliability.get_analytics(guild_id + 1)
        assert other_guild["totals"]["requests"] == 0
        assert other_guild["alerts"] == []

    asyncio.run(scenario())


def test_reliability_settings_reject_unknown_and_negative_limits():
    for value in (
        {"unexpected": True},
        {"monthly_request_limit": -1},
        {"usd_per_million_tokens": -0.5},
        {"failure_window_minutes": 0},
    ):
        try:
            reliability.normalize_settings(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid settings: {value}")
