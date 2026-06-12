from __future__ import annotations

from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry


def test_disabled_memory_registry_excludes_memory_tools() -> None:
    registry = build_alfworld_tool_registry(memory_mode="disabled")
    names = registry.all_names()

    assert "robot_observe" in names
    assert "robot_navigate" in names
    assert "robot_manipulate" in names
    assert "robot_verify" in names
    assert "task_interpreter" not in names
    assert "task_summarizer" not in names
    assert "memory_retriever" not in names
    assert "target_grounder" not in names
    assert "memory_writer" not in names


def test_readonly_memory_registry_adds_retriever_only() -> None:
    names = build_alfworld_tool_registry(memory_mode="readonly").all_names()

    assert "memory_retriever" in names
    assert "memory_writer" not in names
    assert "target_grounder" not in names


def test_full_memory_registry_adds_retriever_and_writer() -> None:
    names = build_alfworld_tool_registry(memory_mode="full").all_names()

    assert "memory_retriever" in names
    assert "memory_writer" in names
    assert "target_grounder" not in names
