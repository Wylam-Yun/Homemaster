"""Tests for domain memory tool integration — memory_writer and retrieval fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.agent.normalized import RunContext
from homemaster.domain.tools import make_memory_writer
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext


def build_home_tool_registry():
    tool = make_memory_writer()
    return {tool.definition.model_alias: tool}


def _make_run_context(tmp_path: Path, **kwargs: Path | None) -> RunContext:
    settings = SimpleNamespace(
        run_id="test-run",
        runtime_root=tmp_path,
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
        memory_path=kwargs.get("memory_path"),
    )
    return RunContext(
        session_id="s1",
        run_id="test-run",
        turn_index=0,
        settings=settings,
        event_sink=None,
        deps={},
    )


def _tool_context(tmp_path: Path, run_context: RunContext) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id=run_context.session_id,
        run_id=run_context.run_id,
        turn_index=run_context.turn_index,
        tool_call_id="memory-writer-call",
        internal_tool_id="homemaster.memory_writer.v1",
        permission_subject=PermissionSubject("test", "pytest", tenant_id="local"),
        backend=None,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=tmp_path.resolve(),
        services={"run_context": run_context},
    )
@pytest.mark.asyncio
async def test_memory_writer_requires_proposal(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("memory_writer")
    run_context = _make_run_context(tmp_path)
    result = await spec.executor.execute({}, _tool_context(tmp_path, run_context))
    assert result.success is False
    assert result.error is not None
    assert "proposal" in result.error.message


@pytest.mark.asyncio
async def test_memory_writer_validates_required_fields(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("memory_writer")
    run_context = _make_run_context(tmp_path)
    result = await spec.executor.execute(
        {"proposal": {"object_category": "cup"}},
        _tool_context(tmp_path, run_context),
    )
    assert result.success is False
    assert result.error is not None
    assert "missing" in result.error.message


@pytest.mark.asyncio
async def test_memory_writer_accepts_valid_proposal(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("memory_writer")
    run_context = _make_run_context(tmp_path)
    result = await spec.executor.execute(
        {
            "proposal": {
                "object_category": "cup",
                "room_id": "kitchen",
                "anchor_id": "kitchen:cup:1",
                "belief_state": "verified",
            },
        },
        _tool_context(tmp_path, run_context),
    )
    assert result.success is True
    assert result.data["committed"] is True
    assert result.data["object_category"] == "cup"


@pytest.mark.asyncio
async def test_memory_writer_persists_runtime_overlay(tmp_path: Path) -> None:
    memory_path = tmp_path / "memory.json"
    memory_path.write_text(
        json.dumps({
            "objects": [
                {
                    "object_category": "cup",
                    "anchor": {"room_id": "kitchen", "anchor_id": "kitchen:cup:1"},
                    "belief_state": "present",
                }
            ]
        }),
        encoding="utf-8",
    )

    registry = build_home_tool_registry()
    spec = registry.get("memory_writer")
    run_context = _make_run_context(tmp_path, memory_path=memory_path)
    result = await spec.executor.execute(
        {
            "proposal": {
                "object_category": "cup",
                "room_id": "kitchen",
                "anchor_id": "kitchen:cup:1",
                "belief_state": "verified",
            },
        },
        _tool_context(tmp_path, run_context),
    )

    assert result.success is True
    overlay_path = tmp_path / "test-run" / "memory" / "object_memory.json"
    overlay = json.loads(overlay_path.read_text())
    assert overlay["objects"][0]["belief_state"] == "verified"
    assert "last_confirmed_at" in overlay["objects"][0]


def test_fetch_cup_retry_fixture_structure() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "home_tasks" / "fetch_cup_retry"
    case = json.loads((fixture_dir / "case.json").read_text())
    assert case["name"] == "fetch_cup_retry"
    assert "utterance" in case
    assert "expected" in case
    assert "reply_contains_any" in case["expected"]

    world = json.loads((fixture_dir / "world.json").read_text())
    assert "rooms" in world

    memory = json.loads((fixture_dir / "memory.json").read_text())
    assert "objects" in memory


def test_check_medicine_fixture_structure() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "home_tasks" / "check_medicine_success"
    case = json.loads((fixture_dir / "case.json").read_text())
    assert case["name"] == "check_medicine_success"
    assert "utterance" in case


def test_object_not_found_fixture_structure() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "home_tasks" / "object_not_found"
    case = json.loads((fixture_dir / "case.json").read_text())
    assert case["name"] == "object_not_found"
    assert "utterance" in case


def test_fixture_forbidden_keys() -> None:
    """Fixtures must not contain old stage/scenario keys."""
    forbidden = {"scenario", "runtime_modes", "deterministic", "stage", "stage_", "stage_statuses"}
    fixtures_dir = Path(__file__).parent / "fixtures" / "home_tasks"
    for case_dir in fixtures_dir.iterdir():
        if not case_dir.is_dir():
            continue
        for json_file in case_dir.glob("*.json"):
            data = json.loads(json_file.read_text())
            keys = set(data.keys())
            overlap = keys & forbidden
            assert not overlap, f"{json_file} has forbidden keys: {overlap}"
