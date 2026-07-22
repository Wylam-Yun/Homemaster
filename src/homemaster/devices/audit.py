"""Append-only structured audit sink for physical-device lifecycle events."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class DeviceAuditLog:
    """Lazily persist one canonical JSON object per device event."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, object]) -> None:
        encoded = (
            json.dumps(
                event,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        with self._lock:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(self.path, 0o600)


__all__ = ["DeviceAuditLog"]
