import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

from prime_ai.errors import AIProviderUnavailable
from prime_ai.pricing import calculate_cost_usd, rates_for_model
from prime_ai.providers import OpenAICompatibleProvider, TokenUsage, request_with_provider_fallback


class _FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, **_kwargs):
        return self.payload


class _FakeSession:
    closed = False

    def __init__(self, response):
        self.response = response
        self.url = None
        self.body = None
        self.headers = None

    def post(self, url, *, json, headers, **_kwargs):
        self.url, self.body, self.headers = url, json, headers
        return self.response


class _FallbackProvider:
    name = "OpenRouter"
    provider_key = "openrouter"

    def __init__(self):
        self.calls = 0

    def is_configured(self):
        return True

    def model_name(self):
        return "openai/gpt-oss-20b"

    async def complete(self, _session, payload, *, timeout_seconds):
        self.calls += 1
        payload["_actual_provider"] = self.name
        payload["_actual_model"] = self.model_name()
        return "fallback answer", TokenUsage(5, 2, 3)


class ProviderFailoverTests(unittest.IsolatedAsyncioTestCase):
    def test_pricing_catalog_covers_fallback_and_gemini_introductory_expiry(self):
        fallback = rates_for_model("gemini-3.5-flash-lite")
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["input_usd_per_million"], 0.30)
        self.assertEqual(fallback["output_usd_per_million"], 2.50)
        estimated = calculate_cost_usd("gemini-3.5-flash-lite", 1_000_000, 1_000_000)
        self.assertEqual(estimated["estimated_cost_usd"], 2.8)

        final_intro_day = rates_for_model("gemini-3.8-flash", date(2026, 12, 31))
        first_standard_day = rates_for_model("gemini-3.8-flash", date(2027, 1, 1))
        self.assertEqual(final_intro_day["output_usd_per_million"], 3.75)
        self.assertIsNone(first_standard_day)

    async def test_openai_compatible_adapter_parses_text_and_usage(self):
        session = _FakeSession(_FakeResponse({
            "model": "openai/gpt-oss-20b",
            "choices": [{"message": {"content": " backup works "}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
        }))
        provider = OpenAICompatibleProvider(
            name="OpenRouter", provider_key="openrouter",
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            api_key_env="OPENROUTER_API_KEY",
            model_env="PRIME_OPENROUTER_MODEL",
            default_model="openai/gpt-oss-20b",
            extra_headers={"X-OpenRouter-Title": "PRIME Discord Bot"},
        )
        payload = {
            "model": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.5, "max_tokens": 250,
        }
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            answer, usage = await provider.complete(session, payload, timeout_seconds=5)

        self.assertEqual(answer, "backup works")
        self.assertEqual(int(usage), 10)
        self.assertEqual(usage.prompt_tokens, 4)
        self.assertEqual(usage.completion_tokens, 6)
        self.assertEqual(session.body["model"], "openai/gpt-oss-20b")
        self.assertEqual(session.headers["Authorization"], "Bearer test-key")
        self.assertEqual(payload["_actual_provider"], "OpenRouter")

    async def test_gemini_outage_uses_independent_backup(self):
        fallback = _FallbackProvider()

        async def unavailable(*_args, **_kwargs):
            raise AIProviderUnavailable("gemini_status_503", retryable=True, status_code=503)

        payload = {
            "model": "gemini-3.8-flash",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.7, "max_tokens": 200,
        }
        answer, _tokens = await request_with_provider_fallback(
            object(), payload, timeout_seconds=5, retry_count=0,
            complete=unavailable, logger=Mock(), fallback_providers=[fallback],
        )
        self.assertEqual(answer, "fallback answer")
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(payload["_actual_provider"], "OpenRouter")
        self.assertEqual(payload["_actual_model"], "openai/gpt-oss-20b")

    async def test_missing_gemini_key_uses_configured_backup(self):
        fallback = _FallbackProvider()

        async def missing_key(*_args, **_kwargs):
            raise AIProviderUnavailable("gemini_api_key_missing")

        answer, _ = await request_with_provider_fallback(
            object(),
            {"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "hello"}]},
            timeout_seconds=5, retry_count=0, complete=missing_key,
            logger=Mock(), fallback_providers=[fallback],
        )
        self.assertEqual(answer, "fallback answer")
        self.assertEqual(fallback.calls, 1)

    async def test_non_retryable_gemini_failure_does_not_route_elsewhere(self):
        fallback = _FallbackProvider()

        async def rejected(*_args, **_kwargs):
            raise AIProviderUnavailable("gemini_no_text_candidate")

        with self.assertRaises(AIProviderUnavailable):
            await request_with_provider_fallback(
                object(),
                {"model": "gemini-3.8-flash", "messages": [{"role": "user", "content": "hello"}]},
                timeout_seconds=5, retry_count=0, complete=rejected,
                logger=Mock(), fallback_providers=[fallback],
            )
        self.assertEqual(fallback.calls, 0)

    async def test_attachments_never_use_independent_provider(self):
        fallback = _FallbackProvider()

        async def unavailable(*_args, **_kwargs):
            raise AIProviderUnavailable("gemini_status_503", retryable=True, status_code=503)

        with self.assertRaises(AIProviderUnavailable):
            await request_with_provider_fallback(
                object(),
                {"model": "gemini-3.8-flash", "messages": [{
                    "role": "user", "content": "describe this",
                    "media_parts": [{"type": "image", "mime_type": "image/png", "data": "abc"}],
                }]},
                timeout_seconds=5, retry_count=0, complete=unavailable,
                logger=Mock(), fallback_providers=[fallback],
            )
        self.assertEqual(fallback.calls, 0)


if __name__ == "__main__":
    unittest.main()
