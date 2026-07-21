"""Trace and debug asset helpers — JSONL events, JSON writes, log sanitization."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "x-api-key",
    "auth_token",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "credential",
    "private_key",
)


def append_jsonl_event(path: Path, *, event: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "payload": sanitize_for_log(payload),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sanitize_for_log(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sanitize_for_log(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if any(secret in str(key).lower() for secret in _SECRET_KEY_PARTS):
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = sanitize_for_log(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_log(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("bearer ", "basic ")):
        return "[REDACTED]"
    if isinstance(value, str):
        return _redact_url_userinfo(value)
    return value


def _redact_url_userinfo(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.username is None and parsed.password is None:
            return value
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit(
            (parsed.scheme, f"[REDACTED]@{host}{port}", parsed.path, parsed.query, parsed.fragment)
        )
    except ValueError:
        return "[REDACTED_URL]"
