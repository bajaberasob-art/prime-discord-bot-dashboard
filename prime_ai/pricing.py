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
        # Google Gemini 3.8 Flash introductory paid-tier price, valid to year end.
        "gemini-3.8-flash": (0.75, 3.75),
        "gemini-3.1-flash": (0.50, 3.00),
        "gemini-3.1-flash-lite": (0.25, 1.50),
        "gemini-3.1-flash-image": (0.50, 60.00),
        "gemini-3.1-flash-lite-image": (0.25, 30.00),
        "gemini-3-pro-image": (2.00, 120.00),
    }
    pair = rates.get(name)
    if pair is None:
        return None
    is_flash_38 = name == "gemini-3.8-flash"
    if is_flash_38 and current_date > date(2026, 12, 31):
        return None
    return {
        "input_usd_per_million": pair[0],
        "output_usd_per_million": pair[1],
        "source": (
            f"Google Gemini 3.8 Flash introductory pricing ({PRICING_VERIFIED_DATE})"
            if is_flash_38 else
            f"Google Gemini Standard paid tier ({PRICING_VERIFIED_DATE})"
        ),
        "source_url": (
            "https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8"
            if is_flash_38 else GEMINI_PRICING_URL
        ),
        "valid_from": "2026-10-15",
        "valid_until": "2026-12-31" if is_flash_38 else None,
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
