"""Utilities for JSON object extraction from model output."""

from __future__ import annotations

import json
import re
from typing import Any

from homemaster.providers.errors import LLMProviderError


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
            raise LLMProviderError(
                error_type="response_not_json_object",
                message="model output did not contain a JSON object",
                raw_content=content,
            ) from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except ValueError as exc:
            raise LLMProviderError(
                error_type="response_not_json_object",
                message="model output did not contain parseable JSON",
                raw_content=content,
            ) from exc
    if not isinstance(payload, dict):
        raise LLMProviderError(
            error_type="response_not_json_object",
            message="model output JSON was not an object",
            raw_content=content,
        )
    return payload
