from __future__ import annotations

import json

import httpx
import pytest

from homemaster.embedding_client import (
    BGEEmbeddingClient,
    EmbeddingProviderResponseError,
    derive_embeddings_url,
)
from homemaster.runtime import ProviderConfig
from homemaster.trace import sanitize_for_log


def _provider(**overrides: object) -> ProviderConfig:
    payload = {
        "name": "MemoryEmbedding",
        "base_url": "https://api.example.cn/v1/messages",
        "model": "BAAI/bge-m3",
        "api_keys": ("secret-one",),
        "protocol": "anthropic",
        "embedding_url": None,
    }
    payload.update(overrides)
    return ProviderConfig(**payload)


def test_derive_embeddings_url_from_messages_base_url() -> None:
    assert (
        derive_embeddings_url("https://api.siliconflow.cn/v1/messages")
        == "https://api.siliconflow.cn/v1/embeddings"
    )
    assert (
        derive_embeddings_url("https://api.siliconflow.cn/v1")
        == "https://api.siliconflow.cn/v1/embeddings"
    )


def test_embedding_client_posts_to_embeddings_endpoint_and_redacts_secret() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(
            {
                "url": str(request.url),
                "authorization": request.headers.get("authorization"),
                "model": body["model"],
                "input": body["input"],
            }
        )
        return httpx.Response(
            status_code=200,
            json={
                "data": [
                    {"index": 0, "embedding": [1.0, 0.0, 0.0]},
                    {"index": 1, "embedding": [0.0, 1.0, 0.0]},
                ]
            },
        )

    provider = _provider(embedding_url="https://api.example.cn/v1/embeddings")
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        response = BGEEmbeddingClient(provider, client=http_client).embed_texts(["水杯", "药盒"])

    assert response.embeddings == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert response.public_summary()["provider_name"] == "MemoryEmbedding"
    assert requests == [
        {
            "url": "https://api.example.cn/v1/embeddings",
            "authorization": "Bearer secret-one",
            "model": "BAAI/bge-m3",
            "input": ["水杯", "药盒"],
        }
    ]
    encoded_summary = json.dumps(sanitize_for_log(response.public_summary()), ensure_ascii=False)
    assert "secret-one" not in encoded_summary


def test_embedding_client_falls_back_to_second_key_after_http_error() -> None:
    seen_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers.get("authorization"))
        if len(seen_authorization) == 1:
            return httpx.Response(status_code=401, json={"error": {"message": "bad key"}})
        return httpx.Response(
            status_code=200,
            json={"data": [{"index": 0, "embedding": [0.5, 0.5]}]},
        )

    provider = _provider(api_keys=("bad-secret", "good-secret"))
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        response = BGEEmbeddingClient(provider, client=http_client).embed_texts(["厨房 水杯"])

    assert response.embeddings == [[0.5, 0.5]]
    assert seen_authorization == ["Bearer bad-secret", "Bearer good-secret"]
    assert response.attempts[0]["status_code"] == 401
    assert response.attempts[1]["status_code"] == 200


def test_embedding_client_rejects_malformed_embedding_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json={"data": [{"index": 0, "embedding": "oops"}]})

    provider = _provider()
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        with pytest.raises(EmbeddingProviderResponseError) as exc_info:
            BGEEmbeddingClient(provider, client=http_client).embed_texts(["水杯"])

    assert exc_info.value.error_type == "embedding_response_invalid"
