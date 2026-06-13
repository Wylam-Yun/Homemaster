"""Tests for the CLI-facing agent turn adapter."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from homemaster.agent.messages import Message
from homemaster.agent.session import AgentSession
from homemaster.agent.turn import (
    _build_run_context,
    _build_tool_dispatcher_and_specs,
    run_agent_turn,
)
from homemaster.providers.transport import TransportDelta

EXPECTED_HOME_TOOLS = [
    "task_interpreter",
    "memory_retriever",
    "target_grounder",
    "skill_view",
    "robot_navigate",
    "robot_observe",
    "robot_manipulate",
    "robot_verify",
    "memory_writer",
    "task_summarizer",
]


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


def test_cli_adapter_registers_v14_home_tools() -> None:
    _dispatcher, schemas = _build_tool_dispatcher_and_specs("r1")
    assert [schema["name"] for schema in schemas] == EXPECTED_HOME_TOOLS


def test_run_context_carries_world_and_memory_paths(tmp_path: Path) -> None:
    world_path = tmp_path / "world.json"
    memory_path = tmp_path / "memory.json"
    context = _build_run_context(
        "r1",
        "s1",
        world_path=world_path,
        memory_path=memory_path,
    )
    assert context.settings.world_path == world_path
    assert context.settings.memory_path == memory_path


def test_run_agent_turn_writes_trace_and_uses_transport_stream(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("homemaster.agent.turn._build_transport", lambda: FakeTransport())
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
        "runtime.turn_completed",
    ]
