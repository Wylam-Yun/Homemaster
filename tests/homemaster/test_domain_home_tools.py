"""Tests for home domain tool registry and executors."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.domain.tool_registry import build_home_tool_registry
from homemaster.domain.tools import make_skill
from homemaster.tools.results import ToolResult


def _make_run_context(
    tmp_path: Path,
    *,
    memory_path: Path | None = None,
    deps: dict[str, Any] | None = None,
) -> RunContext:
    settings = SimpleNamespace(
        run_id="test-run",
        runtime_root=tmp_path,
        debug_root=tmp_path / "debug",
        results_root=tmp_path / "results",
        memory_path=memory_path,
    )
    return RunContext(
        session_id="s1",
        run_id="test-run",
        turn_index=0,
        settings=settings,
        event_sink=None,
        deps=deps or {},
    )


def test_home_tool_registry_exposes_robot_tools() -> None:
    registry = build_home_tool_registry()
    names = set(registry.all_names())
    assert {
        "task_interpreter",
        "memory_retriever",
        "target_grounder",
        "skill_view",
        "robot_navigate",
        "robot_manipulate",
        "robot_verify",
        "memory_writer",
        "task_summarizer",
    } <= names


def test_task_interpreter_returns_structured_result(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("task_interpreter")
    assert spec is not None
    result = spec.executor(
        arguments={"utterance": "去厨房找水杯"},
        run_context=_make_run_context(tmp_path),
    )
    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data["utterance"] == "去厨房找水杯"
    assert result.data["intent"] == "home_assistance"


def test_task_interpreter_requires_utterance(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("task_interpreter")
    result = spec.executor(arguments={}, run_context=_make_run_context(tmp_path))
    assert result.success is False
    assert "utterance" in (result.failure_reason or "")


def test_memory_retriever_returns_failure_when_no_memory_file(tmp_path: Path) -> None:
    registry = build_home_tool_registry(memory_path=tmp_path / "missing.json")
    spec = registry.get("memory_retriever")
    result = spec.executor(
        arguments={"query": "水杯"},
        run_context=_make_run_context(tmp_path, memory_path=tmp_path / "missing.json"),
    )
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert "not found" in (result.failure_reason or result.summary or "").lower()


def test_memory_retriever_finds_matching_objects(tmp_path: Path) -> None:
    import json

    memory_path = tmp_path / "memory.json"
    memory_path.write_text(json.dumps({
        "objects": [
            {"object_category": "cup", "anchor": {"room_id": "kitchen"}, "belief_state": "present"},
            {"object_category": "book", "anchor": {"room_id": "study"}, "belief_state": "present"},
        ]
    }), encoding="utf-8")

    registry = build_home_tool_registry(memory_path=memory_path)
    spec = registry.get("memory_retriever")
    result = spec.executor(
        arguments={"query": "cup"},
        run_context=_make_run_context(tmp_path, memory_path=memory_path),
    )
    assert result.success is True
    assert result.data["hit_count"] == 1
    assert result.data["hits"][0]["object_category"] == "cup"


def test_target_grounder_returns_grounding_result(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("target_grounder")
    result = spec.executor(
        arguments={"target_object": "水杯", "room_hint": "kitchen"},
        run_context=_make_run_context(tmp_path),
    )
    assert result.success is True
    assert result.data["target_object"] == "水杯"
    assert result.data["grounded_location"] == "kitchen"


def test_skill_view_requires_skill_registry_in_deps(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("skill_view")
    result = spec.executor(
        arguments={"skill_name": "fetch_object"},
        run_context=_make_run_context(tmp_path),
    )
    assert result.success is False
    assert "skill_registry" in (result.failure_reason or "")


def test_skill_view_returns_full_skill_content(tmp_path: Path) -> None:
    from homemaster.skills.loader import load_builtin_skills
    from homemaster.skills.registry import SkillRegistry

    skill_registry = SkillRegistry()
    load_builtin_skills(skill_registry)

    registry = build_home_tool_registry()
    spec = registry.get("skill_view")
    result = spec.executor(
        arguments={"skill_name": "fetch_object"},
        run_context=_make_run_context(tmp_path, deps={"skill_registry": skill_registry}),
    )
    assert result.success is True
    assert result.data["name"] == "fetch_object"
    assert result.data["content"].startswith("---")
    assert result.data["base_dir"]


def test_standard_skill_entry_returns_the_same_complete_content(tmp_path: Path) -> None:
    from homemaster.skills.loader import load_builtin_skills
    from homemaster.skills.registry import SkillRegistry

    skill_registry = SkillRegistry()
    load_builtin_skills(skill_registry)

    spec = make_skill()
    result = spec.executor(
        arguments={"name": "fetch_object"},
        run_context=_make_run_context(tmp_path, deps={"skill_registry": skill_registry}),
    )

    assert result.success is True
    assert result.tool_name == "skill"
    assert result.data["content"] == skill_registry.get("fetch_object").content
    assert result.data["base_dir"]


def test_robot_navigate_simulated(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("robot_navigate")
    result = spec.executor(
        arguments={"room_hint": "kitchen"},
        run_context=_make_run_context(tmp_path),
    )
    assert result.success is True
    assert result.data["location"] == "kitchen"


def test_home_legacy_registry_has_no_textual_observe_variant(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    del tmp_path
    assert registry.get("robot_observe") is None
    assert registry.get("observe") is None


def test_robot_manipulate_simulated(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("robot_manipulate")
    result = spec.executor(
        arguments={"action": "pick_up", "target_object": "水杯"},
        run_context=_make_run_context(tmp_path),
    )
    assert result.success is True
    assert result.data["action"] == "pick_up"


def test_robot_verify_simulated(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("robot_verify")
    result = spec.executor(
        arguments={"target_object": "水杯", "expected_state": "delivered"},
        run_context=_make_run_context(tmp_path),
    )
    assert result.success is True
    assert result.data["verified"] is True


def test_task_summarizer_returns_summary(tmp_path: Path) -> None:
    registry = build_home_tool_registry()
    spec = registry.get("task_summarizer")
    result = spec.executor(
        arguments={"task_name": "fetch_cup", "status": "completed", "summary": "水杯已送达"},
        run_context=_make_run_context(tmp_path),
    )
    assert result.success is True
    assert result.data["task_name"] == "fetch_cup"
    assert result.data["status"] == "completed"
