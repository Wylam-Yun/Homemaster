"""HTTP schemas for the ``/v1/skills/*`` endpoints (design §5.2–§5.4).

Kept separate from ``api.schemas`` so the memory and skill surfaces evolve
independently while sharing the same :class:`ApiResponse` envelope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel

from ..typing import (
    SkillEvolveResult,
    SkillSummary,
    SkillSyncRequestItem,
    SkillSyncResult,
    SkillTraceOutcome,
    SkillTraceStatus,
    SkillTraceStep,
    SkillVersion,
    SkillVersionStatus,
)
from .schemas import ActorIdentityRequest, NonEmptyStr


class SkillRegisterRequest(BaseModel):
    """HTTP body for ``POST /v1/skills/register`` (design §5.2).

    ``content`` is the canonical bundle text (see ``components/skill``); a bare
    ``SKILL.md`` body is also accepted and treated as the single whitelisted
    file. ``parent_version_id`` branches off an existing version (lineage);
    omitting it registers a root version.
    """

    model_config = ConfigDict(extra="forbid")

    name: NonEmptyStr
    content: NonEmptyStr
    version_label: NonEmptyStr | None = None
    parent_version_id: NonEmptyStr | None = None


class SkillRegisterData(BaseModel):
    """``data`` payload returned by ``POST /v1/skills/register`` (design §5.2)."""

    cloud_skill_id: str
    version_id: str
    version_label: str | None = None
    content_hash: str
    status: SkillVersionStatus


class SkillListData(BaseModel):
    """``data`` payload returned by ``GET /v1/skills`` (design §5.4)."""

    skills: list[SkillSummary]


class SkillVersionsData(BaseModel):
    """``data`` payload returned by ``GET .../versions`` (design §5.4)."""

    versions: list[SkillVersion]


class SkillContentData(BaseModel):
    """``data`` payload returned by ``GET .../content`` (design §5.4)."""

    version: SkillVersion
    content: str


class SkillSyncRequest(RootModel[list[SkillSyncRequestItem]]):
    """HTTP body for ``POST /v1/skills/sync`` (design §5.3).

    The wire contract is a top-level JSON array, matching the SDK-facing design:
    ``[{ "cloud_skill_id": "...", "local_version_id": "..." }]``.
    """

    root: list[SkillSyncRequestItem] = Field(min_length=1)


class SkillSyncData(BaseModel):
    """``data`` payload returned by ``POST /v1/skills/sync`` (design §5.3)."""

    results: list[SkillSyncResult]


class SkillEvolveRequest(ActorIdentityRequest):
    """HTTP body for ``POST /v1/skills/evolve``.

    ``cloud_skill_id`` selects the injected add traces that drive the
    self-evolution pipeline. ``mode`` mirrors memory add: sync runs inline,
    async queues Kafka work. ``project_id`` is resolved from the bearer
    ``api_key`` (same as the memory endpoints), never taken from the body.
    """

    model_config = ConfigDict(extra="forbid")

    cloud_skill_id: NonEmptyStr
    mode: Literal["sync", "async"] = "sync"


# The evolve endpoint returns the full result DTO as its ``data`` payload.
SkillEvolveData = SkillEvolveResult


class SkillTraceSubmitRequest(BaseModel):
    """HTTP body for ``POST /v1/skills/{cloud_skill_id}/traces`` (V3.8).

    Seals one skill invocation trace. Idempotent on ``invocation_id``: an
    identical resubmission replays the stored response; a resubmission with
    different content is rejected with 409.
    """

    model_config = ConfigDict(extra="forbid")

    invocation_id: NonEmptyStr
    skill_version_id: NonEmptyStr
    skill_content_hash: NonEmptyStr
    task_input: str = ""
    tool_steps: list[SkillTraceStep] = Field(default_factory=list)
    agent_completion: str = ""
    outcome: SkillTraceOutcome
    score: float = Field(ge=0.0, le=1.0)


class SkillTraceSubmitData(BaseModel):
    """``data`` payload returned when sealing a trace (V3.8)."""

    invocation_id: str
    cloud_skill_id: str
    status: Literal["sealed"] = "sealed"
    unconsumed_count: int


class SkillTraceData(BaseModel):
    """``data`` payload returned by ``GET .../traces/{invocation_id}`` (V3.8)."""

    invocation_id: str
    cloud_skill_id: str
    skill_version_id: str
    skill_content_hash: str
    task_input: str = ""
    tool_steps: list[SkillTraceStep] = Field(default_factory=list)
    agent_completion: str = ""
    outcome: SkillTraceOutcome
    score: float
    status: SkillTraceStatus
    consumed_by_batch_id: str | None = None
    created_at: datetime


class EvolutionBatchRequest(BaseModel):
    """HTTP body for ``POST /v1/skills/{cloud_skill_id}/evolution-batches`` (V3.8).

    Claims ``batch_id`` over exactly ``trace_ids`` (up to eight sealed
    invocations) and mints one draft ``SKILL.md`` version — never activated.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: NonEmptyStr
    trace_ids: list[NonEmptyStr] = Field(min_length=1, max_length=8)


class EvolutionBatchData(BaseModel):
    """``data`` payload returned by the evolution-batch endpoint (V3.8)."""

    batch_id: str
    draft_version_id: str | None = None
    status: Literal["completed", "no_action"]
    reason: str | None = None
