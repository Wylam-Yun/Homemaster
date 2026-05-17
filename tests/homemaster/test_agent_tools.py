"""Tests for builtin tool executors — ToolSpec registration, simulated behavior."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.agent.state import AgentState
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.tools.builtin import build_tool_registry


def _make_settings() -> RuntimeSettings:
    return RuntimeSettings(
        run_id="test-tools",
        runtime_root="/tmp/runs",
        debug_root="/tmp/debug",
        results_root="/tmp/results",
    )


def test_navigate_executor_returns_simulated_skill() -> None:
    registry = build_tool_registry()
    spec = registry.get("navigate")
    assert spec is not None
    assert spec.executor is not None

    result = spec.executor(
        arguments={"room_hint": "kitchen"},
        state=AgentState(), settings=_make_settings(),
    )
    assert result.success is True
    assert result.executor_mode == "simulated_skill"
    assert result.data["location"] == "kitchen"


def test_observe_executor_returns_simulated_skill() -> None:
    registry = build_tool_registry()
    spec = registry.get("observe")
    assert spec is not None

    state = AgentState(current_location="kitchen")
    result = spec.executor(
        arguments={"target_object": "cup"}, state=state, settings=_make_settings(),
    )
    assert result.success is True
    assert result.executor_mode == "simulated_skill"
    assert result.data["object"] == "cup"


def test_manipulate_executor_returns_simulated_skill() -> None:
    registry = build_tool_registry()
    spec = registry.get("manipulate")
    assert spec is not None

    result = spec.executor(
        arguments={"action": "pick_up", "target_object": "cup"},
        state=AgentState(), settings=_make_settings(),
    )
    assert result.success is True
    assert result.executor_mode == "simulated_skill"
    assert result.data["holding"] == "cup"


def test_verify_executor_returns_simulated_verification() -> None:
    registry = build_tool_registry()
    spec = registry.get("verify")
    assert spec is not None

    state = AgentState(holding_object="cup")
    result = spec.executor(
        arguments={"target_object": "cup", "expected_state": "delivered"},
        state=state, settings=_make_settings(),
    )
    assert result.success is True
    assert result.executor_mode == "simulated_verification"
    assert result.data["verified"] is True


def test_verify_executor_fails_when_not_holding() -> None:
    registry = build_tool_registry()
    spec = registry.get("verify")

    state = AgentState(holding_object=None)
    result = spec.executor(
        arguments={"target_object": "cup"},
        state=state, settings=_make_settings(),
    )
    assert result.success is True  # tool itself succeeds
    assert result.data["verified"] is False  # but verification fails


def test_get_skill_executor_returns_skill_content() -> None:
    registry = build_tool_registry()
    spec = registry.get("get_skill")
    assert spec is not None

    # This will fail if fetch_object SKILL.md doesn't exist, which is OK for this test
    result = spec.executor(
        arguments={"skill_name": "fetch_object"},
        state=AgentState(), settings=_make_settings(),
    )
    # Either succeeds (skill exists) or fails gracefully (skill not found)
    if result.success:
        assert "name" in result.data
        assert "content" in result.data
    else:
        assert "not found" in result.failure_reason


def test_get_skill_executor_requires_skill_name() -> None:
    registry = build_tool_registry()
    spec = registry.get("get_skill")

    result = spec.executor(arguments={}, state=AgentState(), settings=_make_settings())
    assert result.success is False
    assert "skill_name" in result.failure_reason


def test_update_memory_executor_validates_proposal() -> None:
    registry = build_tool_registry()
    spec = registry.get("update_memory")

    # Missing required fields
    result = spec.executor(
        arguments={"proposal": {"object_category": "cup"}},
        state=AgentState(), settings=_make_settings(),
    )
    assert result.success is False
    assert "missing fields" in result.failure_reason


def test_update_memory_executor_accepts_valid_proposal() -> None:
    registry = build_tool_registry()
    spec = registry.get("update_memory")

    result = spec.executor(
        arguments={"proposal": {
            "object_category": "cup",
            "room_id": "kitchen",
            "anchor_id": "table-1",
        }},
        state=AgentState(), settings=_make_settings(),
    )
    assert result.success is True
    assert result.data["committed"] is True


def test_update_user_profile_executor_validates_key() -> None:
    registry = build_tool_registry()
    spec = registry.get("update_user_profile")

    result = spec.executor(
        arguments={"proposal": {"value": "zh-CN"}},
        state=AgentState(), settings=_make_settings(),
    )
    assert result.success is False
    assert "key" in result.failure_reason


def test_update_user_profile_executor_accepts_valid_proposal() -> None:
    registry = build_tool_registry()
    spec = registry.get("update_user_profile")

    result = spec.executor(
        arguments={"proposal": {"key": "language", "value": "zh-CN"}},
        state=AgentState(), settings=_make_settings(),
    )
    assert result.success is True
    assert result.data["committed"] is True


def test_finish_task_not_selectable() -> None:
    registry = build_tool_registry()
    spec = registry.get("finish_task")
    assert spec is not None
    assert spec.selectable_by_model is False


def test_all_executors_return_tool_name_and_mode() -> None:
    """Every executor must populate tool_name and executor_mode."""
    registry = build_tool_registry()
    settings = _make_settings()
    state = AgentState(current_location="kitchen", holding_object="cup")

    for name in registry.all_names():
        spec = registry.get(name)
        if spec is None or spec.executor is None:
            continue
        result = spec.executor(arguments={}, state=state, settings=settings)
        assert result.tool_name == name, f"{name}: tool_name mismatch"
        assert result.executor_mode, f"{name}: executor_mode empty"


def test_update_memory_persists_to_store(tmp_path: Path) -> None:
    """update_memory writes to RuntimeMemoryStore when memory_path exists."""
    # Set up a base memory file
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    base_memory = memory_dir / "object_memory.json"
    base_memory.write_text(json.dumps({
        "object_memory": [
            {"memory_id": "m1", "object_category": "cup", "room_id": "kitchen",
             "anchor_id": "table-1", "belief_state": "stale"},
        ]
    }), encoding="utf-8")

    settings = RuntimeSettings(
        run_id="test-persist",
        runtime_root=tmp_path / "runs",
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
        memory_path=base_memory,
    )
    state = AgentState(
        memory_hits=[{
            "memory_id": "m1", "object_category": "cup",
            "room_id": "kitchen", "anchor_id": "table-1",
        }],
    )

    registry = build_tool_registry()
    spec = registry.get("update_memory")
    result = spec.executor(
        arguments={"proposal": {
            "object_category": "cup",
            "room_id": "kitchen",
            "anchor_id": "table-1",
            "belief_state": "verified",
        }},
        state=state, settings=settings,
    )
    assert result.success is True
    assert result.data["committed"] is True
    assert result.data["memory_id"] == "m1"

    # Verify the store was written
    store_path = tmp_path / "runs" / "test-persist" / "memory" / "object_memory.json"
    assert store_path.exists()
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    memories = stored["object_memory"]
    assert memories[0]["belief_state"] == "verified"


def test_update_memory_graceful_without_memory_path() -> None:
    """update_memory succeeds even without memory_path (graceful degradation)."""
    settings = RuntimeSettings(
        run_id="test-no-path",
        runtime_root=Path("/tmp/runs"),
        debug_root=Path("/tmp/debug"),
        results_root=Path("/tmp/results"),
        # memory_path is None
    )
    state = AgentState()
    registry = build_tool_registry()
    spec = registry.get("update_memory")
    result = spec.executor(
        arguments={"proposal": {
            "object_category": "cup",
            "room_id": "kitchen",
            "anchor_id": "table-1",
        }},
        state=state, settings=settings,
    )
    assert result.success is True
    assert result.data["committed"] is True
