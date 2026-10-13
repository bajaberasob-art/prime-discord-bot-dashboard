"""Dated, model-specific Gemini Standard paid-tier token pricing.

Rates are based on Google's published Gemini Developer API pricing.
Unknown models or incomplete token usage intentionally return no estimate.
No network requests are performed at runtime.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

PRICING_VERIFIED_DATE = "2026-10-13"
GEMINI_PRICING_URL = "https://ai.google.dev/gemini-api/docs/pricing"
GEMINI_MODEL_GUIDE_URL = "https://ai.google.dev/gemini-api/docs/latest-model?hl=en"

# USD per 1,000,000 tokens, Standard paid tier. Output includes thinking tokens
# when Google's model pricing page says it does. The 3.8 introductory rate ends
# on 2026-12-31; do not carry it into 2027.
def rates_for_model(model: str, when: date | None = None) -> dict | None:
    name = str(model or "").strip()
    current_date = when or datetime.now(timezone.utc).date()
    if name == "gemini-3.8-flash":
        input_rate, output_rate = (
            (0.75, 3.75) if current_date < date(2027, 1, 1)
            else (1.50, 7.50)
        )
        return {
            "input_usd_per_million": input_rate,
            "output_usd_per_million": output_rate,
            "source": f"Google Gemini Standard paid tier ({PRICING_VERIFIED_DATE})",
            "source_url": GEMINI_MODEL_GUIDE_URL,
            "valid_from": "2026-01-01",
            "valid_until": "2026-12-31" if current_date < date(2027, 1, 1) else None,
        }
    return None


def calculate_cost_usd(
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    *,
    when: date | None = None,
) -> dict | None:
    """Return a per-request estimate, or None where pricing/usage is unknown."""
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
