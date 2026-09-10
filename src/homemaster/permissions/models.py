"""Generic permission DTOs for exact household resources.

Simulator-independent on purpose: this module must never import
benchmarking/alfworld/THOR code and must not carry scene/episode/native
handles. Native bindings stay inside device adapters; the core only sees
opaque binding references plus exact permission keys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RequestStatus = Literal[
    "prepared",
    "awaiting_approval",
    "ready",
    "running",
    "succeeded",
    "failed",
    "outcome_unknown",
    "blocked",
    "expired",
    "cancelled",
    "interrupted",
]


class TargetUnresolved(Exception):
    """The target cannot be identified; ask for clarification, do not act."""


class TargetChanged(Exception):
    """The locked binding no longer matches; the old approval must not run."""


class ApprovalConflict(Exception):
    """Concurrent or divergent submission for the same request (HTTP 409)."""


class ApprovalExpired(Exception):
    """The request passed its deadline without a decision (HTTP 410)."""


class PermissionStorageUnavailable(Exception):
    """Durable permission state could not be read or committed (HTTP 503)."""


class FrozenDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _nonblank(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _checked_utc_instant(value: str, *, label: str) -> str:
    """Validate an ISO-8601 UTC instant and return its canonical form."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    text = value.strip()
    candidate = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must carry a timezone")
    if parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{label} must be in UTC")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _checked_instant_pair(created_at: str, deadline_at: str) -> tuple[str, str]:
    created = _checked_utc_instant(created_at, label="created_at")
    deadline = _checked_utc_instant(deadline_at, label="deadline_at")
    if deadline <= created:
        raise ValueError("deadline_at must be after created_at")
    return created, deadline


class ResourceKey(FrozenDTO):
    environment_id: str = Field(min_length=1)
    resource_kind: Literal["object", "area"] = Field()
    resource_id: str = Field(min_length=1)
    action: str = Field(min_length=1)

    @field_validator("environment_id", "resource_id")
    @classmethod
    def _ids_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="resource identity")

    @field_validator("action")
    @classmethod
    def _action_must_be_canonical(cls, value: str) -> str:
        from homemaster.permissions.resources import resolve_action

        try:
            return resolve_action(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc


class Requirement(FrozenDTO):
    item_id: str = Field(min_length=1)
    key: ResourceKey = Field()
    display_name: str = Field(min_length=1)
    location: str = Field(min_length=1)
    action_label: str = Field(min_length=1)
    step_ids: tuple[str, ...] = Field(min_length=1)
    image_ref: str | None = None

    @field_validator("item_id", "display_name", "location", "action_label")
    @classmethod
    def _text_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="requirement text")

    @field_validator("step_ids")
    @classmethod
    def _step_ids_must_be_unique_nonblank(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        for step_id in value:
            _nonblank(step_id, label="step id")
        if len(set(value)) != len(value):
            raise ValueError("step_ids must be unique")
        return value

    @model_validator(mode="after")
    def _area_allows_only_enter(self) -> Requirement:
        if self.key.resource_kind == "area" and self.key.action != "enter":
            raise ValueError("area resources only allow the enter action")
        return self


class PreparedStep(FrozenDTO):
    step_id: str = Field(min_length=1)
    binding_ref: str = Field(min_length=1)
    required_item_ids: tuple[str, ...] = Field(min_length=1)
    summary: str = Field(min_length=1)

    @field_validator("step_id", "binding_ref", "summary")
    @classmethod
    def _text_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="prepared step text")

    @field_validator("required_item_ids")
    @classmethod
    def _item_ids_must_be_unique_nonblank(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        for item_id in value:
            _nonblank(item_id, label="required item id")
        if len(set(value)) != len(value):
            raise ValueError("required_item_ids must be unique")
        return value


class PreparedPhysicalRequest(FrozenDTO):
    request_id: str = Field(min_length=1)
    approval_id: str = Field(min_length=1)
    environment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    intent_id: str = Field(min_length=1)
    intent_summary: str = Field(min_length=1)
    revision: int = Field(ge=1)
    # Empty requirements mean the adapter found nothing to approve (for
    # example navigating to the area the robot already occupies); the
    # executor then runs the steps directly without touching the store.
    requirements: tuple[Requirement, ...] = Field(default=())
    steps: tuple[PreparedStep, ...] = Field(default=())
    target_snapshot_revision: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    deadline_at: str = Field(min_length=1)

    @field_validator(
        "request_id",
        "approval_id",
        "environment_id",
        "session_id",
        "run_id",
        "intent_id",
        "intent_summary",
        "target_snapshot_revision",
    )
    @classmethod
    def _text_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="prepared request text")

    @model_validator(mode="after")
    def _validate_closure(self) -> PreparedPhysicalRequest:
        item_ids = [item.item_id for item in self.requirements]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("requirement item_ids must be unique")
        known = set(item_ids)
        for step in self.steps:
            unknown = set(step.required_item_ids) - known
            if unknown:
                raise ValueError(
                    f"step {step.step_id!r} references unknown items: "
                    f"{sorted(unknown)}"
                )
        step_ids = {step.step_id for step in self.steps}
        if len(step_ids) != len(self.steps):
            raise ValueError("step_ids must be unique")
        covered = {
            item_id for step in self.steps for item_id in step.required_item_ids
        }
        orphaned = known - covered
        if orphaned:
            raise ValueError(
                f"requirements without a step: {sorted(orphaned)}"
            )
        environments = {item.key.environment_id for item in self.requirements}
        if self.requirements and environments != {self.environment_id}:
            raise ValueError(
                "requirement environments must match the request environment"
            )
        created, deadline = _checked_instant_pair(self.created_at, self.deadline_at)
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "deadline_at", deadline)
        return self


