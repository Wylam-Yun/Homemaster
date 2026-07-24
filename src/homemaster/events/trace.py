"""Trace and debug asset helpers for exact JSON-compatible values."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def append_jsonl_event(path: Path, *, event: str, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        "payload": json_compatible_copy(payload),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            json_compatible_copy(payload),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def json_compatible_copy(value: Any) -> Any:
    """Return an exact JSON-compatible recursive copy."""

    if isinstance(value, dict):
        return {str(key): json_compatible_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_compatible_copy(item) for item in value]
    if isinstance(value, tuple):
        return [json_compatible_copy(item) for item in value]
    return value
