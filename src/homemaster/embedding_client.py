"""Embedding client for HomeMaster Stage 03 memory RAG."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from homemaster.runtime import ProviderConfig


class EmbeddingClientError(RuntimeError):
    """Base embedding error with a stable category."""

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


class EmbeddingProviderNetworkError(EmbeddingClientError):
    """Raised when an embedding provider request cannot complete."""


class EmbeddingProviderResponseError(EmbeddingClientError):
    """Raised when an embedding provider response cannot be accepted."""


@dataclass(frozen=True)
class EmbeddingResponse:
    provider_name: str
    model: str
    endpoint: str
    embeddings: list[list[float]]
    elapsed_ms: float
    attempts: tuple[dict[str, Any], ...]

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider_name": self.provider_name,
            "model": self.model,
            "endpoint": self.endpoint,
            "embedding_count": len(self.embeddings),
            "elapsed_ms": self.elapsed_ms,
            "attempts": list(self.attempts),
        }


def derive_embeddings_url(base_url: str) -> str:
    """Return the embeddings endpoint for an OpenAI-compatible base URL."""

    cleaned = base_url.rstrip("/")
    for suffix in ("/v1/messages", "/v1/chat/completions", "/messages", "/chat/completions"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    if cleaned.endswith("/v1"):
        return f"{cleaned}/embeddings"
    return f"{cleaned}/v1/embeddings"


class BGEEmbeddingClient:
    """Minimal BGE-M3 embeddings client using an OpenAI-compatible endpoint."""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        timeout_s: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._provider = provider
        self._endpoint = provider.embedding_url or derive_embeddings_url(provider.base_url)
        self._owns_client = client is None
        timeout = httpx.Timeout(connect=10.0, read=timeout_s, write=15.0, pool=10.0)
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def public_summary(self) -> dict[str, Any]:
        return {
            "provider_name": self._provider.name,
            "model": self._provider.model,
            "endpoint": self._endpoint,
        }

    def embed_texts(self, texts: Sequence[str]) -> EmbeddingResponse:
        clean_texts = [text for text in texts if isinstance(text, str) and text.strip()]
        if not clean_texts:
            raise EmbeddingProviderResponseError(
                error_type="embedding_input_empty",
                message="embedding input must contain at least one non-empty text",
            )

        attempts: list[dict[str, Any]] = []
        errors: list[str] = []
        last_raw_content: str | None = None
        for key_index, api_key in enumerate(self._provider.api_keys, start=1):
            started = time.perf_counter()
            try:
                response = self._client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self._provider.model, "input": clean_texts},
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

            last_raw_content = response.text
            embeddings = _extract_embeddings(response)
            if len(embeddings) != len(clean_texts):
                errors.append(f"key#{key_index}:embedding_count_mismatch")
                continue

            return EmbeddingResponse(
                provider_name=self._provider.name,
                model=self._provider.model,
                endpoint=self._endpoint,
                embeddings=embeddings,
                elapsed_ms=elapsed_ms,
                attempts=tuple(attempts),
            )

        if any("network_error" in item for item in errors):
            raise EmbeddingProviderNetworkError(
                error_type="embedding_provider_network_error",
                message="all configured embedding API keys failed: " + "; ".join(errors),
            )
        raise EmbeddingProviderResponseError(
            error_type="embedding_provider_response_error",
            message="all configured embedding API keys failed: " + "; ".join(errors),
            raw_content=last_raw_content,
        )


def _extract_embeddings(response: httpx.Response) -> list[list[float]]:
    try:
        body = response.json()
    except ValueError as exc:
        raise EmbeddingProviderResponseError(
            error_type="embedding_response_not_json",
            message="embedding response body is not JSON",
            raw_content=response.text,
        ) from exc

    raw_data = body.get("data")
    if not isinstance(raw_data, list):
        raise EmbeddingProviderResponseError(
            error_type="embedding_response_invalid",
            message="embedding response missing data list",
            raw_content=json.dumps(body, ensure_ascii=False),
        )

    ordered = sorted(
        (item for item in raw_data if isinstance(item, dict)),
        key=lambda item: int(item.get("index", 0)),
    )
    embeddings: list[list[float]] = []
    for item in ordered:
        raw_embedding = item.get("embedding")
        if not isinstance(raw_embedding, list) or not raw_embedding:
            raise EmbeddingProviderResponseError(
                error_type="embedding_response_invalid",
                message="embedding item missing numeric vector",
                raw_content=json.dumps(body, ensure_ascii=False),
            )
        try:
            embeddings.append([float(value) for value in raw_embedding])
        except (TypeError, ValueError) as exc:
            raise EmbeddingProviderResponseError(
                error_type="embedding_response_invalid",
                message="embedding item contains non-numeric value",
                raw_content=json.dumps(body, ensure_ascii=False),
            ) from exc
    return embeddings


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:200]
        if isinstance(payload.get("message"), str):
            return payload["message"][:200]
    return json.dumps(payload, ensure_ascii=False)[:200]