class ItemDecision(FrozenDTO):
    item_id: str = Field(min_length=1)
    choice: Literal["allow_once", "allow_always", "reject"] = Field()

    @field_validator("item_id")
    @classmethod
    def _item_id_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="item id")


class ApprovalSubmission(FrozenDTO):
    protocol_version: Literal[2] = 2
    submission_id: str = Field(min_length=1)
    request_revision: int = Field(ge=1)
    decisions: tuple[ItemDecision, ...] = Field(min_length=1)

    @field_validator("submission_id")
    @classmethod
    def _submission_id_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="submission id")

    @field_validator("decisions")
    @classmethod
    def _decisions_must_cover_unique_items(
        cls, value: tuple[ItemDecision, ...]
    ) -> tuple[ItemDecision, ...]:
        item_ids = [decision.item_id for decision in value]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("decisions must not repeat an item_id")
        return value


class ApprovalResolution(FrozenDTO):
    approval_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    request_status: RequestStatus = Field()
    execution_started: bool = Field()
    persisted_grant_ids: tuple[str, ...] = Field()
    items: tuple[ItemDecision, ...] = Field(min_length=1)

    @field_validator("approval_id", "request_id")
    @classmethod
    def _text_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="approval resolution text")


class ExecutionObservation(FrozenDTO):
    outcome: Literal["succeeded", "no_effect_failure", "unknown"] = Field()
    backend_code: str = Field(min_length=1)
    binding_ref: str = Field(min_length=1)
    observed_resource_id: str | None = None
    current_area_id: str | None = None
    evidence_ref: str = Field(min_length=1)

    @field_validator("backend_code", "binding_ref", "evidence_ref")
    @classmethod
    def _text_must_be_nonblank(cls, value: str) -> str:
        return _nonblank(value, label="execution observation text")


__all__ = [
    "ApprovalConflict",
    "ApprovalExpired",
    "ApprovalResolution",
    "ApprovalSubmission",
    "ExecutionObservation",
    "FrozenDTO",
    "ItemDecision",
    "PermissionStorageUnavailable",
    "PreparedPhysicalRequest",
    "PreparedStep",
    "RequestStatus",
    "Requirement",
    "ResourceKey",
    "TargetChanged",
    "TargetUnresolved",
]
