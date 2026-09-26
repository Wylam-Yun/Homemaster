"""Append-only Harness evidence recording."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class HarnessRecorder:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    def record(self, payload: dict[str, Any]) -> str | None:
        if self.path is None:
            return None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return str(self.path)
