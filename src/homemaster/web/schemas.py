"""Typed JSON envelopes exposed to the browser console."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateSessionRequest(BaseModel):
    """Optional explicit persisted session to resume."""

    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None


class SendMessageAttachment(BaseModel):
    """Reference to one session-scoped attachment uploaded prior to send."""

    model_config = ConfigDict(extra="forbid")

    attachment_id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", max_length=128)


class UploadAttachmentRequest(BaseModel):
    """JSON base64 attachment upload for a session."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", max_length=128)
    data_base64: str = Field(min_length=1, max_length=15_000_000)


class SendMessageRequest(BaseModel):
    """One immutable browser command submitted for asynchronous execution."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1)
    text: str = Field(default="")
    provider_name: str | None = None
    model: str | None = None
    attachments: list[SendMessageAttachment] = Field(default_factory=list, max_length=20)


class CreateMemoryRequest(BaseModel):
    """Create a user-authored long-term memory in MindMemOS."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)
    memory_type: Literal["fact", "procedure"] = "fact"


class UpdateMemoryRequest(BaseModel):
    """Update the text content of an active memory.

    ``fields`` performs a native structured update (new version + lineage) on
    memories that carry a structured record; without it a content-only patch
    on a structured memory is rejected, exactly like the pre-V3.8 surface.
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)
    fields: dict[str, Any] | None = None


class SetSessionModeRequest(BaseModel):
    """Switch one session between plan (read-only) and act execution modes."""

    model_config = ConfigDict(extra="forbid")

    ui_mode: Literal["plan", "act"]


class RenameSessionRequest(BaseModel):
    """Replace the operator-visible list title of one session."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)


class AnswerQuestionRequest(BaseModel):
    """Resolve one pending ask_user question with the operator's answer."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class SkillResolveRequest(BaseModel):
    """Resolve a slash command against the server-side skill registry."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)


class SkillImportFile(BaseModel):
    """One file of an uploaded skill folder (path relative, POSIX-style)."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=1_000_000)


class SkillImportRequest(BaseModel):
    """JSON folder upload: every file sits under one top-level directory."""

    model_config = ConfigDict(extra="forbid")

    files: list[SkillImportFile] = Field(min_length=1, max_length=200)
    overwrite: bool = False


class ItemChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    choice: Literal["allow_once", "allow_always", "allow_session", "reject"]


class ApprovalSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[2]
    submission_id: str = Field(min_length=1)
    request_revision: int = Field(ge=1)
    decisions: list[ItemChoiceRequest] = Field(min_length=1)


class RevokeGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)


class CancelApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(min_length=1)
    request_revision: int = Field(ge=1)


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
    domain: str | None = None
    outcome: str | None = None
    classification: str | None = None
    goal_type: str | None = None
    episode_id: str | None = None
    taskset_id: str | None = None
    subtask_index: int | None = None
    is_executable: bool | None = None
    source_trajectory_id: str | None = None
    derived_memory_id: str | None = None
    compile_status: str | None = None


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


class MemorySearchHitResponse(BaseModel):
    """One MindMemOS search hit projected for the browser."""

    model_config = ConfigDict(from_attributes=True)

    memory_id: str
    content: str
    memory_type: str
    memory_type_label: str
    score: float | None
    session_id: str | None


class MemorySearchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    query: str
    hits: tuple[MemorySearchHitResponse, ...]
    search_record_id: str | None

    @classmethod
    def from_domain(cls, result: Any) -> MemorySearchResponse:
        return cls.model_validate(result)


class MemoryGraphNodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    memory_id: str
    preview: str
    memory_type: str
    status: str
    session_id: str | None
    updated_at: datetime | None


class MemoryGraphEdgeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    src: str
    rel: str
    dst: str
    dst_label: str
    entity_name: str | None


class MemoryGraphEntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: str
    name: str


class MemoryGraphSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    title: str


class MemoryGraphResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    nodes: tuple[MemoryGraphNodeResponse, ...]
    entities: tuple[MemoryGraphEntityResponse, ...]
    edges: tuple[MemoryGraphEdgeResponse, ...]
    sessions: tuple[MemoryGraphSessionResponse, ...]
    edges_truncated: bool = False

    @classmethod
    def from_domain(cls, graph: Any) -> MemoryGraphResponse:
        return cls.model_validate(graph)


@dataclass(frozen=True)
class WebEvent:
    """One browser-facing event after Runtime field projection.

    ``seq``/``ts`` are stamped by ``WebEventHub.publish`` under its fanout
    lock — the single sequencing point every wire event crosses, including
    direct publishes that bypass the Runtime event pump (``request.accepted``,
    ``question.*``, ``session.mode_changed``, fallback terminals). ``seq`` is
    a per-session monotonically increasing counter; ``ts`` is epoch seconds.
    An event that never passed through the hub keeps the zero defaults.
    """

    type: str
    session_id: str
    run_id: str
    request_id: str
    payload: Mapping[str, object]
    seq: int = 0
    ts: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible copy for a WebSocket frame."""

        return {
            "type": self.type,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "seq": self.seq,
            "ts": self.ts,
            "payload": _copy_json(self.payload),
        }


def _copy_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _copy_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy_json(item) for item in value]
    return value


__all__ = [
    "AnswerQuestionRequest",
    "ApprovalSubmissionRequest",
    "CancelApprovalRequest",
    "CreateMemoryRequest",
    "CreateSessionRequest",
    "ItemChoiceRequest",
    "ManagedMemoryResponse",
    "MemoryGraphEdgeResponse",
    "MemoryGraphEntityResponse",
    "MemoryGraphNodeResponse",
    "MemoryGraphResponse",
    "MemoryGraphSessionResponse",
    "MemoryGroupResponse",
    "MemoryHistoryResponse",
    "MemorySearchHitResponse",
    "MemorySearchResponse",
    "MemorySnapshotResponse",
    "MemoryStatsResponse",
    "RenameSessionRequest",
    "RevokeGrantRequest",
    "SendMessageAttachment",
    "SendMessageRequest",
    "SetSessionModeRequest",
    "SkillResolveRequest",
    "UpdateMemoryRequest",
    "UploadAttachmentRequest",
    "WebEvent",
]
