"""HTTP adapters and bounded request policy; no guild, database or Discord state."""

from __future__ import annotations

import asyncio
import json
import os
import re
from urllib.parse import quote

import aiohttp

from .errors import AIProviderUnavailable

PROVIDER_NAME = "Google Gemini"
PROVIDER_MODEL = "gemini-3.8-flash"
FALLBACK_PROVIDER_MODEL = "gemini-3.5-flash-lite"
RETRYABLE_PROVIDER_STATUSES = frozenset({408, 425, 500, 502, 503, 504})
FALLBACK_PROVIDER_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class PollinationsProvider:
    """URL adapter for the existing image command."""

    image_endpoint = "https://image.pollinations.ai/prompt"

    @staticmethod
    def image_url(prompt: str, *, width: int = 800, height: int = 600) -> str:
        value = str(prompt or "").strip()
        if not value:
            raise ValueError("invalid_image_prompt")
        if (
            isinstance(width, bool) or not isinstance(width, int)
            or not 64 <= width <= 2048 or isinstance(height, bool)
            or not isinstance(height, int) or not 64 <= height <= 2048
        ):
            raise ValueError("invalid_image_dimensions")
        return (
            f"{PollinationsProvider.image_endpoint}/{quote(value, safe='')}"
            f"?width={width}&height={height}&nologo=true"
        )


class GeminiProvider:
    """Gemini REST adapter preserving PRIME's messages and generation settings."""

    api_endpoint = "https://generativelanguage.googleapis.com/v1beta/models/"

    @staticmethod
    def _request_body(payload: dict) -> dict:
        messages = payload.get("messages", [])
        system_text = "\n\n".join(
            str(item.get("content", "")).strip()
            for item in messages
            if item.get("role") == "system" and str(item.get("content", "")).strip()
        )
        contents = []
        for item in messages:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = str(item.get("content", "")).strip()
            if not text:
                continue
            gemini_role = "model" if role == "assistant" else "user"
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"][0]["text"] += "\n" + text
            else:
                contents.append({"role": gemini_role, "parts": [{"text": text}]})
        # Gemini 3.x deprecates temperature/top_p/top_k. Sending a legacy
        # temperature value makes otherwise valid generateContent calls fail
        # with HTTP 400, so generation is controlled through thinkingLevel.
        body = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": payload.get("max_tokens", 1200),
                "thinkingConfig": {
                    "thinkingLevel": str(payload.get("thinking_level", "medium")).lower(),
                },
            },
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        return body

    @staticmethod
    def _text_parts(data: dict) -> list[str]:
        candidates = data.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("invalid_candidates")
        if not candidates:
            return []
        parts = candidates[0].get("content", {}).get("parts", [])
        # Thought summaries are optional internal reasoning artifacts, not
        # user-facing answers. Never forward them to Discord even if a future
        # request enables includeThoughts or a provider returns them by default.
        return [
            part["text"] for part in parts
            if (
                isinstance(part, dict)
                and isinstance(part.get("text"), str)
                and not bool(part.get("thought", False))
            )
        ]

    async def complete(
        self, session: aiohttp.ClientSession, payload: dict,
        *, timeout_seconds: int,
    ) -> tuple[str, int | None]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise AIProviderUnavailable("gemini_api_key_missing")
        model = str(payload.get("model") or PROVIDER_MODEL).strip()
        if not model or len(model) > 100:
            raise AIProviderUnavailable("gemini_model_invalid")
        streaming = bool(payload.get("stream"))
        action = "streamGenerateContent" if streaming else "generateContent"
        url = f"{self.api_endpoint}{quote(model, safe='-._')}:{action}"
        options = {
            "json": self._request_body(payload),
            "headers": {
                "Accept": "text/event-stream" if streaming else "application/json",
                "x-goog-api-key": api_key,
            },
            "timeout": aiohttp.ClientTimeout(
                total=timeout_seconds, connect=min(8, timeout_seconds),
            ),
        }
        if streaming:
            options["params"] = {"alt": "sse"}
        async with session.post(url, **options) as response:
            if response.status != 200:
                # Google returns a structured error status (for example
                # INVALID_ARGUMENT or RESOURCE_EXHAUSTED). Preserve only that
                # safe category, not provider response text that could echo
                # user-supplied request content.
                reason_code = ""
                try:
                    error_payload = await response.json(content_type=None)
                    error_info = (
                        error_payload.get("error", {})
                        if isinstance(error_payload, dict) else {}
                    )
                    if isinstance(error_info, dict):
                        raw_reason = str(error_info.get("status", "")).upper()
                        if re.fullmatch(r"[A-Z0-9_]{1,40}", raw_reason):
                            reason_code = "_" + raw_reason.lower()
                except (aiohttp.ClientError, ValueError, TypeError, AttributeError):
                    pass
                raise AIProviderUnavailable(
                    f"gemini_status_{response.status}{reason_code}",
                    retryable=response.status in RETRYABLE_PROVIDER_STATUSES,
                    status_code=response.status,
                )
            try:
                tokens_used = None
                if streaming:
                    chunks = []
                    while True:
                        raw_line = await response.content.readline()
                        if not raw_line:
                            break
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line.startswith("data:"):
                            continue
                        content = line[5:].strip()
                        if content == "[DONE]":
                            break
                        try:
                            event = json.loads(content)
                            chunks.extend(self._text_parts(event))
                            usage = event.get("usageMetadata", {})
                            if isinstance(usage, dict):
                                tokens_used = usage.get("totalTokenCount", tokens_used)
                        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
                            # Ignore malformed SSE events, not transport failures.
                            continue
                    answer = "".join(chunks)
                else:
                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise AIProviderUnavailable("gemini_invalid_response")
                    candidates = data.get("candidates", [])
                    if not isinstance(candidates, list) or not candidates:
                        raise AIProviderUnavailable("gemini_no_text_candidate")
                    answer = "".join(self._text_parts(data))
                    usage = data.get("usageMetadata", {})
                    if isinstance(usage, dict):
                        tokens_used = usage.get("totalTokenCount")
            except (AIProviderUnavailable, aiohttp.ClientError):
                raise
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                raise AIProviderUnavailable("gemini_invalid_response") from error
        return answer.strip(), tokens_used


