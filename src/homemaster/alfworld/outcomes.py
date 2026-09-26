"""Typed Harness requests and terminal feedback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class AlfworldActionRequest:
    tool_name: Literal["robot_go_to", "robot_manipulate", "robot_verify"]
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AlfworldExecutionFeedback:
    action: str
    success: bool
    classification: str | None
    external_return_code: int
    backend_attempted: bool
    terminal: bool = False
    won: bool = False
    evidence_refs: tuple[str, ...] = ()
    state_before: dict[str, Any] = field(default_factory=dict)
    state_after: dict[str, Any] = field(default_factory=dict)


def failure_feedback(
    *, action: str, classification: str, receipt: Any, before: dict[str, Any], after: dict[str, Any]
) -> AlfworldExecutionFeedback:
    return AlfworldExecutionFeedback(
        action=action,
        success=False,
        classification=classification,
        external_return_code=int(getattr(receipt, "external_return_code", 1)),
        backend_attempted=bool(getattr(receipt, "backend_attempted", False)),
        evidence_refs=tuple(
            value for value in (getattr(receipt, "evidence_ref", None),) if isinstance(value, str)
        ),
        state_before=before,
        state_after=after,
    )
