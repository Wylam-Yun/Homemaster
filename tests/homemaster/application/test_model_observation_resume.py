from __future__ import annotations

import inspect

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState, ModelObservationBarrier
from homemaster.application.runtime import _FencedAgentSession
from homemaster.task_state.store import TaskStateStore


def _image() -> ContentBlock:
    return ContentBlock(
        type="image",
        source={"type": "base64", "media_type": "image/png", "data": "aW1hZ2U="},
    )


def test_fenced_session_snapshot_interface_matches_agent_session() -> None:
    agent_parameters = inspect.signature(AgentSession.to_snapshot_dict).parameters
    fenced_parameters = inspect.signature(_FencedAgentSession.to_snapshot_dict).parameters

    assert tuple(agent_parameters) == tuple(fenced_parameters)


def test_barrier_and_only_unconsumed_observation_image_survive_snapshot() -> None:
    session = AgentSession("session-observe-resume")
    session.append(
        ToolResultMessage(
            tool_call_id="old-observe",
            name="observe",
            content=[_image()],
        )
    )
    session.append(
        ToolResultMessage(
            tool_call_id="observe-1",
            name="observe",
            content=[_image()],
        )
    )
    state = AgentState(
        session_id=session.session_id,
        pending_model_observation=ModelObservationBarrier(
            source_tool_name="robot_go_to",
            source_tool_call_id="action-1",
            source_status="failure",
        ),
        unconsumed_observation_tool_call_id="observe-1",
    )

    payload = session.to_snapshot_dict(
        agent_state=state,
        task_state_store=TaskStateStore(run_id=session.session_id),
        model="test-model",
        system_prompt="",
        preserve_image_tool_call_ids=frozenset({"observe-1"}),
    )
    restored, restored_state, _task_state = AgentSession.from_snapshot_dict(payload)

    assert restored_state.pending_model_observation is not None
    assert restored_state.pending_model_observation.source_tool_call_id == "action-1"
    assert restored_state.unconsumed_observation_tool_call_id == "observe-1"
    messages = {
        message.tool_call_id: message
        for message in restored.messages
        if isinstance(message, ToolResultMessage)
    }
    assert [block.type for block in messages["observe-1"].content] == ["image"]
    assert [block.type for block in messages["old-observe"].content] == ["text"]
