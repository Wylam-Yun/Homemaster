"""Physical policy tests: exact grants gate items, modes do not bypass."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeClock, decide, make_combo_request, make_request

from homemaster.permissions import (
    PermissionChecker,
    PermissionMode,
    PermissionSettingsConfig,
    PhysicalDecision,
)
from homemaster.permissions.store import PermissionStore
from homemaster.tools import ToolExecutionContext
from homemaster.tools.executor import AllowAllPermissionChecker


def _pctx(
    tmp_path: Path, run_id: str = "run-1", session_id: str = "session-1"
) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path, metadata={"session_id": session_id, "run_id": run_id}
    )


def _checker(
    store: PermissionStore, clock: FakeClock, **settings: object
) -> PermissionChecker:
    return PermissionChecker(
        PermissionSettingsConfig(**settings),  # type: ignore[arg-type]
        store=store,
        clock=clock,
    )


@pytest.mark.parametrize("mode", [PermissionMode.FULL_AUTO, PermissionMode.DEFAULT])
@pytest.mark.parametrize("capabilities", [(), ("tool.auto",)])
@pytest.mark.parametrize("allowed", [(), ("robot_navigate",)])
def test_physical_missing_ignores_tool_modes(
    tmp_path: Path,
    store: PermissionStore,
    clock: FakeClock,
    mode: PermissionMode,
    capabilities: tuple[str, ...],
    allowed: tuple[str, ...],
) -> None:
    del capabilities
    request = make_request(clock, f"mode-{mode.value}-{len(allowed)}")
    store.create_request(request)
    checker = _checker(store, clock, mode=mode, allowed_tools=allowed)
    verdict = checker.evaluate_physical(
        request=request, context=_pctx(tmp_path, run_id=f"run-{mode.value}")
    )
    assert verdict.allowed is False
    assert verdict.missing_item_ids == tuple(
        item.item_id for item in request.requirements
    )


def test_default_mode_no_longer_pops_generic_confirmation(tmp_path: Path) -> None:
    checker = PermissionChecker(PermissionSettingsConfig(mode=PermissionMode.DEFAULT))
    context = ToolExecutionContext(tmp_path, metadata={"session_id": "session"})
    decision = checker.evaluate_tool(
        tool_name="write_file",
        is_read_only=False,
        required_capabilities=(),
        arguments={},
        context=context,
    )
    assert decision.allowed is True
    assert decision.requires_confirmation is False


def test_grant_covers_same_key_after_rename_and_move(
    tmp_path: Path, store: PermissionStore, clock: FakeClock
) -> None:
    first = make_request(clock, "rename-src")
    store.create_request(first)
    decide(
        store,
        first,
        {item.item_id: "allow_always" for item in first.requirements},
        "sub-rename",
    )
    moved = make_request(clock, "rename-dst")
    relocated = moved.requirements[0].model_copy(
        update={"display_name": "红色杯子", "location": "客厅茶几"}
    )
    moved = moved.model_copy(update={"requirements": (relocated,)})
    moved_item = moved.requirements[0]
    assert moved_item.key == first.requirements[0].key
    assert (moved_item.display_name, moved_item.location) != (
        first.requirements[0].display_name,
        first.requirements[0].location,
    )
    store.create_request(moved)
    verdict = _checker(store, clock).evaluate_physical(
        request=moved, context=_pctx(tmp_path)
    )
    assert verdict == PhysicalDecision(True, (), "household permission holds")


def test_other_cup_other_action_other_env_still_ask(
    tmp_path: Path, store: PermissionStore, clock: FakeClock
) -> None:
    granted = make_request(clock, "grant-src")
    store.create_request(granted)
    decide(
        store,
        granted,
        {item.item_id: "allow_always" for item in granted.requirements},
        "sub-grant-src",
    )
    from conftest import make_item, make_step

    base_key = granted.requirements[0].key
    variants = [
        base_key.model_copy(update={"resource_id": "cup-b"}),
        base_key.model_copy(update={"action": "clean"}),
        base_key.model_copy(update={"environment_id": "other-home"}),
    ]
    for index, key in enumerate(variants):
        request = make_request(clock, f"variant-{index}")
        item = make_item(
            f"item-var-{index}",
            key.resource_kind,
            key.resource_id,
            key.action,
            "杯子",
            "某处",
            "动作",
            f"step-var-{index}",
            key.environment_id,
        )
        request = request.model_copy(
            update={
                "request_id": f"request-variant-{index}",
                "approval_id": f"approval-variant-{index}",
                "environment_id": key.environment_id,
                "requirements": (item,),
                "steps": (make_step(item.step_ids[0], item.item_id, "变体"),),
            }
        )
        store.create_request(request)
        verdict = _checker(store, clock).evaluate_physical(
            request=request, context=_pctx(tmp_path, run_id=f"run-var-{index}")
        )
        assert verdict.allowed is False
        assert verdict.missing_item_ids == (item.item_id,)


def test_area_missing_survives_item_grant(
    tmp_path: Path, store: PermissionStore, clock: FakeClock
) -> None:
    combo = make_combo_request(clock, "areasrc")
    store.create_request(combo)
    enter, pick = (item.item_id for item in combo.requirements)
    decide(store, combo, {enter: "allow_always", pick: "reject"}, "sub-areasrc")
    retry = make_combo_request(clock, "areadst")
    store.create_request(retry)
    enter2, pick2 = (item.item_id for item in retry.requirements)
    verdict = _checker(store, clock).evaluate_physical(
        request=retry, context=_pctx(tmp_path)
    )
    assert verdict.allowed is False
    assert verdict.missing_item_ids == (pick2,)
    assert enter2 not in verdict.missing_item_ids


def test_once_valid_only_before_terminal(
    tmp_path: Path, store: PermissionStore, clock: FakeClock
) -> None:
    from homemaster.permissions.models import ExecutionObservation

    request = make_request(clock, "oncelife")
    store.create_request(request)
    decide(
        store,
        request,
        {item.item_id: "allow_once" for item in request.requirements},
        "sub-oncelife",
    )
    checker = _checker(store, clock)
    assert checker.evaluate_physical(
        request=request, context=_pctx(tmp_path)
    ).allowed is True
    step = request.steps[0]
    store.claim_step(request.request_id, step.step_id, request.revision)
    store.finish_step(
        request.request_id,
        step.step_id,
        ExecutionObservation(
            outcome="succeeded",
            backend_code="ok",
            binding_ref=step.binding_ref,
            observed_resource_id="cup-a",
            current_area_id="bedroom",
            evidence_ref="ev-1",
        ),
    )
    again = make_request(clock, "oncelife2")
    store.create_request(again)
    verdict = checker.evaluate_physical(request=again, context=_pctx(tmp_path))
    assert verdict.allowed is False
    assert verdict.missing_item_ids == tuple(
        item.item_id for item in again.requirements
    )


@pytest.mark.parametrize(
    "setup",
    ["cancelled", "expired", "stale_revision"],
)
def test_dead_requests_deny(
    tmp_path: Path, store: PermissionStore, clock: FakeClock, setup: str
) -> None:
    request = make_request(clock, f"dead-{setup}")
    store.create_request(request)
    if setup == "cancelled":
        store.cancel(request.request_id, "test")
    elif setup == "expired":
        clock.advance(600)
    elif setup == "stale_revision":
        request = request.model_copy(update={"revision": request.revision + 1})
    verdict = _checker(store, clock).evaluate_physical(
        request=request, context=_pctx(tmp_path)
    )
    assert verdict.allowed is False
    assert verdict.missing_item_ids == ()


def test_rejected_scope_suppressed_within_run(
    tmp_path: Path, store: PermissionStore, clock: FakeClock
) -> None:
    request = make_request(clock, "suppress")
    store.create_request(request)
    checker = _checker(store, clock)
    first_run = _pctx(tmp_path, run_id="run-1")
    checker.note_rejected(request=request, context=first_run)
    denied = checker.evaluate_physical(request=request, context=first_run)
    assert denied.allowed is False
    assert denied.missing_item_ids == ()
    assert "already rejected" in denied.reason
    second_run = _pctx(tmp_path, run_id="run-2")
    asked = checker.evaluate_physical(request=request, context=second_run)
    assert asked.allowed is False
    assert asked.missing_item_ids == tuple(
        item.item_id for item in request.requirements
    )


def test_evaluate_without_store_raises(tmp_path: Path, clock: FakeClock) -> None:
    checker = PermissionChecker(PermissionSettingsConfig(), clock=clock)
    with pytest.raises(RuntimeError):
        checker.evaluate_physical(
            request=make_request(clock, "nostore"), context=_pctx(tmp_path)
        )


def test_allow_all_physical_raises(tmp_path: Path, clock: FakeClock) -> None:
    with pytest.raises(RuntimeError):
        AllowAllPermissionChecker().evaluate_physical(
            request=make_request(clock, "allowall"), context=_pctx(tmp_path)
        )
