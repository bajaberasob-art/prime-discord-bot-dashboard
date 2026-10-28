"""HTTP adapters and bounded request policy; no guild, database or Discord state."""

from __future__ import annotations

import asyncio
import base64
import binascii
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


class PollinationsImageProvider:
    """Opt-in image adapter for the current Pollinations generation API.

    Requires a Pollinations API key. It is not described as unlimited or
    guaranteed free; the account's current limits and terms apply.
    """

    image_endpoint = "https://gen.pollinations.ai/image/"

    async def generate(
        self, session: aiohttp.ClientSession, prompt: str, *,
        timeout_seconds: int = 90, aspect_ratio: str = "1:1",
    ) -> tuple[bytes, str]:
        api_key = os.getenv("POLLINATIONS_API_KEY", "").strip()
        if not api_key:
            raise AIProviderUnavailable("pollinations_api_key_missing")
        value = str(prompt or "").strip()
        if not value or len(value) > 1800:
            raise ValueError("invalid_image_prompt")
        if session is None or getattr(session, "closed", False):
            raise AIProviderUnavailable("shared_http_session_unavailable")
        dimensions = {
            "1:1": (1024, 1024), "3:2": (1024, 682), "2:3": (682, 1024),
            "4:3": (1024, 768), "3:4": (768, 1024),
            "16:9": (1024, 576), "9:16": (576, 1024),
        }
        if aspect_ratio not in dimensions:
            raise ValueError("invalid_image_aspect_ratio")
        width, height = dimensions[aspect_ratio]
        url = self.image_endpoint + quote(value, safe="")
        async with session.get(
            url,
            params={"width": width, "height": height, "model": os.getenv("POLLINATIONS_IMAGE_MODEL", "flux"), "nologo": "true"},
            headers={"Authorization": f"Bearer {api_key}", "Accept": "image/*"},
            timeout=aiohttp.ClientTimeout(
                total=max(10, min(int(timeout_seconds), 120)), connect=8,
            ),
        ) as response:
            if response.status != 200:
                raise AIProviderUnavailable(
                    f"pollinations_image_status_{response.status}",
                    retryable=response.status in RETRYABLE_PROVIDER_STATUSES,
                    status_code=response.status,
                )
            mime = str(response.headers.get("Content-Type", "")).split(";", 1)[0].lower()
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                raise AIProviderUnavailable("pollinations_image_invalid_content_type")
            raw = await response.read()
            if not raw or len(raw) > 8 * 1024 * 1024:
                raise AIProviderUnavailable("pollinations_image_invalid_size")
            return raw, mime


