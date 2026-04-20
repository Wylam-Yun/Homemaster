"""NVIDIA Kimi LLM provider for Phase B high-level planning."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from pydantic import ValidationError

from task_brain.domain import HighLevelPlan


class KimiProviderError(RuntimeError):
    """Base provider error carrying a stable error type."""

    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


class KimiProviderAuthError(KimiProviderError):
    """Raised when API key is missing or rejected."""


class KimiProviderNetworkError(KimiProviderError):
    """Raised for transport/network failures."""


class KimiProviderResponseError(KimiProviderError):
    """Raised for malformed or non-success responses."""


class KimiProviderSchemaError(KimiProviderError):
    """Raised when completion cannot be validated as HighLevelPlan."""


class KimiPlanProvider:
    """OpenAI-compatible chat completion provider for Kimi planning."""

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
    DEFAULT_MODEL = "moonshotai/kimi-k2.5"
    DEFAULT_API_KEY_ENV = "NVIDIA_API_KEY"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 20.0,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"missing API key in env '{api_key_env}'",
            )

        self._api_key = api_key
        self._api_key_env = api_key_env
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_s = timeout_s
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=self._base_url, timeout=self._timeout_s)

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 20.0,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        client: httpx.Client | None = None,
    ) -> KimiPlanProvider:
        """Build provider from local environment only."""
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"missing API key in env '{api_key_env}'",
            )
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
            api_key_env=api_key_env,
            client=client,
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_key_env(self) -> str:
        return self._api_key_env

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate_plan(self, *, prompt: str) -> HighLevelPlan:
        """Call Kimi and validate response into ``HighLevelPlan``."""
        payload = {
            "model": self._model,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a high-level task planner. "
                        "Return JSON only that validates as HighLevelPlan."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._client.post("/chat/completions", headers=headers, json=payload)
        except httpx.RequestError as exc:
            raise KimiProviderNetworkError(
                error_type="network_error",
                message=f"kimi request failed: {exc}",
            ) from exc

        if response.status_code in {401, 403}:
            raise KimiProviderAuthError(
                error_type="auth_rejected",
                message=f"kimi auth rejected with status={response.status_code}",
            )

        if response.status_code >= 400:
            error_message = _extract_error_message(response)
            raise KimiProviderResponseError(
                error_type="http_error",
                message=f"kimi http status={response.status_code}: {error_message}",
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise KimiProviderResponseError(
                error_type="response_not_json",
                message="kimi response body is not valid JSON",
            ) from exc

        content = _extract_message_content(body)
        plan_payload = _extract_json_payload(content)

        try:
            return HighLevelPlan.model_validate(plan_payload)
        except ValidationError as exc:
            raise KimiProviderSchemaError(
                error_type="schema_validation_error",
                message=f"llm plan schema validation failed: {exc}",
            ) from exc


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or "unknown error"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return response.text.strip() or "unknown error"


def _extract_message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise KimiProviderResponseError(
            error_type="response_missing_choices",
            message="kimi response missing choices",
        )

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise KimiProviderResponseError(
            error_type="response_missing_message",
            message="kimi response missing message payload",
        )

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content

    # Some compatible APIs may return list[parts].
    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_chunks.append(text)
        joined = "\n".join(text_chunks).strip()
        if joined:
            return joined

    raise KimiProviderResponseError(
        error_type="response_missing_content",
        message="kimi response missing textual content",
    )


def _extract_json_payload(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, count=1).strip()
        cleaned = re.sub(r"```$", "", cleaned, count=1).strip()

    for candidate in (cleaned, _extract_braced_block(cleaned)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload

    raise KimiProviderSchemaError(
        error_type="response_not_plan_json",
        message="llm content is not parseable JSON object",
    )


def _extract_braced_block(content: str) -> str | None:
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if match is None:
        return None
    return match.group(0)


__all__ = [
    "KimiPlanProvider",
    "KimiProviderAuthError",
    "KimiProviderError",
    "KimiProviderNetworkError",
    "KimiProviderResponseError",
    "KimiProviderSchemaError",
]
