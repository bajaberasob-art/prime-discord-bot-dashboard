import pytest

import prime_ai_usage


def test_cost_estimation_requires_an_explicit_price():
    assert prime_ai_usage.estimate_cost(100_000, 0) is None
    assert prime_ai_usage.estimate_cost(None, 1.5) is None
    assert prime_ai_usage.estimate_cost(100_000, 1.5) == 0.15


def test_cost_estimation_can_count_generated_images_separately():
    assert prime_ai_usage.estimate_cost(0, 0, images=3, usd_per_image=0.04) == 0.12
    assert prime_ai_usage.estimate_cost(100_000, 1.0, images=2, usd_per_image=0.05) == 0.2


def test_usage_settings_keep_caps_disabled_by_default_and_alerts_enabled():
    settings = prime_ai_usage.normalize_settings({})
    assert settings["monthly_request_limit"] == 0
    assert settings["monthly_budget_usd"] == 0
    assert settings["usd_per_million_tokens"] == 0
    assert settings["alerts_enabled"] is True
    assert settings["request_spike_threshold"] == 30
    assert settings["provider_failure_threshold"] == 3


@pytest.mark.parametrize("setting,value", [
    ("monthly_request_limit", -1),
    ("monthly_request_limit", 10_000_001),
    ("monthly_budget_usd", -0.01),
    ("usd_per_million_tokens", float("inf")),
    ("request_spike_threshold", 0),
    ("provider_failure_threshold", -1),
    ("alerts_enabled", "yes"),
])
def test_usage_settings_reject_invalid_values(setting, value):
    with pytest.raises(ValueError):
        prime_ai_usage.normalize_settings({setting: value})


def test_alert_candidates_cover_request_spikes_failures_and_warnings():
    snapshot = {
        "settings": {
            "request_spike_threshold": 10,
            "provider_failure_threshold": 2,
            "monthly_request_limit": 100,
            "monthly_budget_usd": 2,
        },
        "monthly": {"requests": 80, "estimated_cost_usd": 1.8},
        "requests_10m": 10,
        "provider_failures_30m": 2,
    }
    kinds = {item["type"] for item in prime_ai_usage._alert_candidates(snapshot)}
    assert {
        "request_spike", "provider_failures",
        "monthly_requests_warning", "monthly_budget_warning",
    } <= kinds


def test_alert_candidates_raise_critical_alert_at_caps():
    snapshot = {
        "settings": {
            "request_spike_threshold": 30,
            "provider_failure_threshold": 3,
            "monthly_request_limit": 10,
            "monthly_budget_usd": 1,
        },
        "monthly": {"requests": 10, "estimated_cost_usd": 1},
        "requests_10m": 0,
        "provider_failures_30m": 0,
    }
    candidates = {item["type"]: item for item in prime_ai_usage._alert_candidates(snapshot)}
    assert candidates["monthly_requests_limit"]["severity"] == "critical"
    assert candidates["monthly_budget_limit"]["severity"] == "critical"
