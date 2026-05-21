"""RunContext — execution context passed through the generic agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homemaster.config.runtime_settings import RuntimeSettings


@dataclass(slots=True)
class RunContext:
    session_id: str
    run_id: str
    turn_index: int
    settings: RuntimeSettings
    event_sink: Any
    deps: dict[str, Any] = field(default_factory=dict)
    cancellation_token: Any | None = None
