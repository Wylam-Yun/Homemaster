"""Exact-resource fixtures for V3.4 permission store tests.

Fixed environment ``home``: two distinct cups and one bedroom. Every
request gets unique ids; time comes from an explicitly injected clock,
never from sleep.
"""

from __future__ import annotations

import itertools
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from homemaster.permissions.models import (
    ApprovalResolution,
    ApprovalSubmission,
    ItemDecision,
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
)
from homemaster.permissions.store import PermissionStore

_counter = itertools.count(1)


class FakeClock:
    def __init__(self, start: str = "2026-09-10T01:00:00Z") -> None:
        text = start.strip()
        candidate = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
        self._now = datetime.fromisoformat(candidate)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now


def _tag() -> str:
    return f"t{next(_counter):04d}{uuid4().hex[:4]}"


def _instant(clock: FakeClock, offset_s: float = 0) -> str:
    moment = clock() + timedelta(seconds=offset_s)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def make_item(
    item_id: str,
    kind: str,
    resource_id: str,
    action: str,
    display: str,
    location: str,
    label: str,
    step_id: str,
) -> Requirement:
    return Requirement(
        item_id=item_id,
        key=ResourceKey(
            environment_id="home",
            resource_kind=kind,  # type: ignore[arg-type]
            resource_id=resource_id,
            action=action,
        ),
        display_name=display,
        location=location,
        action_label=label,
        step_ids=(step_id,),
    )


def make_step(step_id: str, item_id: str, summary: str) -> PreparedStep:
    return PreparedStep(
        step_id=step_id,
        binding_ref=f"binding-{step_id}",
        required_item_ids=(item_id,),
        summary=summary,
    )


def make_request(
    clock: FakeClock,
    tag: str | None = None,
    *,
    items: tuple[Requirement, ...] | None = None,
    steps: tuple[PreparedStep, ...] | None = None,
) -> PreparedPhysicalRequest:
    suffix = tag or _tag()
    default_item = make_item(
        f"item-pick-{suffix}",
        "object",
        "cup-a",
        "pick_up",
        "白色杯子",
        "卧室床头柜",
        "拿取",
        f"step-1-{suffix}",
    )
    default_step = make_step(
        f"step-1-{suffix}", default_item.item_id, "拿取床头柜上的白色杯子"
    )
    return PreparedPhysicalRequest(
        request_id=f"request-{suffix}",
        approval_id=f"approval-{suffix}",
        environment_id="home",
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        intent_id=f"intent-{suffix}",
        intent_summary="拿杯子",
        revision=1,
        requirements=items or (default_item,),
        steps=steps or (default_step,),
        target_snapshot_revision=f"snap-{suffix}",
        created_at=_instant(clock),
        deadline_at=_instant(clock, 300),
    )


def make_combo_request(clock: FakeClock, tag: str | None = None) -> PreparedPhysicalRequest:
    """One enter-bedroom item plus one pick-cup item, each with its own step."""
    suffix = tag or _tag()
    enter = make_item(
        f"item-enter-{suffix}",
        "area",
        "bedroom",
        "enter",
        "卧室",
        "卧室",
        "进入",
        f"step-enter-{suffix}",
    )
    pick = make_item(
        f"item-pick-{suffix}",
        "object",
        "cup-b",
        "pick_up",
        "蓝色杯子",
        "卧室书桌",
        "拿取",
        f"step-pick-{suffix}",
    )
    return make_request(
        clock,
        suffix,
        items=(enter, pick),
        steps=(
            make_step(enter.step_ids[0], enter.item_id, "进入卧室"),
            make_step(pick.step_ids[0], pick.item_id, "拿取书桌上的蓝色杯子"),
        ),
    )


def decide(
    store: PermissionStore,
    request: PreparedPhysicalRequest,
    choices: dict[str, str],
    submission_id: str,
    actor: str = "tester",
) -> ApprovalResolution:
    return store.submit(
        request.approval_id,
        ApprovalSubmission(
            submission_id=submission_id,
            request_revision=request.revision,
            decisions=tuple(
                ItemDecision(item_id=item_id, choice=choice)  # type: ignore[arg-type]
                for item_id, choice in choices.items()
            ),
        ),
        actor,
    )


def read_raw(path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(str(path))
    try:
        return [tuple(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    return tmp_path / "permissions.sqlite3"


@pytest.fixture
def store(store_path: Path, clock: FakeClock) -> Iterator[PermissionStore]:
    handle = PermissionStore.open(store_path, clock=clock)
    try:
        yield handle
    finally:
        handle.close()
