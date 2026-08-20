"""Typed JSON envelopes exposed to the browser console."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

from homemaster.web.confirmations import ApprovalOutcome


class CreateSessionRequest(BaseModel):
    """Optional explicit persisted session to resume."""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None


class SendMessageRequest(BaseModel):
    """One immutable browser command submitted for asynchronous execution."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ApprovalDecisionRequest(BaseModel):
    """One typed resolution for a pending server-owned approval."""

    model_config = ConfigDict(extra="forbid")

    outcome: ApprovalOutcome


@dataclass(frozen=True)
class WebEvent:
    """One browser-facing event after Runtime field projection."""

    type: str
    session_id: str
    run_id: str
    request_id: str
    payload: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible copy for a WebSocket frame."""

        return {
            "type": self.type,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "payload": _copy_json(self.payload),
        }


def _copy_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json(item) for item in value]
    return value


__all__ = [
    "ApprovalDecisionRequest",
    "CreateSessionRequest",
    "SendMessageRequest",
    "WebEvent",
]
