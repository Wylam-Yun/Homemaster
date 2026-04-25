"""Minimal raw JSON LLM client for HomeMaster smoke tests."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from homemaster.runtime import ProviderConfig


class LLMClientError(RuntimeError):
    """Base LLM client error with a stable error type."""

    def __init__(
        self,
        *,
        error_type: str,
        message: str,
        raw_content: str | None = None,
    ) -> None:
        self.error_type = error_type
        self.message = message
        self.raw_content = raw_content
        super().__init__(message)


class LLMProviderNetworkError(LLMClientError):
    """Raised when the provider request cannot complete."""


class LLMProviderResponseError(LLMClientError):
    """Raised when the provider response cannot be parsed or accepted."""


@dataclass(frozen=True)
class LLMJsonResponse:
    provider_name: str
    model: str
    protocol: str
    content: str
    json_payload: dict[str, Any]
    elapsed_ms: float
    attempts: tuple[dict[str, Any], ...]

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "model": self.model,
            "protocol": self.protocol,
            "elapsed_ms": self.elapsed_ms,
            "attempts": list(self.attempts),
        }


class RawJsonLLMClient:
    """Call one configured provider and return a parsed JSON object."""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        timeout_s: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._provider = provider
        self._owns_client = client is None
        timeout = httpx.Timeout(connect=10.0, read=timeout_s, write=15.0, pool=10.0)
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete_json(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.0,
    ) -> LLMJsonResponse:
        errors: list[str] = []
        attempts: list[dict[str, Any]] = []
        last_raw_content: str | None = None

        for key_index, api_key in enumerate(self._provider.api_keys, start=1):
            started = time.perf_counter()
            try:
                response = self._send_prompt(
                    prompt,
                    api_key=api_key,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except httpx.RequestError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                attempts.append(
                    {"key_index": key_index, "status": "network_error", "elapsed_ms": elapsed_ms}
                )
                errors.append(f"key#{key_index}:network_error:{type(exc).__name__}")
                continue

            elapsed_ms = (time.perf_counter() - started) * 1000
            attempts.append(
                {
                    "key_index": key_index,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                }
            )
            if response.status_code >= 400:
                errors.append(
                    f"key#{key_index}:http_{response.status_code}:{_extract_error_message(response)}"
                )
                continue

            content = self._extract_content(response)
            last_raw_content = content
            try:
                payload = extract_json_payload(content)
            except LLMProviderResponseError as exc:
                errors.append(f"key#{key_index}:{exc.error_type}")
                continue

            return LLMJsonResponse(
                provider_name=self._provider.name,
                model=self._provider.model,
                protocol=self._provider.protocol,
                content=content,
                json_payload=payload,
                elapsed_ms=elapsed_ms,
                attempts=tuple(attempts),
            )

        if any("network_error" in item for item in errors):
            raise LLMProviderNetworkError(
                error_type="provider_network_error",
                message="all configured API keys failed: " + "; ".join(errors),
            )
        raise LLMProviderResponseError(
            error_type="provider_response_error",
            message="all configured API keys failed: " + "; ".join(errors),
            raw_content=last_raw_content,
        )

    def _send_prompt(
        self,
        prompt: str,
        *,
        api_key: str,
        max_tokens: int,
        temperature: float,
    ) -> httpx.Response:
        if self._provider.protocol == "anthropic":
            return self._client.post(
                f"{self._provider.base_url}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self._provider.model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )

        return self._client.post(
            f"{self._provider.base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._provider.model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            },
        )

    def _extract_content(self, response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError as exc:
            raise LLMProviderResponseError(
                error_type="response_not_json",
                message="provider response body is not JSON",
            ) from exc

        if self._provider.protocol == "anthropic":
            return _extract_anthropic_content(body)
        return _extract_openai_content(body)


def extract_json_payload(content: str) -> dict[str, Any]:
    """Extract one JSON object from plain or fenced model output."""

    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, count=1).strip()
    cleaned = re.sub(r"```$", "", cleaned, count=1).strip()

    try:
        payload = json.loads(cleaned)
    except ValueError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise LLMProviderResponseError(
                error_type="response_not_json_object",
                message="model output did not contain a JSON object",
            ) from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except ValueError as exc:
            raise LLMProviderResponseError(
                error_type="response_not_json_object",
                message="model output did not contain parseable JSON",
            ) from exc

    if not isinstance(payload, dict):
        raise LLMProviderResponseError(
            error_type="response_not_json_object",
            message="model output JSON was not an object",
        )
    return payload


def _extract_anthropic_content(body: dict[str, Any]) -> str:
    content = body.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(part for part in parts if part).strip()
        if text:
            return text
        thinking_parts = [
            item.get("thinking", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "thinking"
        ]
        thinking = "\n".join(part for part in thinking_parts if part).strip()
        if thinking:
            return thinking
    raise LLMProviderResponseError(
        error_type="response_missing_text",
        message="anthropic response missing textual content",
    )


def _extract_openai_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    raise LLMProviderResponseError(
        error_type="response_missing_text",
        message="openai response missing textual content",
    )


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return _sanitize_error_text(response.text)

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return _sanitize_error_text(message)
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return _sanitize_error_text(message)
    return _sanitize_error_text(response.text)


def _sanitize_error_text(value: str) -> str:
    compact = " ".join(value.split())
    return compact[:240] if compact else "unknown error"
