"""Minimal external THOR backend contract used by the HomeMaster Harness."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class BackendReceipt:
    operation: str
    external_return_code: int
    backend_attempted: bool
    state: dict[str, Any] = field(default_factory=dict)
    evidence_ref: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.backend_attempted and self.external_return_code == 0 and self.error is None


@dataclass(frozen=True)
class ThorObservation:
    state: dict[str, Any]
    scene_generation: int
    state_sequence: int
    raw_event_ref: str | None = None


class ThorBackend(Protocol):
    """Only external environment calls belong behind this interface."""

    def reset(self, trial: Any) -> BackendReceipt: ...

    def set_task(self, task: Any) -> BackendReceipt: ...

    def observe(self) -> ThorObservation: ...

    def act(self, action: dict[str, Any]) -> BackendReceipt: ...

    def close(self) -> BackendReceipt: ...
