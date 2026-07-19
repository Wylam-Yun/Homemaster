"""Router, runtime and result contracts for coworker turns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal


class TicketRouteKind(StrEnum):
    NO_MATCH = "no_match"
    VALID_TICKET = "valid_ticket"
    INVALID_TICKET_INTENT = "invalid_ticket_intent"


@dataclass(frozen=True)
class NoTicketRoute:
    kind: Literal[TicketRouteKind.NO_MATCH] = TicketRouteKind.NO_MATCH


@dataclass(frozen=True)
class ValidTicketRoute:
    ticket_path: Path
    case_root: Path
    scenario_id: Literal["normal", "post_change_anomaly"]
    locked_hashes: dict[str, str]
    kind: Literal[TicketRouteKind.VALID_TICKET] = TicketRouteKind.VALID_TICKET


@dataclass(frozen=True)
class InvalidTicketRoute:
    error_code: str
    message: str
    kind: Literal[TicketRouteKind.INVALID_TICKET_INTENT] = TicketRouteKind.INVALID_TICKET_INTENT


TicketRoute = NoTicketRoute | ValidTicketRoute | InvalidTicketRoute


@dataclass
class CoworkerOutcome:
    terminal: bool = False
    classification: str | None = None
    decision: str | None = None

    def mark(self, decision: str) -> None:
        self.decision = decision
        if decision in {"complete", "rolled_back", "block", "escalate", "insufficient_evidence"}:
            self.terminal = True
            self.classification = decision


@dataclass
class CoworkerTurnResult:
    run_id: str
    status: str
    final_reply: str
    trajectory_score: float
    result_score: float
    overall_score: float
    formal_success: bool
    artifact_path: Path
    video_path: Path | None = None
    trace_path: Path | None = None
    classification: str | None = None
    process_returns: dict[str, int | None] = field(default_factory=dict)


class CoworkerAttemptError(RuntimeError):
    """Safe failure carrying the already allocated run bundle path."""

    def __init__(self, *, run_id: str, run_root: Path, error_type: str) -> None:
        super().__init__(f"coworker attempt failed: {error_type}")
        self.run_id = run_id
        self.run_root = run_root
        self.error_type = error_type
