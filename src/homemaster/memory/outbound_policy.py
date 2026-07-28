"""Fail-closed policy for text sent to the configured embedding endpoint."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_FORBIDDEN_TEXT = re.compile(
    r"(?:memory-evidence-[0-9a-f]{16,}|"
    r"(?:api[_-]?key|password|passwd|secret|credential|session[_-]?token|access[_-]?token)"
    r"\s*[:=]\s*\S+|"
    r"https?://[^\s/@]+:[^\s/@]+@)",
    re.IGNORECASE,
)


class MemoryOutboundPolicyError(RuntimeError):
    pass


def validate_embedding_endpoint(base_url: str, embedding_url: str) -> str:
    base = urlsplit(base_url)
    endpoint = urlsplit(embedding_url)
    if base.scheme not in {"http", "https"} or not base.netloc:
        raise MemoryOutboundPolicyError("embedding base URL must be absolute http/https")
    if endpoint.username is not None or endpoint.password is not None:
        raise MemoryOutboundPolicyError("embedding endpoint must not contain userinfo")
    if endpoint.query or endpoint.fragment:
        raise MemoryOutboundPolicyError("embedding endpoint must not contain query or fragment")
    if (endpoint.scheme, endpoint.netloc) != (base.scheme, base.netloc):
        raise MemoryOutboundPolicyError("embedding endpoint origin differs from provider base URL")
    expected_path = base.path.rstrip("/") + "/embeddings"
    if endpoint.path != expected_path:
        raise MemoryOutboundPolicyError(
            "embedding endpoint path must be the exact provider embeddings path"
        )
    return embedding_url


def validate_embedding_text(text: str) -> None:
    if _FORBIDDEN_TEXT.search(text):
        raise MemoryOutboundPolicyError("embedding text contains prohibited outbound content")


__all__ = [
    "MemoryOutboundPolicyError",
    "validate_embedding_endpoint",
    "validate_embedding_text",
]
