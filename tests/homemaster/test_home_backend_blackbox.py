from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from homemaster.domain.home_backend import HomeWorldBackend
from homemaster.domain.tools import make_robot_go_to, make_robot_manipulate
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionStatus,
)

WORLD_FIXTURE = (
    Path(__file__).resolve().parents[2] / "data" / "homes" / "elder_home_v1" / "world.json"
)


def _context(tmp_path: Path, backend: HomeWorldBackend) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="session-home-blackbox",
        run_id="run-home-blackbox",
        turn_index=0,
        tool_call_id="call-home-blackbox",
        internal_tool_id="homemaster.robot_go_to.v1",
        permission_subject=PermissionSubject(subject_id="tester", channel="cli"),
        backend=backend,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=tmp_path,
    )


@pytest.mark.asyncio
async def test_home_tools_change_external_world_and_return_receipts(tmp_path: Path) -> None:
    world_path = tmp_path / "world.json"
    shutil.copyfile(WORLD_FIXTURE, world_path)
    backend = HomeWorldBackend(world_path)
    go_to = make_robot_go_to()
    manipulate = make_robot_manipulate()

    navigation = await go_to.executor.execute({"room_hint": "kitchen"}, _context(tmp_path, backend))
    assert navigation.status is ToolExecutionStatus.SUCCESS
    assert navigation.external_return_code == 0
    assert navigation.backend_attempted is True
    assert navigation.evidence_refs

    taken = await manipulate.executor.execute(
        {"action": "take", "target_object": "obj_cup_1"}, _context(tmp_path, backend)
    )
    assert taken.status is ToolExecutionStatus.SUCCESS
    assert taken.external_return_code == 0

    placed = await manipulate.executor.execute(
        {
            "action": "put",
            "target_object": "obj_cup_1",
            "target_receptacle": "anchor_kitchen_counter_1",
        },
        _context(tmp_path, backend),
    )
    assert placed.status is ToolExecutionStatus.SUCCESS
    assert placed.external_return_code == 0

    state = json.loads(world_path.read_text(encoding="utf-8"))["runtime"]
    assert state["robot_room"] == "kitchen"
    assert state["held_object_id"] is None
    assert state["object_locations"]["obj_cup_1"] == "anchor_kitchen_counter_1"
    assert state["revision"] == 3
    assert placed.evidence_refs[0].startswith("home-world/")


@pytest.mark.asyncio
async def test_home_backend_rejects_unknown_target_with_nonzero_return_code(tmp_path: Path) -> None:
    world_path = tmp_path / "world.json"
    shutil.copyfile(WORLD_FIXTURE, world_path)
    backend = HomeWorldBackend(world_path)

    result = await make_robot_go_to().executor.execute(
        {"room_hint": "does-not-exist"}, _context(tmp_path, backend)
    )

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.external_return_code == 2
    assert result.error is not None
    assert result.error.code == "target_not_found"
    world = json.loads(world_path.read_text(encoding="utf-8"))
    assert "runtime" not in world
