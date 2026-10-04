"""Golden lock for the web-facing event surface of a real ``AsAgentRuntime`` run.

The substrate already converts ``AgentEvent`` items into ``RuntimeEvent``;
this suite runs the canonical tool-call round-trip through the real chain
(AS Agent -> Toolkit -> HomeToolAdapter -> ToolExecutor) and projects the
emitted ``RuntimeEvent`` sequence through the same ``WebEventProjection``
the web app uses. The projected ``WebEvent`` sequence is the actual wire
surface — asserting it end-to-end locks ordering, allowlisted fields,
private-event drop behaviour and the single-terminal-final invariant in
one shot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentscope.message import TextBlock, ToolCallBlock
from homemaster.agent.session import AgentSession
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    project_stream_event,
)
from homemaster.tools.base import ToolRegistry
from homemaster.tools.executor import ToolExecutor
from homemaster.web.event_projection import WebEventProjection
from tests.homemaster import test_as_runtime as as_harness


async def _canonical_run_events(tmp_path: Path) -> list[RuntimeEvent]:
    registry = ToolRegistry()
    registry.register(as_harness._echo_tool())
    executor = ToolExecutor(registry)
    runtime, _model = as_harness._runtime(
        tmp_path,
        script=[
            [ToolCallBlock(id="tc1", name="echo", input='{"x": 1}')],
            [TextBlock(text="done")],
        ],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session, "say hi", settings=as_harness._settings(tmp_path)
    )
    assert result.status == "replied"
    return list(result.events)


@pytest.mark.asyncio
async def test_real_run_projects_the_locked_web_event_sequence(
    tmp_path: Path,
) -> None:
    events = await _canonical_run_events(tmp_path)
    projection = WebEventProjection(include_thinking=True)

    projected = [
        web_event
        for event in events
        for web_event in projection.project(event, request_id="req-1")
    ]

    # The whole browser-visible sequence, wire shape and order.
    assert [(e.type, e.request_id) for e in projected] == [
        ("run.started", "req-1"),
        ("tool.started", "req-1"),
        ("usage.updated", "req-1"),
        ("tool.completed", "req-1"),
        ("answer.delta", "req-1"),
        ("usage.updated", "req-1"),
        ("answer.snapshot", "req-1"),
        ("run.completed", "req-1"),
    ]

    by_type = {e.type: e for e in projected}
    assert by_type["tool.started"].payload == {
        "tool_call_id": "tc1",
        "name": "echo",
        "arguments": {"x": 1},
    }
    assert by_type["tool.completed"].payload == {
        "tool_call_id": "tc1",
        "name": "echo",
        "status": "completed",
        "output": "echo:1",
        "artifacts": [],
    }
    assert by_type["answer.snapshot"].payload == {"text": "done"}

    # Exactly one terminal final; the empty intermediate assistant.reply
    # from the tool-call turn produced no snapshot.
    assert [e.type for e in projected].count("run.completed") == 1
    assert [e.type for e in projected].count("answer.snapshot") == 1

    # usage.updated carries only allowlisted token fields.
    for event in projected:
        if event.type == "usage.updated":
            assert set(event.payload) <= {
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            }


@pytest.mark.asyncio
async def test_private_events_and_internal_fields_never_reach_the_wire(
    tmp_path: Path,
) -> None:
    events = await _canonical_run_events(tmp_path)
    projection = WebEventProjection(include_thinking=True)

    # Internal event types present in the real run are dropped wholesale.
    internal_types = {
        "transport.request_started",
        "transport.response_completed",
    }
    # Precondition: the canonical run really emits them — otherwise the
    # loop below asserts nothing.
    assert internal_types <= {event.type for event in events}
    for event in events:
        if event.type in internal_types:
            assert projection.project(event, request_id="req-1") == ()

    # Synthetic private events routed through the same boundary drop too —
    # including types that carry internal-only semantics.
    private = (
        RuntimeEvent(
            type="extension.hook_completed",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"extension_id": "x", "hook_id": "y", "output": "z"},
        ),
        RuntimeEvent(
            type="memory.automatic_recall",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"memories": [{"text": "internal"}]},
        ),
        RuntimeEvent(
            type="tool.execution_published",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"raw": "tool internals"},
        ),
    )
    for event in private:
        assert projection.project(event, request_id="req-1") == ()

    # usage.update extra keys and event-level internals never leak into
    # projected payloads.
    leaked = projection.project(
        RuntimeEvent(
            type="usage.update",
            session_id="s",
            run_id="r",
            turn_index=0,
            gateway_generation=7,
            payload={
                "input_tokens": 1,
                "cost_usd": 0.5,
                "provider_metadata": {"key": "sk-secret"},
            },
        ),
        request_id="req-1",
    )
    assert len(leaked) == 1
    assert leaked[0].payload == {"input_tokens": 1}
    assert "generation" not in json.dumps(leaked[0].payload)
    assert "sk-secret" not in json.dumps(leaked[0].payload)


@pytest.mark.asyncio
async def test_stream_surface_golden_and_no_internal_type_fallback(
    tmp_path: Path,
) -> None:
    events = await _canonical_run_events(tmp_path)
    stream = [
        projected
        for event in events
        if (projected := project_stream_event(event)) is not None
    ]

    assert [type(item) for item in stream] == [
        ToolExecutionStarted,
        AssistantTurnComplete,
        ToolExecutionCompleted,
        AssistantTextDelta,
        AssistantTurnComplete,
    ]
    started, tool_turn, completed, delta, done = stream
    assert started.tool_name == "echo"
    # The tool-call turn's assistant.reply carries the tool_call record —
    # empty text is expected on intermediate turns.
    assert tool_turn.message.tool_calls[0].name == "echo"
    assert completed.tool_name == "echo"
    assert completed.output == "echo:1"
    assert delta.text == "done"
    assert done.message.tool_calls == []

    # turn_failed with no user-semantic error text is dropped, never
    # projected as the internal event type name.
    assert (
        project_stream_event(
            RuntimeEvent(
                type="runtime.turn_failed",
                session_id="s",
                run_id="r",
                turn_index=0,
                payload={},
            )
        )
        is None
    )
    assert (
        project_stream_event(
            RuntimeEvent(
                type="runtime.budget_exhausted",
                session_id="s",
                run_id="r",
                turn_index=0,
                payload={"error": "", "error_code": ""},
            )
        )
        is None
    )
    # ...while a real failure keeps its typed code and message.
    failed = project_stream_event(
        RuntimeEvent(
            type="runtime.turn_failed",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"error": "deadline hit", "error_code": "deadline_exceeded"},
        )
    )
    assert failed is not None
    assert failed.message == "deadline hit"
    assert failed.recoverable is True


def test_gateway_projection_uses_verbatim_error_then_fixed_phrase() -> None:
    """transport failures surface their error text; a bare event with no
    user-semantic text gets a fixed phrase — never the internal type name."""

    from homemaster.events.public_projection import PublicEventProjection

    projection = PublicEventProjection()

    failed = projection.project(
        RuntimeEvent(
            type="transport.request_failed",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"error": "connection reset", "error_type": "network_error"},
        )
    )
    assert failed is not None
    assert failed.content == "connection reset"

    coded = projection.project(
        RuntimeEvent(
            type="runtime.turn_failed",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"error_code": "deadline_exceeded"},
        )
    )
    assert coded is not None
    assert coded.content == "deadline_exceeded"

    bare = projection.project(
        RuntimeEvent(
            type="runtime.turn_failed",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={},
        )
    )
    assert bare is not None
    assert bare.content == "run failed"

    cancelled = projection.project(
        RuntimeEvent(
            type="runtime.cancelled",
            session_id="s",
            run_id="r",
            turn_index=0,
            payload={"phase": "as_reply"},
        )
    )
    assert cancelled is not None
    assert cancelled.content == "run cancelled"
    for event in (failed, coded, bare, cancelled):
        assert "runtime." not in event.content and "transport." not in event.content


__all__ = []
