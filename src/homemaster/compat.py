"""Small runtime compatibility helpers."""

from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - exercised on Python 3.10
    from enum import Enum

    class StrEnum(str, Enum):  # noqa: UP042
        """Python 3.10 fallback for enum.StrEnum."""
