from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from homemaster.agent.generic_runtime import AgentRuntime
from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import resume_session
from homemaster.agent.state import AgentState
from homemaster.providers.transports.types import TransportDelta
from homemaster.tools.base import FunctionTool, ToolRegistry, ToolResult


def _png_base64() -> str:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color=(18, 52, 86)).save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class _ScriptedTransport:
    def __init__(self, responses: list[list[ToolCall] | str | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def stream(self, messages, *, tools=None, system_prompt="", **kwargs):
        del kwargs
        self.requests.append({"messages": messages, "tools": tools, "system_prompt": system_prompt})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, str):
            yield TransportDelta(type="text", text_delta=response, finish_reason="stop")
            return
        for call in response:
            yield TransportDelta(type="tool_call", tool_call_delta=call)
        yield TransportDelta(type="message", finish_reason="tool_calls")


class _Dispatcher:
    def __init__(
        self,
        *,
        action_backend_attempted: bool = True,
        valid_observe: bool = True,
    ) -> None:
        self.action_backend_attempted = action_backend_attempted
        self.valid_observe = valid_observe
        self.calls: list[str] = []

    async def dispatch(self, *, tool_calls, run_context):
        results = []
        for call in tool_calls:
            self.calls.append(call.name)
            if call.name == "observe":
                content = (
                    [
                        ContentBlock(
                            type="image",
                            source={
                                "type": "base64",
                                "media_type": "image/png",
                                "data": _png_base64(),
                            },
                        )
                    ]
                    if self.valid_observe
                    else [ContentBlock(text="capture failed")]
                )
                results.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        name=call.name,
                        content=content,
                        is_error=not self.valid_observe,
                        data={
                            "status": "success" if self.valid_observe else "failure",
                            "backend_attempted": True,
                        },
                    )
                )
            else:
                results.append(
                    ToolResultMessage(
                        tool_call_id=call.id,
                        name=call.name,
                        content=[ContentBlock(text="action result")],
                        data={
                            "status": "success",
                            "backend_attempted": self.action_backend_attempted,
                        },
                    )
                )
        return results


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="robot_go_to",
            description="Move.",
            input_schema={"type": "object", "properties": {}},
            execute=lambda arguments, context: ToolResult("unused"),
            requires_model_observation=True,
        )
    )
    registry.register(
        FunctionTool(
            name="observe",
            description="Observe.",
            input_schema={"type": "object", "properties": {}},
            execute=lambda arguments, context: ToolResult("unused"),
        )
    )
    return registry


def _settings(session_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        provider_name="test-provider",
        observability=SimpleNamespace(
            interrupt_enabled=False,
            interrupt_abort_llm_stream=True,
            save_session_per_iteration=True,
            save_on_sigint=True,
            session_dir=session_dir,
            strip_images_in_snapshot=True,
            trace_rotation_max_mb=1,
        ),
    )


@pytest.mark.asyncio
async def test_last_normal_iteration_gets_observe_and_post_image_grace() -> None:
    transport = _ScriptedTransport(
        [
            [ToolCall(id="action-1", name="robot_go_to")],
            [ToolCall(id="observe-1", name="observe")],
            "visually confirmed",
        ]
    )
    dispatcher = _Dispatcher()
    state = AgentState()
    result = await AgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=1,
    ).run(
        AgentSession("barrier-grace"),
        "move",
        agent_state=state,
        tool_registry=_registry(),
    )

    assert result.status == "replied"
    assert result.final_reply == "visually confirmed"
    assert dispatcher.calls == ["robot_go_to", "observe"]
    assert [tool["name"] for tool in transport.requests[1]["tools"]] == ["observe"]
    assert "state-changing environment action" in transport.requests[1]["system_prompt"]
    image_blocks = [
        block
        for message in transport.requests[2]["messages"]
        for block in message.content
        if block.type == "image"
    ]
    assert len(image_blocks) == 1
    assert state.pending_model_observation is None
    assert state.unconsumed_observation_tool_call_id is None


@pytest.mark.asyncio
async def test_action_batch_is_rejected_before_any_backend_call() -> None:
    transport = _ScriptedTransport(
        [
            [
                ToolCall(id="action-1", name="robot_go_to"),
                ToolCall(id="observe-early", name="observe"),
            ],
            "stopped",
        ]
    )
    dispatcher = _Dispatcher()
    result = await AgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=2,
    ).run(
        AgentSession("barrier-batch"),
        "move",
        tool_registry=_registry(),
    )

    assert result.status == "replied"
    assert dispatcher.calls == []
    rejected = [
        message for message in result.session.messages if isinstance(message, ToolResultMessage)
    ]
    assert len(rejected) == 2
    assert {message.data["error_code"] for message in rejected} == {
        "model_observation_batch_rejected"
    }
    assert all(message.data["backend_attempted"] is False for message in rejected)