async def complete_with_retries(
    session, payload: dict, *, provider: GeminiProvider,
    timeout_seconds: int, retry_count: int,
) -> tuple[str, int | None]:
    retries = max(0, min(int(retry_count), 3))
    for attempt in range(retries + 1):
        try:
            return await provider.complete(
                session, payload, timeout_seconds=timeout_seconds,
            )
        except AIProviderUnavailable as error:
            if not error.retryable or attempt >= retries:
                raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt >= retries:
                raise
        await asyncio.sleep(min(0.5 * (2 ** attempt), 2.0))
    raise AIProviderUnavailable("provider_request_failed")


async def request_with_fallback(
    session, payload: dict, *, timeout_seconds: float, retry_count: int,
    complete, logger,
) -> tuple[str, int | None]:
    """Try the primary model and fallback within one caller-visible time budget."""
    total_budget = max(0.01, float(timeout_seconds))
    requested_retries = max(0, min(int(retry_count), 3))
    primary_share = 0.70
    primary_budget = total_budget * primary_share
    fallback_budget = total_budget - primary_budget

    # Allocate the primary window across all configured retries and their
    # exponential backoff. For very short total budgets, reduce the retry
    # count rather than assigning each request an unusably tiny timeout.
    effective_retries = requested_retries

    def backoff_budget(count: int) -> float:
        return sum(min(0.5 * (2 ** attempt), 2.0) for attempt in range(count))

    while effective_retries > 0:
        planned_backoff = backoff_budget(effective_retries)
        attempt_timeout = (
            primary_budget - planned_backoff
        ) / (effective_retries + 1)
        if attempt_timeout >= 0.25:
            break
        effective_retries -= 1

    retry_backoff_budget = backoff_budget(effective_retries)
    per_attempt_timeout = max(
        0.01,
        (primary_budget - retry_backoff_budget) / (effective_retries + 1),
    )

    # Keep one overall deadline, but reserve explicit time for fallback. Each
    # primary attempt gets only a slice of the primary budget, so retries cannot
    # consume the entire timeout and silently prevent the fallback from running.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_budget
    try:
        async with asyncio.timeout(total_budget):
            try:
                async with asyncio.timeout(primary_budget):
                    return await complete(
                        session, payload,
                        timeout_seconds=per_attempt_timeout,
                        retry_count=effective_retries,
                    )
            except AIProviderUnavailable as primary_error:
                if (
                    primary_error.status_code not in FALLBACK_PROVIDER_STATUSES
                    or payload["model"] == FALLBACK_PROVIDER_MODEL
                ):
                    raise
                logger.warning(
                    "[AI] Gemini model %s returned a retryable API status; "
                    "trying fallback model %s.",
                    payload["model"], FALLBACK_PROVIDER_MODEL,
                )
            except asyncio.TimeoutError:
                if payload["model"] == FALLBACK_PROVIDER_MODEL:
                    raise
                logger.warning(
                    "[AI] Gemini primary model %s timed out within its budget; "
                    "trying fallback model %s.",
                    payload["model"], FALLBACK_PROVIDER_MODEL,
                )
            except aiohttp.ClientError as primary_transport_error:
                if payload["model"] == FALLBACK_PROVIDER_MODEL:
                    raise
                logger.warning(
                    "[AI] Gemini primary model %s had a transport error (%s); "
                    "trying fallback model %s.",
                    payload["model"],
                    type(primary_transport_error).__name__,
                    FALLBACK_PROVIDER_MODEL,
                )

            remaining = max(0.01, deadline - loop.time())
            # The reserved share is an upper bound, not extra time past the
            # request deadline. This also keeps short custom test budgets valid.
            fallback_timeout = min(fallback_budget, remaining)
            payload["model"] = FALLBACK_PROVIDER_MODEL
            return await complete(
                session, payload, timeout_seconds=fallback_timeout,
                retry_count=0,
            )
    except asyncio.TimeoutError:
        logger.warning(
            "[AI] Gemini primary and fallback exceeded total budget %.2fs.",
            total_budget,
        )
        raise