class GeminiImageProvider:
    """Generate real image bytes with Gemini's native image-generation model."""

    api_endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    default_model = "gemini-3.1-flash-image"

    @staticmethod
    def _image_output(data: dict) -> tuple[bytes, str]:
        if not isinstance(data, dict):
            raise AIProviderUnavailable("gemini_image_invalid_response")
        direct = data.get("output_image")
        if isinstance(direct, dict) and isinstance(direct.get("data"), str):
            candidates = [direct]
        else:
            candidates = []

            def walk(node):
                if isinstance(node, dict):
                    if (
                        isinstance(node.get("data"), str)
                        and (
                            str(node.get("type", "")).lower() in {"image", "image_output", "output_image"}
                            or str(node.get("mime_type", "")).lower().startswith("image/")
                        )
                    ):
                        candidates.append(node)
                    # Walk output steps; request echoes / user input are not model results.
                    for key in ("output", "steps", "content", "parts", "model_output"):
                        if key in node:
                            walk(node[key])
                elif isinstance(node, list):
                    for item in node:
                        walk(item)

            walk(data.get("output", []))
            walk(data.get("steps", []))
        for item in reversed(candidates):
            encoded = item.get("data")
            mime = str(item.get("mime_type") or item.get("mimeType") or "image/png").lower()
            if mime not in {"image/png", "image/jpeg", "image/webp"}:
                continue
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError, binascii.Error):
                continue
            if image_bytes and len(image_bytes) <= 8 * 1024 * 1024:
                return image_bytes, mime
        raise AIProviderUnavailable("gemini_image_missing_output")

    async def generate(
        self,
        session: aiohttp.ClientSession,
        prompt: str,
        *,
        reference_image: dict | None = None,
        timeout_seconds: int = 120,
        aspect_ratio: str = "1:1",
    ) -> tuple[bytes, str]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise AIProviderUnavailable("gemini_api_key_missing")
        value = str(prompt or "").strip()
        if not value or len(value) > 1800:
            raise ValueError("invalid_image_prompt")
        if session is None or getattr(session, "closed", False):
            raise AIProviderUnavailable("shared_http_session_unavailable")
        ratios = {"1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"}
        if aspect_ratio not in ratios:
            raise ValueError("invalid_image_aspect_ratio")

        input_parts = [{"type": "text", "text": value}]
        if reference_image is not None:
            if not isinstance(reference_image, dict):
                raise ValueError("invalid_image_reference")
            mime = str(reference_image.get("mime_type") or "").lower()
            encoded = reference_image.get("data")
            if mime not in {"image/png", "image/jpeg", "image/webp"} or not isinstance(encoded, str):
                raise ValueError("invalid_image_reference")
            if len(encoded) > 8 * 1024 * 1024:
                raise ValueError("image_reference_too_large")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError, binascii.Error):
                raise ValueError("invalid_image_reference") from None
            if not raw or len(raw) > 6 * 1024 * 1024:
                raise ValueError("image_reference_too_large")
            input_parts.append({
                "type": "image",
                "mime_type": mime,
                "data": encoded,
            })

        model = os.getenv("PRIME_IMAGE_MODEL", self.default_model).strip()
        if not model or len(model) > 100:
            raise AIProviderUnavailable("gemini_image_model_invalid")
        body = {
            "model": model,
            "input": input_parts,
            "store": False,
            "response_format": {
                "type": "image",
                "mime_type": "image/png",
                "aspect_ratio": aspect_ratio,
                "image_size": "1K",
            },
        }
        async with session.post(
            self.api_endpoint,
            json=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            timeout=aiohttp.ClientTimeout(
                total=max(10, min(int(timeout_seconds), 180)),
                connect=8,
            ),
        ) as response:
            if response.status != 200:
                reason_code = ""
                try:
                    payload = await response.json(content_type=None)
                    err = payload.get("error", {}) if isinstance(payload, dict) else {}
                    status = str(err.get("status", "")).upper() if isinstance(err, dict) else ""
                    if re.fullmatch(r"[A-Z0-9_]{1,40}", status):
                        reason_code = "_" + status.lower()
                except (aiohttp.ClientError, ValueError, TypeError, AttributeError):
                    pass
                raise AIProviderUnavailable(
                    f"gemini_image_status_{response.status}{reason_code}",
                    retryable=response.status in RETRYABLE_PROVIDER_STATUSES,
                    status_code=response.status,
                )
            try:
                payload = await response.json(content_type=None)
            except (aiohttp.ClientError, ValueError, TypeError) as error:
                raise AIProviderUnavailable("gemini_image_invalid_response") from error
        return self._image_output(payload)


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
            parts = []
            text = item.get("content", "")
            if isinstance(text, str) and text.strip():
                parts.append({"text": text})
            media_parts = item.get("media_parts", [])
            if media_parts is not None:
                if not isinstance(media_parts, list):
                    raise ValueError("invalid_media_parts")
                for media in media_parts:
                    if not isinstance(media, dict):
                        raise ValueError("invalid_media_part")
                    media_type = media.get("type")
                    if media_type == "text":
                        extra_text = media.get("text")
                        if isinstance(extra_text, str) and extra_text.strip():
                            parts.append({"text": extra_text})
                        continue
                    mime_type = str(media.get("mime_type") or "").strip().lower()
                    encoded = media.get("data")
                    if (
                        media_type not in {"image", "audio", "document"}
                        or not isinstance(encoded, str)
                        or not encoded
                        or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime_type)
                    ):
                        raise ValueError("invalid_media_part")
                    parts.append({
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": encoded,
                        }
                    })
            if not parts:
                continue
            gemini_role = "model" if role == "assistant" else "user"
            if contents and contents[-1]["role"] == gemini_role:
                # Preserve the former newline-joined shape for adjacent
                # text-only messages. Keep explicit media and its explanatory
                # text as separate, ordered parts within the same request.
                if (
                    not media_parts
                    and len(parts) == 1
                    and set(parts[0]) == {"text"}
                    and len(contents[-1]["parts"]) == 1
                    and set(contents[-1]["parts"][0]) == {"text"}
                ):
                    contents[-1]["parts"][0]["text"] += "\n" + parts[0]["text"]
                else:
                    contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": gemini_role, "parts": parts})
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


