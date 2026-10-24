
"""AI provider clients for Gemini and OpenRouter.

Both clients expose the same generate() interface so chat_service.py
can use either provider without changing its conversation logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from errors import AIError


PUBLIC_UNAVAILABLE = (
    "The AI service is temporarily unavailable. Please try again later."
)


def _truncate(value: Any, limit: int = 500) -> str:
    """Convert an upstream value to a short, log-safe string."""
    if value is None:
        return ""

    text = str(value).replace("\r", " ").replace("\n", " ").strip()

    if len(text) > limit:
        return text[:limit] + "..."

    return text


@dataclass
class AIResult:
    """Normalized result returned by an AI provider."""

    text: str
    model: str
    finish_reason: Optional[str]
    latency_ms: int


class GeminiClient:
    """Client for Google's Gemini generateContent API."""

    BASE_URL = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
    )

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 20.0,
        max_output_tokens: int = 1000,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self.model)

    def __repr__(self) -> str:
        # Never include the API key in the representation.
        return (
            f"GeminiClient(model={self.model!r}, "
            f"configured={self.configured})"
        )

    def generate(
        self,
        contents: List[Dict[str, Any]],
        *,
        temperature: float,
        system_instruction: Optional[str] = None,
    ) -> AIResult:
        if not self.configured:
            raise AIError(
                "ai_not_configured",
                "The AI service is not configured on this server.",
                503,
                detail="GEMINI_API_KEY and/or GEMINI_MODEL is not set",
            )

        url = f"{self.BASE_URL}{self.model}:generateContent"

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": self.max_output_tokens,
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        # Send the key in a header, never in the URL or request body.
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }

        started = time.perf_counter()

        try:
            response = self._session.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            raise AIError(
                "ai_timeout",
                "The AI service took too long to respond. Please try again.",
                504,
                detail="Gemini request timed out",
            ) from None
        except requests.exceptions.RequestException as exc:
            # Do not include exception text; it could contain sensitive data.
            raise AIError(
                "ai_unreachable",
                "Could not reach the AI service. Please check the connection and try again.",
                502,
                detail=f"Gemini request failed: {type(exc).__name__}",
            ) from None

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            raise self._map_http_error(response)

        return self._parse_success(response, latency_ms)

    def _map_http_error(self, response: requests.Response) -> AIError:
        status = response.status_code
        upstream_msg = ""

        try:
            body = response.json()
            if isinstance(body, dict):
                error = body.get("error", {})
                if isinstance(error, dict):
                    upstream_msg = _truncate(error.get("message", ""))
        except (ValueError, TypeError):
            upstream_msg = ""

        detail = f"Gemini HTTP {status}: {upstream_msg}"
        lower_msg = upstream_msg.lower()

        if (
            status in (401, 403)
            or (
                status == 400
                and (
                    "api key not valid" in lower_msg
                    or "invalid api key" in lower_msg
                    or "api_key_invalid" in lower_msg
                )
            )
        ):
            return AIError(
                "ai_auth_error",
                "Gemini could not authorize the request. Please check the API key.",
                503,
                detail=detail,
            )

        if status == 400:
            return AIError(
                "ai_bad_request",
                "The AI service could not process this request.",
                502,
                detail=detail,
            )

        if status == 404:
            return AIError(
                "ai_model_unavailable",
                "The configured Gemini model is unavailable. Please check GEMINI_MODEL.",
                503,
                detail=(
                    f"{detail}; check GEMINI_MODEL configuration"
                ),
            )

        if status == 429:
            retry_after = response.headers.get("Retry-After", "").strip()
            headers = (
                {"Retry-After": retry_after}
                if retry_after.isdigit()
                else {}
            )

            return AIError(
                "ai_rate_limited",
                "The Gemini usage limit has been reached. Please wait and try again later.",
                503,
                headers=headers,
                detail=detail,
            )

        if status in (408, 504):
            return AIError(
                "ai_timeout",
                "The AI service took too long to respond. Please try again.",
                504,
                detail=detail,
            )

        return AIError(
            "ai_upstream_error",
            PUBLIC_UNAVAILABLE,
            502,
            detail=detail,
        )

    def _parse_success(
        self,
        response: requests.Response,
        latency_ms: int,
    ) -> AIResult:
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise AIError(
                "ai_bad_response",
                PUBLIC_UNAVAILABLE,
                502,
                detail="Gemini returned a non-JSON response",
            ) from None

        if not isinstance(data, dict):
            raise AIError(
                "ai_bad_response",
                PUBLIC_UNAVAILABLE,
                502,
                detail="unexpected Gemini JSON shape",
            )

        prompt_feedback = data.get("promptFeedback") or {}
        if (
            isinstance(prompt_feedback, dict)
            and prompt_feedback.get("blockReason")
        ):
            raise AIError(
                "ai_blocked",
                "The request was blocked by the AI provider's safety filters. Please rephrase it.",
                422,
                detail=(
                    "Gemini prompt blocked: "
                    f"{_truncate(prompt_feedback.get('blockReason'))}"
                ),
            )

        candidates = data.get("candidates") or []
        candidate = (
            candidates[0]
            if candidates and isinstance(candidates[0], dict)
            else {}
        )

        finish_reason = candidate.get("finishReason")

        if finish_reason in ("SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT"):
            raise AIError(
                "ai_blocked",
                "The request was blocked by the AI provider's safety filters. Please rephrase it.",
                422,
                detail=f"Gemini candidate blocked: {_truncate(finish_reason)}",
            )

        content = candidate.get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else []
        parts = parts or []

        # Skip thought parts and join only user-facing text.
        text_parts = [
            part["text"]
            for part in parts
            if isinstance(part, dict)
            and isinstance(part.get("text"), str)
            and not part.get("thought", False)
        ]

        reply = "".join(text_parts).strip()

        if not reply:
            raise AIError(
                "ai_empty_response",
                "The AI service returned an empty answer. Please try again.",
                502,
                detail=(
                    "no text in Gemini response "
                    f"(finishReason={_truncate(finish_reason)})"
                ),
            )

        return AIResult(
            text=reply,
            model=self.model,
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )


