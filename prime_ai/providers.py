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


class TokenUsage(int):
    """Total token count with optional split counts, backward-compatible as int."""

    def __new__(
        cls, total_tokens: int, prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ):
        value = int.__new__(cls, max(0, int(total_tokens)))
        value.prompt_tokens = (
            max(0, int(prompt_tokens)) if prompt_tokens is not None else None
        )
        value.completion_tokens = (
            max(0, int(completion_tokens)) if completion_tokens is not None else None
        )
        return value


def _token_usage(metadata: dict) -> TokenUsage | None:
    if not isinstance(metadata, dict):
        return None
    prompt = metadata.get("promptTokenCount")
    total = metadata.get("totalTokenCount")
    output = metadata.get("candidatesTokenCount")
    if total is not None and prompt is not None:
        try:
            # Prefer total minus prompt because Gemini total usage can include
            # separate thinking tokens that candidatesTokenCount may omit.
            output = max(0, int(total) - int(prompt))
        except (TypeError, ValueError, OverflowError):
            output = metadata.get("candidatesTokenCount")
    if total is None and prompt is not None and output is not None:
        try:
            total = int(prompt) + int(output)
        except (TypeError, ValueError, OverflowError):
            total = None
    if total is None:
        return None
    try:
        return TokenUsage(total, prompt, output)
    except (TypeError, ValueError, OverflowError):
        return None


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
                                tokens_used = _token_usage(usage) or tokens_used
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
                        tokens_used = _token_usage(usage)
            except (AIProviderUnavailable, aiohttp.ClientError):
                raise
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                raise AIProviderUnavailable("gemini_invalid_response") from error
        return answer.strip(), tokens_used


class OpenAICompatibleProvider:
    """Text-only fallback adapter for OpenAI-compatible chat-completions APIs."""

    def __init__(
        self, *, name: str, provider_key: str, endpoint: str,
        api_key_env: str, model_env: str, default_model: str,
        extra_headers: dict[str, str] | None = None,
    ):
        self.name = name
        self.provider_key = provider_key
        self.endpoint = endpoint
        self.api_key_env = api_key_env
        self.model_env = model_env
        self.default_model = default_model
        self.extra_headers = dict(extra_headers or {})

    def is_configured(self) -> bool:
        return bool(os.getenv(self.api_key_env, "").strip())

    def model_name(self) -> str:
        model = os.getenv(self.model_env, self.default_model).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/~-]{0,119}", model):
            raise AIProviderUnavailable(f"{self.provider_key}_model_invalid", status_code=503)
        return model

    async def complete(
        self, session, payload: dict, *, timeout_seconds: float,
    ) -> tuple[str, int | None]:
        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            raise AIProviderUnavailable(f"{self.provider_key}_api_key_missing", status_code=503)
        if session is None or getattr(session, "closed", False):
            raise AIProviderUnavailable("shared_http_session_unavailable", status_code=503)

        source_messages = payload.get("messages")
        if not isinstance(source_messages, list) or not source_messages:
            raise AIProviderUnavailable("openai_compatible_messages_invalid", status_code=400)
        messages = []
        for item in source_messages:
            if not isinstance(item, dict) or item.get("role") not in {"system", "user", "assistant"}:
                continue
            # Attachments stay on Gemini; member files are not silently forwarded
            # to another company during automatic failover.
            if item.get("media_parts"):
                raise AIProviderUnavailable("external_provider_multimodal_not_supported", status_code=400)
            content = item.get("content", "")
            if not isinstance(content, str):
                raise AIProviderUnavailable("openai_compatible_message_content_invalid", status_code=400)
            messages.append({"role": item["role"], "content": content})
        if not messages:
            raise AIProviderUnavailable("openai_compatible_messages_empty", status_code=400)

        model = self.model_name()
        try:
            temperature = max(0.0, min(2.0, float(payload.get("temperature", 0.7))))
            max_tokens = max(64, min(8192, int(payload.get("max_tokens", 1200))))
        except (TypeError, ValueError, OverflowError) as error:
            raise AIProviderUnavailable("openai_compatible_generation_settings_invalid", status_code=400) from error

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            **self.extra_headers,
        }
        try:
            async with session.post(
                self.endpoint,
                json=body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=max(1.0, min(float(timeout_seconds), 180.0)),
                    connect=5.0,
                ),
            ) as response:
                if response.status != 200:
                    raise AIProviderUnavailable(
                        f"{self.provider_key}_status_{response.status}",
                        retryable=response.status in FALLBACK_PROVIDER_STATUSES,
                        status_code=response.status,
                    )
                data = await response.json(content_type=None)
        except AIProviderUnavailable:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise
        except (ValueError, TypeError, AttributeError) as error:
            raise AIProviderUnavailable(
                f"{self.provider_key}_invalid_response", status_code=502,
            ) from error

        try:
            choices = data.get("choices", []) if isinstance(data, dict) else []
            first = choices[0] if isinstance(choices, list) and choices else {}
            message = first.get("message", {}) if isinstance(first, dict) else {}
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )
            answer = str(content or "").strip()
            usage = data.get("usage", {}) if isinstance(data, dict) else {}
            prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
            total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
            if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
                total_tokens = int(prompt_tokens) + int(completion_tokens)
            tokens_used = (
                TokenUsage(total_tokens, prompt_tokens, completion_tokens)
                if total_tokens is not None else None
            )
            actual_model = str(data.get("model") or model) if isinstance(data, dict) else model
        except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
            raise AIProviderUnavailable(
                f"{self.provider_key}_invalid_response", status_code=502,
            ) from error

        if not answer:
            raise AIProviderUnavailable(f"{self.provider_key}_empty_response", status_code=502)
        payload["_actual_provider"] = self.name
        payload["_actual_model"] = actual_model[:140]
        return answer, tokens_used


