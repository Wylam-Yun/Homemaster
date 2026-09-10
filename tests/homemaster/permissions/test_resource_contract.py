"""Contract tests for the V3.4 generic permission DTOs and action directory.

No simulator, no store, no HTTP: exact identity, directory membership and
request closure only.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from homemaster.permissions.models import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalResolution,
    ApprovalSubmission,
    ItemDecision,
    PermissionStorageUnavailable,
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
    TargetChanged,
    TargetUnresolved,
)
from homemaster.permissions.resources import resolve_action


def _utc(hour: int) -> str:
    return f"2026-09-10T0{hour}:00:00Z"


def _cup_requirement(item_id: str = "item-pick") -> Requirement:
    return Requirement(
        item_id=item_id,
        key=ResourceKey(
            environment_id="home",
            resource_kind="object",
            resource_id="cup-a",
            action="pick_up",
        ),
        display_name="白色杯子",
        location="卧室床头柜",
        action_label="拿取",
        step_ids=("step-1",),
    )


def _cup_request() -> PreparedPhysicalRequest:
    return PreparedPhysicalRequest(
        request_id="request-001",
        approval_id="approval-001",
        environment_id="home",
        session_id="session-001",
        run_id="run-001",
        intent_id="intent-001",
        intent_summary="拿杯子",
        revision=1,
        requirements=(_cup_requirement(),),
        steps=(
            PreparedStep(
                step_id="step-1",
                binding_ref="binding-001",
                required_item_ids=("item-pick",),
                summary="拿取床头柜上的白色杯子",
            ),
        ),
        target_snapshot_revision="snap-001",
        created_at=_utc(1),
        deadline_at=_utc(2),
    )


def test_exact_resource_identity():
    from homemaster.permissions.models import ResourceKey
    a = ResourceKey(environment_id="home", resource_kind="object",
                    resource_id="cup-a", action="pick_up")
    b = a.model_copy(update={"resource_id": "cup-b"})
    c = a.model_copy(update={"action": "clean"})
    assert a != b
    assert a != c
    assert a != a.model_copy(update={"environment_id": "other-home"})


def test_resource_key_rejects_unknown_action() -> None:
    for bad in ("vaporize", "*", "pick up", ""):
        with pytest.raises(ValidationError):
            ResourceKey(
                environment_id="home",
                resource_kind="object",
                resource_id="cup-a",
                action=bad,
            )


def test_resource_key_rejects_broad_calls() -> None:
    for broad in ("use", "toggle", "manipulate"):
        with pytest.raises(ValidationError):
            ResourceKey(
                environment_id="home",
                resource_kind="object",
                resource_id="cup-a",
                action=broad,
            )


def test_action_aliases_resolve_deterministically() -> None:
    assert resolve_action("take") == "pick_up"
    assert resolve_action("PUT") == "place"
    assert ResourceKey(
        environment_id="home",
        resource_kind="object",
        resource_id="cup-a",
        action="take",
    ) == ResourceKey(
        environment_id="home",
        resource_kind="object",
        resource_id="cup-a",
        action="pick_up",
    )


def test_request_rejects_mixed_environments() -> None:
    foreign = Requirement(
        item_id="item-enter",
        key=ResourceKey(
            environment_id="other-home",
            resource_kind="area",
            resource_id="bedroom",
            action="enter",
        ),
        display_name="卧室",
        location="卧室",
        action_label="进入",
        step_ids=("step-1",),
    )
    valid = _cup_request()
    with pytest.raises(ValidationError):
        PreparedPhysicalRequest(
            **{
                **valid.model_dump(mode="python"),
                "requirements": (
                    valid.requirements[0].model_dump(mode="python"),
                    foreign.model_dump(mode="python"),
                ),
                "steps": (
                    {
                        "step_id": "step-1",
                        "binding_ref": "binding-001",
                        "required_item_ids": ("item-pick", "item-enter"),
                        "summary": "进入并拿取",
                    },
                ),
            }
        )


def test_area_allows_only_enter() -> None:
    with pytest.raises(ValidationError):
        Requirement(
            item_id="item-x",
            key=ResourceKey(
                environment_id="home",
                resource_kind="area",
                resource_id="bedroom",
                action="pick_up",
            ),
            display_name="卧室",
            location="卧室",
            action_label="拿取",
            step_ids=("step-1",),
        )


def test_step_item_closure() -> None:
    valid = _cup_request()
    payload = valid.model_dump(mode="python")
    with pytest.raises(ValidationError):
        PreparedPhysicalRequest(
            **{
                **payload,
                "steps": (
                    {
                        "step_id": "step-1",
                        "binding_ref": "binding-001",
                        "required_item_ids": ("item-ghost",),
                        "summary": "引用不存在的项",
                    },
                ),
            }
        )
    orphan = Requirement(
        **{**_cup_requirement().model_dump(mode="python"), "item_id": "item-orphan"}
    )
    with pytest.raises(ValidationError):
        PreparedPhysicalRequest(
            **{
                **payload,
                "requirements": (
                    valid.requirements[0].model_dump(mode="python"),
                    orphan.model_dump(mode="python"),
                ),
            }
        )


def test_dtos_are_frozen_and_forbid_extra() -> None:
    key = ResourceKey(
        environment_id="home",
        resource_kind="object",
        resource_id="cup-a",
        action="pick_up",
    )
    with pytest.raises(ValidationError):
        ResourceKey(
            environment_id="home",
            resource_kind="object",
            resource_id="cup-a",
            action="pick_up",
            scene_id="scene-1",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        key.action = "clean"  # type: ignore[misc]


def test_named_exceptions_exist() -> None:
    for exc in (
        TargetUnresolved,
        TargetChanged,
        ApprovalConflict,
        ApprovalExpired,
        PermissionStorageUnavailable,
    ):
        assert issubclass(exc, Exception)
    assert len(
        {
            TargetUnresolved,
            TargetChanged,
            ApprovalConflict,
            ApprovalExpired,
            PermissionStorageUnavailable,
        }
    ) == 5


def test_request_time_must_be_utc_and_ordered() -> None:
    valid = _cup_request()
    payload = valid.model_dump(mode="python")
    with pytest.raises(ValidationError):
        PreparedPhysicalRequest(**{**payload, "created_at": "2026-09-10 01:00:00"})
    with pytest.raises(ValidationError):
        PreparedPhysicalRequest(
            **{**payload, "created_at": _utc(2), "deadline_at": _utc(1)}
        )


def test_submission_rules() -> None:
    submission = ApprovalSubmission(
        submission_id="submission-001",
        request_revision=1,
        decisions=(ItemDecision(item_id="item-pick", choice="allow_once"),),
    )
    assert submission.protocol_version == 2
    with pytest.raises(ValidationError):
        ApprovalSubmission(
            protocol_version=1,  # type: ignore[call-arg]
            submission_id="submission-002",
            request_revision=1,
            decisions=(ItemDecision(item_id="item-pick", choice="reject"),),
        )
    with pytest.raises(ValidationError):
        ApprovalSubmission(
            submission_id="submission-003",
            request_revision=1,
            decisions=(
                ItemDecision(item_id="item-pick", choice="allow_once"),
                ItemDecision(item_id="item-pick", choice="reject"),
            ),
        )
    resolved = ApprovalResolution(
        approval_id="approval-001",
        request_id="request-001",
        request_status="ready",
        execution_started=False,
        persisted_grant_ids=(),
        items=(ItemDecision(item_id="item-pick", choice="allow_once"),),
    )
    assert resolved.request_status == "ready"
    with pytest.raises(ValidationError):
        ApprovalResolution(
            approval_id="approval-001",
            request_id="request-001",
            request_status="approved",  # type: ignore[arg-type]
            execution_started=False,
            persisted_grant_ids=(),
            items=(ItemDecision(item_id="item-pick", choice="allow_once"),),
        )