class OpenRouterClient:
    """Client for OpenRouter's OpenAI-compatible chat-completions API.

    Accepts Gemini-shaped contents so chat_service.py remains unchanged.
    """

    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 20.0,
        max_output_tokens: int = 1000,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._session = session or requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self.model)

    def __repr__(self) -> str:
        # Never reveal the API key.
        return (
            f"OpenRouterClient(model={self.model!r}, "
            f"configured={self.configured})"
        )

    def generate(
        self,
        contents: List[Dict[str, Any]],
        *,
        temperature: float,
        system_instruction: Optional[str] = None,
    ) -> AIResult:
        if not self.configured:
            raise AIError(
                "ai_not_configured",
                "The AI service is not configured on this server.",
                503,
                detail="OPENROUTER_API_KEY and/or OPENROUTER_MODEL is not set",
            )

        messages: List[Dict[str, str]] = []

        if system_instruction:
            messages.append(
                {"role": "system", "content": system_instruction}
            )

        for item in contents:
            if not isinstance(item, dict):
                continue

            gemini_role = item.get("role")

            if gemini_role == "model":
                role = "assistant"
            elif gemini_role == "user":
                role = "user"
            else:
                continue

            parts = item.get("parts") or []
            text_parts = [
                part["text"]
                for part in parts
                if isinstance(part, dict)
                and isinstance(part.get("text"), str)
            ]

            content = "".join(text_parts).strip()

            if content:
                messages.append({"role": role, "content": content})

        if not messages:
            raise AIError(
                "ai_bad_request",
                "Please enter a message and try again.",
                400,
                detail="no valid messages supplied",
            )

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self.max_output_tokens,
            "stream": False,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()

        try:
            response = self._session.post(
                self.BASE_URL,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            raise AIError(
                "ai_timeout",
                "The AI service took too long to respond. Please try again.",
                504,
                detail="OpenRouter request timed out",
            ) from None
        except requests.exceptions.RequestException as exc:
            raise AIError(
                "ai_unreachable",
                "Could not reach the AI service. Please check the connection and try again.",
                502,
                detail=f"OpenRouter request failed: {type(exc).__name__}",
            ) from None

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code != 200:
            raise self._map_http_error(response)

        return self._parse_success(response, latency_ms)

    def _map_http_error(self, response: requests.Response) -> AIError:
        status = response.status_code
        upstream_msg = ""

        try:
            body = response.json()
            error = body.get("error", {}) if isinstance(body, dict) else {}

            if isinstance(error, dict):
                upstream_msg = _truncate(error.get("message", ""))
        except (ValueError, TypeError):
            upstream_msg = ""

        detail = f"OpenRouter HTTP {status}: {upstream_msg}"

        if status in (401, 403):
            return AIError(
                "ai_auth_error",
                "OpenRouter could not authorize the request. Please check your API key.",
                503,
                detail=detail,
            )

        if status == 402:
            return AIError(
                "ai_free_tier_unavailable",
                "OpenRouter could not process this request on the current account. "
                "Check the model's free availability and your account limits.",
                503,
                detail=detail,
            )

        if status == 404:
            return AIError(
                "ai_model_unavailable",
                "The configured OpenRouter model is unavailable. Please check OPENROUTER_MODEL.",
                503,
                detail=detail,
            )

        if status == 429:
            retry_after = response.headers.get("Retry-After", "").strip()
            headers = (
                {"Retry-After": retry_after}
                if retry_after.isdigit()
                else {}
            )

            return AIError(
                "ai_rate_limited",
                "The OpenRouter model is busy or its usage limit has been reached. "
                "Please wait and try again later.",
                503,
                headers=headers,
                detail=detail,
            )

        if status in (408, 504):
            return AIError(
                "ai_timeout",
                "The AI service took too long to respond. Please try again.",
                504,
                detail=detail,
            )

        return AIError(
            "ai_upstream_error",
            PUBLIC_UNAVAILABLE,
            502,
            detail=detail,
        )

    def _parse_success(
        self,
        response: requests.Response,
        latency_ms: int,
    ) -> AIResult:
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise AIError(
                "ai_bad_response",
                PUBLIC_UNAVAILABLE,
                502,
                detail="OpenRouter returned a non-JSON response",
            ) from None

        if not isinstance(data, dict):
            raise AIError(
                "ai_bad_response",
                PUBLIC_UNAVAILABLE,
                502,
                detail="unexpected OpenRouter JSON shape",
            )

        choices = data.get("choices") or []
        choice = (
            choices[0]
            if choices and isinstance(choices[0], dict)
            else {}
        )

        finish_reason = choice.get("finish_reason")
        message = choice.get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            reply = content.strip()
        elif isinstance(content, list):
            reply = "".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
                and isinstance(block.get("text"), str)
            ).strip()
        else:
            reply = ""

        if not reply:
            raise AIError(
                "ai_empty_response",
                "The AI service returned an empty answer. Please try again.",
                502,
                detail=(
                    "no text in OpenRouter response "
                    f"(finish_reason={_truncate(finish_reason)})"
                ),
            )

        return AIResult(
            text=reply,
            model=str(data.get("model") or self.model),
            finish_reason=finish_reason,
            latency_ms=latency_ms,
        )