OPENROUTER_PROVIDER = OpenAICompatibleProvider(
    name="OpenRouter",
    provider_key="openrouter",
    endpoint="https://openrouter.ai/api/v1/chat/completions",
    api_key_env="OPENROUTER_API_KEY",
    model_env="PRIME_OPENROUTER_MODEL",
    default_model="openai/gpt-oss-20b",
    extra_headers={"X-OpenRouter-Title": "PRIME Discord Bot"},
)
GROQ_PROVIDER = OpenAICompatibleProvider(
    name="Groq",
    provider_key="groq",
    endpoint="https://api.groq.com/openai/v1/chat/completions",
    api_key_env="GROQ_API_KEY",
    model_env="PRIME_GROQ_MODEL",
    default_model="openai/gpt-oss-20b",
)


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

async def request_with_provider_fallback(
    session, payload: dict, *, timeout_seconds: float, retry_count: int,
    complete, logger, fallback_providers=(),
) -> tuple[str, int | None]:
    """Try Gemini models first, then configured independent text providers."""
    messages = payload.get("messages", [])
    has_attachments = isinstance(messages, list) and any(
        isinstance(message, dict) and message.get("media_parts")
        for message in messages
    )
    if has_attachments:
        return await request_with_fallback(
            session, payload, timeout_seconds=timeout_seconds,
            retry_count=retry_count, complete=complete, logger=logger,
        )

    configured = [
        provider for provider in fallback_providers
        if callable(getattr(provider, "is_configured", None))
        and provider.is_configured()
    ]
    if not configured:
        return await request_with_fallback(
            session, payload, timeout_seconds=timeout_seconds,
            retry_count=retry_count, complete=complete, logger=logger,
        )

    total_budget = max(0.01, float(timeout_seconds))
    primary_fraction = 0.70 if len(configured) == 1 else 0.65
    primary_budget = max(0.01, total_budget * primary_fraction)
    deadline = asyncio.get_running_loop().time() + total_budget
    primary_error = None

    def eligible(error, *, after_gemini_lite=False):
        if isinstance(error, (asyncio.TimeoutError, aiohttp.ClientError)):
            return True
        if not isinstance(error, AIProviderUnavailable):
            return False
        reason = str(error).lower()
        if "_api_key_missing" in reason:
            return True
        if error.status_code in (FALLBACK_PROVIDER_STATUSES | {401, 402, 403, 404}):
            return True
        # Errors from the secondary model may be specific to that model; do not
        # automatically reroute non-retryable content/safety refusals.
        return after_gemini_lite and error.status_code == 404

    try:
        async with asyncio.timeout(primary_budget):
            return await request_with_fallback(
                session, payload, timeout_seconds=primary_budget,
                retry_count=retry_count, complete=complete, logger=logger,
            )
    except (AIProviderUnavailable, asyncio.TimeoutError, aiohttp.ClientError) as error:
        primary_error = error
        if not eligible(
            error,
            after_gemini_lite=str(payload.get("model") or "") == FALLBACK_PROVIDER_MODEL,
        ):
            raise
        logger.warning(
            "[AI] Gemini models unavailable; trying configured independent provider fallbacks (%s).",
            type(error).__name__,
        )

    last_error = primary_error
    for index, provider in enumerate(configured):
        remaining = max(0.01, deadline - asyncio.get_running_loop().time())
        attempt_budget = max(0.01, remaining / (len(configured) - index))
        try:
            model = provider.model_name()
            payload["model"] = model
            payload["_active_provider"] = provider.name
            payload["_active_model"] = model
            async with asyncio.timeout(attempt_budget):
                answer, tokens = await provider.complete(
                    session, payload, timeout_seconds=attempt_budget,
                )
            if not str(answer or "").strip():
                raise AIProviderUnavailable(
                    f"{provider.provider_key}_empty_response", status_code=502,
                )
            payload["_actual_provider"] = provider.name
            payload["_actual_model"] = str(payload.get("_actual_model") or model)
            logger.warning(
                "[AI] Response completed by fallback provider %s (model=%s).",
                provider.name, str(payload.get("_actual_model") or model)[:100],
            )
            return answer, tokens
        except (AIProviderUnavailable, asyncio.TimeoutError, aiohttp.ClientError) as fallback_error:
            last_error = fallback_error
            logger.warning(
                "[AI] Fallback provider %s failed (%s, status=%s).",
                getattr(provider, "name", "unknown"),
                type(fallback_error).__name__,
                getattr(fallback_error, "status_code", None),
            )
            if index < len(configured) - 1:
                status = getattr(fallback_error, "status_code", None)
                reason = str(fallback_error).lower()
                if (
                    isinstance(fallback_error, (asyncio.TimeoutError, aiohttp.ClientError))
                    or status in (FALLBACK_PROVIDER_STATUSES | {401, 402, 403, 404})
                    or "_api_key_missing" in reason
                ):
                    continue
            break

    if last_error is not None:
        raise last_error
    raise AIProviderUnavailable("all_ai_providers_unavailable", status_code=503)

