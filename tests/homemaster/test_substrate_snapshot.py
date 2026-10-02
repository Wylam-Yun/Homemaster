"""Schema-v2 snapshot round-trip tests per decision-snapshot-schema.md §3."""

from __future__ import annotations

import pytest

from agentscope.message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from agentscope.state import AgentState as EngineState

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState as RunBookkeepingState
from homemaster.substrate import (
    MessageConversionError,
    build_snapshot_payload,
    from_agent_scope,
    parse_snapshot_payload,
)
from homemaster.substrate.snapshot import SNAPSHOT_SCHEMA_VERSION
from homemaster.task_state.store import TaskStateStore


def _engine_state(context: list[Msg] | None = None) -> EngineState:
    return EngineState(session_id="sess-1", context=context or [])


def _build(engine: EngineState, **kwargs) -> dict:
    return build_snapshot_payload(
        engine_state=engine,
        run_state=RunBookkeepingState(),
        task_state_store=TaskStateStore(run_id="r1"),
        model="m",
        system_prompt="sys",
        **kwargs,
    )


def test_v2_roundtrip_engine_context_is_authoritative() -> None:
    context = [
        Msg(name="u", role="user", content=[TextBlock(text="hi")]),
        Msg(
            name="a",
            role="assistant",
            content=[
                ToolCallBlock(
                    id="tc-9", name="home_device", input='{"op":"enter"}',
                    state="asking",
                )
            ],
        ),
    ]
    payload = _build(_engine_state(context))
    assert payload["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    parsed = parse_snapshot_payload(payload)
    assert not parsed.migrated_from_v1
    restored = parsed.engine_state
    assert restored.session_id == "sess-1"
    assert len(restored.context) == 2
    call = restored.context[1].content[0]
    assert isinstance(call, ToolCallBlock)
    assert call.state == "asking"  # lifecycle survives (projection can't)
    assert restored.context[1].content[0].id == "tc-9"


def test_v2_messages_projection_matches_context() -> None:
    context = [
        Msg(name="u", role="user", content=[TextBlock(text="hi")]),
        Msg(
            name="a",
            role="assistant",
            content=[
                ToolCallBlock(id="tc-1", name="home_device", input="{}"),
                ToolResultBlock(
                    id="tc-1", name="home_device",
                    output=[TextBlock(text="ok")], state="success",
                ),
            ],
        ),
    ]
    payload = _build(_engine_state(context))
    parsed = parse_snapshot_payload(payload)
    projected = from_agent_scope(parsed.engine_state.context)
    assert [m.model_dump(mode="json") for m in projected] == [
        m.model_dump(mode="json") for m in parsed.messages
    ]


def test_v1_snapshot_migrates() -> None:
    session = AgentSession(session_id="sess-old")
    session.append(
        UserMessage(role="user", content=[ContentBlock(type="text", text="hi")])
    )
    session.append(
        AssistantMessage(
            role="assistant",
            content=[ContentBlock(type="text", text="hello")],
        )
    )
    v1 = session.to_snapshot_dict(
        agent_state=RunBookkeepingState(),
        task_state_store=TaskStateStore(run_id="r1"),
        model="m", system_prompt="s",
    )
    assert v1["schema_version"] == 1
    parsed = parse_snapshot_payload(v1)
    assert parsed.migrated_from_v1
    assert len(parsed.engine_state.context) == 2
    assert parsed.engine_state.context[0].role == "user"


def test_future_schema_version_rejected() -> None:
    payload = {"schema_version": 99, "session_id": "x"}
    with pytest.raises(MessageConversionError, match="schema_version"):
        parse_snapshot_payload(payload)


def test_image_stripped_in_both_faces() -> None:
    image = DataBlock(
        source=Base64Source(data="AAAA", media_type="image/png")
    )
    context = [
        Msg(
            name="a",
            role="assistant",
            content=[
                ToolCallBlock(id="tc-i", name="camera", input="{}"),
                ToolResultBlock(
                    id="tc-i", name="camera",
                    output=[TextBlock(text="shot"), image],
                    state="success",
                ),
            ],
        ),
    ]
    payload = _build(_engine_state(context))
    parsed = parse_snapshot_payload(payload)
    result = parsed.engine_state.context[0].content[1]
    assert isinstance(result, ToolResultBlock)
    out = result.output
    assert all(not isinstance(b, DataBlock) for b in out)
    assert any("image stripped" in b.text for b in out)
    # Projection face agrees.
    assert not any(
        b.type == "image"
        for m in parsed.messages
        for b in m.content
    )


def test_preserved_image_tool_call_id_keeps_image() -> None:
    image = DataBlock(
        source=Base64Source(data="BBBB", media_type="image/png")
    )
    context = [
        Msg(
            name="a",
            role="assistant",
            content=[
                ToolCallBlock(id="tc-keep", name="camera", input="{}"),
                ToolResultBlock(
                    id="tc-keep", name="camera",
                    output=[image], state="success",
                ),
            ],
        ),
    ]
    payload = _build(
        _engine_state(context),
        preserve_image_tool_call_ids=frozenset({"tc-keep"}),
    )
    parsed = parse_snapshot_payload(payload)
    result = parsed.engine_state.context[0].content[1]
    assert isinstance(result, ToolResultBlock)
    assert isinstance(result.output[0], DataBlock)


def test_new_fields_and_run_bookkeeping_survive() -> None:
    run_state = RunBookkeepingState(turn_index=3, consecutive_tool_errors=2)
    payload = build_snapshot_payload(
        engine_state=_engine_state(),
        run_state=run_state,
        task_state_store=TaskStateStore(run_id="r1"),
        model="m", system_prompt="s",
        canonical_evidence_refs=("ev-1", "ev-2"),
        require_recall=True,
    )
    parsed = parse_snapshot_payload(payload)
    assert parsed.canonical_evidence_refs == ["ev-1", "ev-2"]
    assert parsed.require_recall is True
    assert parsed.run_state.turn_index == 3
    assert parsed.run_state.consecutive_tool_errors == 2


def test_pending_tool_call_detectable_after_resume() -> None:
    """ASKING/SUBMITTED tool calls survive and are resumable."""
    context = [
        Msg(
            name="a",
            role="assistant",
            content=[
                ToolCallBlock(
                    id="tc-p", name="home_device", input="{}",
                    state="submitted",
                )
            ],
        ),
    ]
    parsed = parse_snapshot_payload(_build(_engine_state(context)))
    msg = parsed.engine_state.context[-1]
    pending = [
        b for b in msg.content
        if isinstance(b, ToolCallBlock) and b.state in ("asking", "submitted")
    ]
    assert pending and pending[0].id == "tc-p"
