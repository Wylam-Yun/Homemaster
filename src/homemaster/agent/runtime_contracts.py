"""Dependency-free contracts shared by agent and application layers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homemaster.agent.interrupt import InterruptController
    from homemaster.agent.messages import ToolResultMessage
    from homemaster.agent.session import AgentSession
    from homemaster.events.runtime_events import RuntimeEvent


@dataclass(frozen=True)
class RuntimeStopDecision:
    """Typed domain decision returned by a run policy stop condition."""

    status: str
    final_reply: str = ""
    error_code: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class GenericRunResult:
    """Result of an engine run (legacy provider loop and AgentScope path)."""

    run_id: str
    status: str
    session: AgentSession
    events: list[RuntimeEvent]
    final_reply: str = ""
    error_code: str | None = None
    # AgentScope engine state for cross-run persistence (schema-v2 snapshot
    # authority); always ``None`` on the legacy provider-loop runtime.
    engine_state: Any = None


StopCondition = Callable[
    ["AgentSession", list["ToolResultMessage"]],
    "RuntimeStopDecision | None | Awaitable[RuntimeStopDecision | None]",
]


def _cancelled(interrupt: InterruptController, cancellation_token: Any) -> bool:
    return interrupt.cancelled or bool(getattr(cancellation_token, "cancelled", False))


__all__ = [
    "GenericRunResult",
    "RuntimeStopDecision",
    "StopCondition",
    "_cancelled",
]
