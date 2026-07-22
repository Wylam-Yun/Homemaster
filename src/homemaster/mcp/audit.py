"""Append-only structured audit sink for MCP lifecycle and protocol boundaries."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path


class McpAuditLog:
    """Write one canonical JSON object per MCP audit event."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, object]) -> None:
        payload = {"timestamp": datetime.now(UTC).isoformat(), **event}
        encoded = (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        with self._lock:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


__all__ = ["McpAuditLog"]
