"""Structured CLI confirmation tests against a real store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from homemaster.cli.confirmation import CliConfirmationHandler, CliPermissionMode
from homemaster.permissions import PermissionMode
from homemaster.permissions.models import (
    ApprovalCancelled,
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
)
from homemaster.permissions.store import PermissionStore
from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    async def aemit(self, event) -> None:
        self.events.append(event)


class FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 9, 10, 1, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self._now


def _request(tag: str, *, combo: bool = False) -> PreparedPhysicalRequest:
    suffix = f"{tag}-{uuid4().hex[:6]}"

    def item(item_id: str, kind: str, rid: str, action: str) -> Requirement:
        return Requirement(
            item_id=item_id,
            key=ResourceKey(
                environment_id="home",
                resource_kind=kind,  # type: ignore[arg-type]
                resource_id=rid,
                action=action,
            ),
            display_name=rid,
            location="here",
            action_label=action,
            step_ids=(f"step-{item_id}-{suffix}",),
        )

    items = [item(f"item-pick-{suffix}", "object", "cup-a", "pick_up")]
    if combo:
        items.insert(
            0, item(f"item-enter-{suffix}", "area", "bedroom", "enter")
        )
    return PreparedPhysicalRequest(
        request_id=f"request-{suffix}",
        approval_id=f"approval-{suffix}",
        environment_id="home",
        session_id="session-cli",
        run_id="run-cli",
        intent_id=f"intent-{suffix}",
        intent_summary="cli test",
        revision=1,
        requirements=tuple(items),
        steps=tuple(
            PreparedStep(
                step_id=item.step_ids[0],
                binding_ref=f"binding-{item.item_id}",
                required_item_ids=(item.item_id,),
                summary=item.item_id,
            )
            for item in items
        ),
        target_snapshot_revision=f"snap-{suffix}",
        created_at="2026-09-10T01:00:00Z",
        deadline_at="2026-09-10T02:00:00Z",
    )


def _context(tmp_path: Path, sink: _EventSink) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "session_id": "session-1",
            "run_id": "run-1",
            "turn_index": 3,
            "tool_call_id": "call-1",
            "permission_subject": PermissionSubject("operator-1", "cli", capabilities=()),
            "run_context": SimpleNamespace(event_sink=sink),
        },
    )


def _handler(store: PermissionStore, answers: list) -> CliConfirmationHandler:
    script = list(answers)

    def scripted(prompt: str) -> str:
        if not script:
            raise EOFError("no more scripted answers")
        answer = script.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return CliConfirmationHandler(
        store=store, input_fn=scripted, output_fn=lambda value: None
    )


def test_cli_permission_modes_map_to_existing_policy_modes() -> None:
    assert CliPermissionMode.FULL_AUTO.policy_mode is PermissionMode.FULL_AUTO
    assert CliPermissionMode.CONFIRM.policy_mode is PermissionMode.DEFAULT
    assert CliPermissionMode.PLAN.policy_mode is PermissionMode.PLAN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answer", "choice", "grants"),
    [("1", "allow_once", 0), ("2", "allow_always", 1), ("3", "reject", 0)],
)
async def test_choice_mapping(tmp_path: Path, answer: str, choice: str, grants: int) -> None:
    clock = FakeClock()
    store = PermissionStore.open(tmp_path / "cli.sqlite3", clock=clock)
    try:
        request = _request("map")
        store.create_request(request)
        resolution = await _handler(store, [answer]).confirm(
            request,
            [item.item_id for item in request.requirements],
            _context(tmp_path, _EventSink()),
        )
        assert resolution.items[0].choice == choice
        assert len(resolution.persisted_grant_ids) == grants
    finally:
        store.close()


@pytest.mark.asyncio
async def test_invalid_answers_reprompt_current_item(tmp_path: Path) -> None:
    clock = FakeClock()
    store = PermissionStore.open(tmp_path / "cli.sqlite3", clock=clock)
    try:
        request = _request("reprompt")
        store.create_request(request)
        resolution = await _handler(store, ["9", "x", "2"]).confirm(
            request,
            [item.item_id for item in request.requirements],
            _context(tmp_path, _EventSink()),
        )
        assert resolution.items[0].choice == "allow_always"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_combo_decides_each_item_once(tmp_path: Path) -> None:
    clock = FakeClock()
    store = PermissionStore.open(tmp_path / "cli.sqlite3", clock=clock)
    try:
        request = _request("combo", combo=True)
        store.create_request(request)
        resolution = await _handler(store, ["2", "3"]).confirm(
            request,
            [item.item_id for item in request.requirements],
            _context(tmp_path, _EventSink()),
        )
        assert resolution.request_status == "blocked"
        assert [item.choice for item in resolution.items] == [
            "allow_always",
            "reject",
        ]
        assert len(resolution.persisted_grant_ids) == 1
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt(), OSError("gone")])
async def test_input_end_cancels(tmp_path: Path, error: BaseException) -> None:
    clock = FakeClock()
    store = PermissionStore.open(tmp_path / "cli.sqlite3", clock=clock)
    try:
        request = _request("eof")
        store.create_request(request)
        with pytest.raises(ApprovalCancelled):
            await _handler(store, [error]).confirm(
                request,
                [item.item_id for item in request.requirements],
                _context(tmp_path, _EventSink()),
            )
        assert store.get_request(request.approval_id).status == "cancelled"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_confirm_without_store_raises(tmp_path: Path) -> None:
    handler = CliConfirmationHandler(
        input_fn=lambda prompt: "1", output_fn=lambda value: None
    )
    with pytest.raises(RuntimeError):
        await handler.confirm(
            _request("nostore"), ["item-x"], _context(tmp_path, _EventSink())
        )
