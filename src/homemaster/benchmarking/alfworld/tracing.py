"""Trace output for ALFWorld benchmark episodes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SECRET_KEY_FRAGMENTS = ("api_key", "token", "auth", "secret", "password")


class AlfworldTraceWriter:
    def __init__(self, episode_dir: Path) -> None:
        self.episode_dir = episode_dir
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.episode_dir / "trace.jsonl"
        self.summary_path = self.episode_dir / "summary.json"

    def write_event(self, event: dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
            writer.write("\n")

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(
            json.dumps(_redact(summary), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and any(
        fragment in key.lower() for fragment in SECRET_KEY_FRAGMENTS
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
