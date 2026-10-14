"""Dated Gemini Developer API Standard paid-tier pricing.

Source: Google's published pricing page, verified 2026-10-15.
Rates are USD per million tokens. Image models use a conservative 4K-output
token allowance at reservation time; final image cost remains an estimate.
Unknown models intentionally return no estimate.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

PRICING_VERIFIED_DATE = "2026-10-15"
GEMINI_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"


def rates_for_model(model: str, when: date | None = None) -> dict | None:
    name = str(model or "").strip()
    current_date = when or datetime.now(timezone.utc).date()
    # Gemini 3.1 standard paid tier. Output prices include thinking tokens.
    rates = {
        "gemini-3.1-flash": (0.50, 3.00),
        "gemini-3.1-flash-lite": (0.25, 1.50),
        "gemini-3.1-flash-image": (0.50, 60.00),
        "gemini-3.1-flash-lite-image": (0.25, 30.00),
        "gemini-3-pro-image": (2.00, 120.00),
    }
    pair = rates.get(name)
    if pair is None:
        return None
    return {
        "input_usd_per_million": pair[0],
        "output_usd_per_million": pair[1],
        "source": f"Google Gemini Standard paid tier ({PRICING_VERIFIED_DATE})",
        "source_url": GEMINI_PRICING_URL,
        "valid_from": "2026-10-15",
        "valid_until": None,
    }


def calculate_cost_usd(
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    *,
    when: date | None = None,
) -> dict | None:
    """Return an estimate, or None when pricing or token usage is unknown."""
    rates = rates_for_model(model, when)
    if rates is None:
        return None
    if (
        isinstance(prompt_tokens, bool) or not isinstance(prompt_tokens, int)
        or isinstance(completion_tokens, bool) or not isinstance(completion_tokens, int)
        or prompt_tokens < 0 or completion_tokens < 0
    ):
        return None
    amount = (
        prompt_tokens * rates["input_usd_per_million"]
        + completion_tokens * rates["output_usd_per_million"]
    ) / 1_000_000
    return {
        "estimated_cost_usd": round(amount, 9),
        "pricing_source": rates["source"],
        "pricing_source_url": rates["source_url"],
        "input_usd_per_million": rates["input_usd_per_million"],
        "output_usd_per_million": rates["output_usd_per_million"],
    }
