"""Dependency-free contracts shared by agent and application layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeStopDecision:
    """Typed domain decision returned by a run policy stop condition."""

    status: str
    final_reply: str = ""
    error_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


__all__ = ["RuntimeStopDecision"]
