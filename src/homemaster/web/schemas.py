"""Typed JSON envelopes exposed to the browser console."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

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


class ManagedMemoryResponse(BaseModel):
    """Browser-safe projection of one persistent memory."""

    model_config = ConfigDict(from_attributes=True)

    memory_id: str
    content: str
    memory_type: str
    memory_type_label: str
    status: str
    session_id: str | None
    created_at: datetime | None
    updated_at: datetime | None
    archived_at: datetime | None
    archive_reason: str | None
    record: dict[str, object] | None
    structure_status: Literal["plain", "valid", "invalid"]
    has_history: bool


class MemoryStatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    active_count: int
    archived_count: int
    total_count: int
    session_group_count: int


class MemoryGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str | None
    title: str
    active_count: int
    archived_count: int
    memories: tuple[ManagedMemoryResponse, ...]


class MemorySnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    stats: MemoryStatsResponse
    groups: tuple[MemoryGroupResponse, ...]

    @classmethod
    def from_domain(cls, snapshot: Any) -> MemorySnapshotResponse:
        return cls.model_validate(snapshot)


class MemoryHistoryResponse(BaseModel):
    memory_id: str
    versions: tuple[ManagedMemoryResponse, ...]

    @classmethod
    def from_domain(
        cls,
        memory_id: str,
        versions: object,
    ) -> MemoryHistoryResponse:
        return cls.model_validate({"memory_id": memory_id, "versions": versions})


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
    "ManagedMemoryResponse",
    "MemoryGroupResponse",
    "MemoryHistoryResponse",
    "MemorySnapshotResponse",
    "MemoryStatsResponse",
    "SendMessageRequest",
    "WebEvent",
]
