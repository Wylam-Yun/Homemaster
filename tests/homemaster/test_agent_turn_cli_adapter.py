"""Tests for the CLI-facing agent turn adapter."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from homemaster.agent.messages import AssistantMessage, ContentBlock, Message, UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.turn import (
    _build_run_context,
    _build_tool_dispatcher_and_specs,
    compact_agent_context,
    run_agent_turn,
)
from homemaster.providers.transports import TransportDelta
from homemaster.task_state.store import TaskStateStore

EXPECTED_HOME_TOOLS = [
    "task_interpreter",
    "memory_retriever",
    "target_grounder",
    "skill_view",
    "robot_go_to",
    "observe",
    "robot_manipulate",
    "robot_verify",
    "memory_writer",
    "task_summarizer",
    "task_planner",
    "task_progress_check",
]
EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "config/homemaster.example.yaml"


class FakeTransport:
    def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str = "",
        event_sink: Any = None,
        run_id: str = "",
        session_id: str = "",
        turn_index: int | None = None,
        iteration: int | None = None,
    ) -> Iterator[TransportDelta]:
        yield TransportDelta(type="transport.delta", text_delta="你好，我在。")
        yield TransportDelta(type="transport.delta", finish_reason="stop")

    def complete(
        self,
        messages: list[Message],
        *,
        system_prompt: str = "",
        **kwargs: Any,
    ) -> AssistantMessage:
        return AssistantMessage(content=[ContentBlock(text="manual compact summary")])


def test_cli_adapter_registers_v14_home_tools() -> None:
    _dispatcher, schemas = _build_tool_dispatcher_and_specs("r1")
    assert [schema["name"] for schema in schemas] == EXPECTED_HOME_TOOLS


def test_run_context_carries_memory_path_and_drops_dead_world_path(tmp_path: Path) -> None:
    world_path = tmp_path / "world.json"
    memory_path = tmp_path / "memory.json"
    context = _build_run_context(
        "r1",
        "s1",
        world_path=world_path,
        memory_path=memory_path,
    )
    assert not hasattr(context.settings, "world_path")
    assert context.settings.memory_path == memory_path


def test_run_context_carries_task_state_store(tmp_path: Path) -> None:
    context = _build_run_context(
        "r1",
        "s1",
        world_path=tmp_path / "world.json",
        memory_path=tmp_path / "memory.json",
    )

    assert isinstance(context.deps["task_state_store"], TaskStateStore)


def test_run_agent_turn_writes_trace_and_uses_transport_stream(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("homemaster.agent.turn._build_transport", lambda: FakeTransport())
    monkeypatch.setattr("homemaster.agent.turn.HOMEMASTER_CONFIG_PATH", EXAMPLE_CONFIG)
    run_id = f"test-trace-{uuid.uuid4().hex}"
    result = run_agent_turn(
        AgentSession(session_id="s1"),
        "你好",
        run_id=run_id,
        world_path=tmp_path / "world.json",
        memory_path=tmp_path / "memory.json",
        progress=False,
    )
    assert result.final_reply == "你好，我在。"
    assert result.trace_path is not None
    assert result.trace_path.exists()
    events = [
        json.loads(line)
        for line in result.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["type"] for event in events] == [
        "runtime.turn_started",
        "assistant.reply",
        "runtime.turn_completed",
    ]


def test_run_agent_turn_force_compact_writes_manual_compaction_event(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("homemaster.agent.turn._build_transport", lambda: FakeTransport())
    monkeypatch.setattr("homemaster.agent.turn.HOMEMASTER_CONFIG_PATH", EXAMPLE_CONFIG)
    session = AgentSession(session_id="s1")
    for index in range(4):
        session.append(UserMessage.from_text(f"old user turn {index} " + "x" * 400))
        session.append(AssistantMessage(content=[ContentBlock(text="old answer " + "y" * 400)]))

    run_id = f"test-compact-{uuid.uuid4().hex}"
    result = run_agent_turn(
        session,
        "当前请求",
        run_id=run_id,
        world_path=tmp_path / "world.json",
        memory_path=tmp_path / "memory.json",
        progress=False,
        force_compact=True,
    )

    assert result.trace_path is not None
    events = [
        json.loads(line)
        for line in result.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    compaction_events = [
        event for event in events if event["type"] == "context.compaction"
    ]
    assert len(compaction_events) == 1
    assert compaction_events[0]["payload"]["trigger"] == "manual"
    assert compaction_events[0]["payload"]["kind"] == "manual_summary"
    assert result.final_reply == "你好，我在。"


def test_compact_agent_context_immediately_writes_manual_compaction_event(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("homemaster.agent.turn._build_transport", lambda: FakeTransport())
    monkeypatch.setattr("homemaster.agent.turn.HOMEMASTER_CONFIG_PATH", EXAMPLE_CONFIG)
    session = AgentSession(session_id="s1")
    for index in range(4):
        session.append(UserMessage.from_text(f"old user turn {index} " + "x" * 400))
        session.append(AssistantMessage(content=[ContentBlock(text="old answer " + "y" * 400)]))

    run_id = f"test-compact-now-{uuid.uuid4().hex}"
    result = compact_agent_context(
        session,
        run_id=run_id,
        progress=False,
    )

    assert result.status == "compacted"
    assert result.compaction_triggered is True
    assert result.trace_path is not None
    events = [
        json.loads(line)
        for line in result.trace_path.read_text(encoding="utf-8").splitlines()
    ]
    compaction_events = [
        event for event in events if event["type"] == "context.compaction"
    ]
    assert len(compaction_events) == 1
    assert compaction_events[0]["payload"]["trigger"] == "manual"
    assert compaction_events[0]["payload"]["kind"] == "manual_summary"