@pytest.mark.asyncio
async def test_backend_not_attempted_does_not_create_barrier() -> None:
    transport = _ScriptedTransport([[ToolCall(id="action-1", name="robot_go_to")], "not attempted"])
    dispatcher = _Dispatcher(action_backend_attempted=False)
    state = AgentState()
    result = await AgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=2,
    ).run(
        AgentSession("barrier-not-attempted"),
        "move",
        agent_state=state,
        tool_registry=_registry(),
    )

    assert result.status == "replied"
    assert dispatcher.calls == ["robot_go_to"]
    assert state.pending_model_observation is None
    assert len(transport.requests[1]["tools"]) == 2


@pytest.mark.asyncio
async def test_invalid_observe_fails_closed_after_bounded_retries() -> None:
    transport = _ScriptedTransport(
        [
            [ToolCall(id="action-1", name="robot_go_to")],
            [ToolCall(id="observe-1", name="observe")],
            [ToolCall(id="observe-2", name="observe")],
            [ToolCall(id="observe-3", name="observe")],
        ]
    )
    dispatcher = _Dispatcher(valid_observe=False)
    state = AgentState()
    result = await AgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=1,
    ).run(
        AgentSession("barrier-invalid-image"),
        "move",
        agent_state=state,
        tool_registry=_registry(),
    )

    assert result.status == "failed"
    assert result.error_code == "model_observation_failed"
    assert dispatcher.calls == ["robot_go_to", "observe", "observe", "observe"]
    assert state.pending_model_observation is not None
    assert state.pending_model_observation.observe_failures == 3
    assert any(
        event.type == "runtime.turn_failed"
        and event.payload.get("error_code") == "model_observation_failed"
        for event in result.events
    )


@pytest.mark.asyncio
async def test_direct_final_is_rejected_and_bounded_while_barrier_is_pending() -> None:
    transport = _ScriptedTransport(
        [
            [ToolCall(id="action-1", name="robot_go_to")],
            "skip one",
            "skip two",
            "skip three",
        ]
    )
    dispatcher = _Dispatcher()
    state = AgentState()
    result = await AgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=1,
    ).run(
        AgentSession("barrier-protocol"),
        "move",
        agent_state=state,
        tool_registry=_registry(),
    )

    assert result.status == "failed"
    assert result.error_code == "model_observation_protocol_failed"
    assert dispatcher.calls == ["robot_go_to"]
    assert state.pending_model_observation is not None
    assert state.pending_model_observation.protocol_failures == 3


@pytest.mark.asyncio
async def test_crash_resume_preserves_image_until_first_successful_provider_response(
    tmp_path: Path,
) -> None:
    first_transport = _ScriptedTransport(
        [
            [ToolCall(id="action-1", name="robot_go_to")],
            [ToolCall(id="observe-1", name="observe")],
            RuntimeError("provider unavailable"),
        ]
    )
    first_state = AgentState()
    first = await AgentRuntime(
        transport=first_transport,
        tool_executor=_Dispatcher(),
        max_tool_iterations=1,
    ).run(
        AgentSession("barrier-crash-resume"),
        "move",
        agent_state=first_state,
        tool_registry=_registry(),
        settings=_settings(tmp_path),
    )

    assert first.status == "failed"
    assert first.error_code == "transport_error"
    assert first_state.unconsumed_observation_tool_call_id == "observe-1"
    snapshot_path = tmp_path / "barrier-crash-resume" / "session.json"
    persisted = json.loads(snapshot_path.read_text(encoding="utf-8"))
    persisted_images = [
        block
        for message in persisted["messages"]
        for block in message["content"]
        if block["type"] == "image"
    ]
    assert len(persisted_images) == 1

    session, restored_state, task_state = resume_session(snapshot_path)
    second_transport = _ScriptedTransport(["visually consumed"])
    second = await AgentRuntime(
        transport=second_transport,
        tool_executor=_Dispatcher(),
        max_tool_iterations=1,
    ).run(
        session,
        "continue",
        agent_state=restored_state,
        task_state_store=task_state,
        tool_registry=_registry(),
        settings=_settings(tmp_path),
    )

    assert second.status == "replied"
    request_images = [
        block
        for message in second_transport.requests[0]["messages"]
        for block in message.content
        if block.type == "image"
    ]
    assert len(request_images) == 1
    assert request_images[0].source["data"] == persisted_images[0]["source"]["data"]
    assert restored_state.unconsumed_observation_tool_call_id is None
    final_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert not any(
        block["type"] == "image"
        for message in final_snapshot["messages"]
        for block in message["content"]
    )