class OpenRouterFreeProvider:
    """Optional OpenRouter free-router adapter for text-only chat fallback.

    This is opt-in because prompts are sent to a third-party provider. The
    OpenRouter free router may choose among eligible free models and is not
    an availability or quality guarantee.
    """

    api_endpoint = "https://openrouter.ai/api/v1/chat/completions"

    async def complete(self, session, payload: dict, *, timeout_seconds: int):
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            raise AIProviderUnavailable("openrouter_api_key_missing")
        model = os.getenv("OPENROUTER_FREE_MODEL", "openrouter/free").strip()
        if not model or len(model) > 160:
            raise AIProviderUnavailable("openrouter_model_invalid")
        messages = []
        for item in payload.get("messages", []):
            if not isinstance(item, dict) or item.get("role") not in {"system", "user", "assistant"}:
                continue
            # Do not silently discard multimodal inputs and then answer as if
            # they had been analyzed. This adapter is text-only by design.
            if item.get("media_parts"):
                raise AIProviderUnavailable("openrouter_text_only")
            content = item.get("content", "")
            if not isinstance(content, str):
                raise AIProviderUnavailable("openrouter_invalid_message")
            messages.append({"role": item["role"], "content": content})
        if not messages:
            raise AIProviderUnavailable("openrouter_empty_messages")
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max(1, min(int(payload.get("max_tokens", 1200)), 1600)),
            "temperature": max(0.0, min(float(payload.get("temperature", 0.7)), 1.5)),
            "stream": False,
        }
        async with session.post(
            self.api_endpoint,
            json=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://prime-panel.discloud.app"),
                "X-Title": "PRIME Discord Bot",
            },
            timeout=aiohttp.ClientTimeout(
                total=max(3, min(int(timeout_seconds), 45)), connect=8,
            ),
        ) as response:
            if response.status != 200:
                raise AIProviderUnavailable(
                    f"openrouter_status_{response.status}",
                    retryable=response.status in FALLBACK_PROVIDER_STATUSES,
                    status_code=response.status,
                )
            try:
                data = await response.json(content_type=None)
            except (aiohttp.ClientError, ValueError, TypeError) as error:
                raise AIProviderUnavailable("openrouter_invalid_response") from error
        try:
            choices = data.get("choices", []) if isinstance(data, dict) else []
            message = choices[0].get("message", {}) if choices else {}
            answer = message.get("content", "") if isinstance(message, dict) else ""
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
            if not isinstance(answer, str) or not answer.strip():
                raise AIProviderUnavailable("openrouter_no_text_candidate")
            if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                tokens = None
            return answer.strip(), tokens
        except (IndexError, KeyError, TypeError, AttributeError) as error:
            raise AIProviderUnavailable("openrouter_invalid_response") from error


async def request_with_openrouter_fallback(
    session, payload: dict, *, timeout_seconds: float, retry_count: int,
    complete, logger, allow_fallback: bool = False,
) -> tuple[str, int | None]:
    """Use OpenRouter's free router only after Gemini fails, when explicitly enabled."""
    try:
        return await request_with_fallback(
            session, payload, timeout_seconds=timeout_seconds,
            retry_count=retry_count, complete=complete, logger=logger,
        )
    except (AIProviderUnavailable, aiohttp.ClientError, asyncio.TimeoutError) as primary_error:
        api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not allow_fallback or not api_key:
            raise
        if any(
            isinstance(message, dict) and message.get("media_parts")
            for message in payload.get("messages", [])
        ):
            raise primary_error
        # Reserve a bounded portion of the caller's configured timeout for the
        # alternate provider. Never retry indefinitely or send multimodal data.
        fallback = OpenRouterFreeProvider()
        try:
            logger.warning("[AI] Gemini unavailable; attempting opt-in OpenRouter free-router fallback.")
            return await fallback.complete(
                session, payload, timeout_seconds=max(3, min(int(timeout_seconds), 45)),
            )
        except (AIProviderUnavailable, aiohttp.ClientError, asyncio.TimeoutError):
            raise primary_error


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
