"""Phase-2 gate: ``AsAgentRuntime`` drives the vendored AS ``Agent`` loop
while HomeMaster keeps the runtime contract — events, session mirror,
schema-v2 snapshot, permission funnel.

The scripted ``ChatModelBase`` stands in for the provider wire; everything
below it is the real chain: AS Agent → Toolkit → ``HomeToolAdapter`` →
``ToolExecutor`` → ``PermissionChecker``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agentscope.credential import OpenAICredential
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.model._model_usage import ChatUsage
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config.observability import ObservabilityConfig
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.permissions.store import PermissionStore
from homemaster.substrate.runtime import AsAgentRuntime
from homemaster.substrate.toolkit import HomeToolAdapter, RunScope
from homemaster.tools.base import FunctionTool, ToolRegistry
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionResult,
    ToolExecutionStatus,
)
from homemaster.tools.executor import ToolExecutor


class ScriptedModel(ChatModelBase):
    """Pops one block-list per call, then replies 'done'."""

    def __init__(self, script: list[list[Any]]) -> None:
        super().__init__(
            credential=OpenAICredential(api_key="sk-stub"),
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self._script = list(script)
        self.calls: list[list[Msg]] = []
        self.call_tools: list[list[dict] | None] = []
        self.seen_tool_result_text = ""

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append(messages)
        self.call_tools.append(tools)
        for msg in messages:
            for block in getattr(msg, "content", ()) or ():
                if getattr(block, "type", None) == "tool_result":
                    for out in getattr(block, "output", ()) or ():
                        self.seen_tool_result_text += str(
                            getattr(out, "text", "") or ""
                        )
        blocks = self._script.pop(0) if self._script else [
            TextBlock(text="done")
        ]
        return ChatResponse(
            content=blocks,
            is_last=True,
            usage=ChatUsage(input_tokens=7, output_tokens=3, time=0.0),
            metadata={"stop_reason": "end_turn"},
        )


def _scope(tmp_path: Path) -> RunScope:
    return RunScope(
        session_id="as-runtime-test",
        run_id="run-1",
        permission_subject=PermissionSubject(
            subject_id="test", channel="pytest", tenant_id="t1"
        ),
        working_directory=tmp_path,
    )


def _echo_tool(execute: Any = None) -> FunctionTool:
    return FunctionTool(
        name="echo",
        description="echo",
        input_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        },
        execute=execute
        or (lambda arguments, context: ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"echo:{arguments.get('x')}",
        )),
        read_only=True,
    )


def _runtime(
    tmp_path: Path,
    *,
    script: list[list[Any]],
    registry: ToolRegistry,
    executor: ToolExecutor,
    settings: Any = None,
) -> tuple[AsAgentRuntime, ScriptedModel]:
    scope = _scope(tmp_path)
    tools = [
        HomeToolAdapter(tool, executor, scope) for tool in registry.list_tools()
    ]
    model = ScriptedModel(script)
    return (
        AsAgentRuntime(
            model=model,
            system_prompt="sys",
            tools=tools,
            max_tool_iterations=4,
        ),
        model,
    )


def _settings(tmp_path: Path) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        provider_name="stub",
        observability=ObservabilityConfig(session_dir=str(tmp_path / "sess")),
    )


@pytest.mark.asyncio
async def test_as_runtime_tool_call_round_trip(tmp_path: Path) -> None:
    """User -> AS reasoning -> tool_call -> real HM executor -> tool_result
    -> final reply; the session mirror and v2 snapshot follow."""
    registry = ToolRegistry()
    registry.register(_echo_tool())
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
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
        session, "say hi", settings=_settings(tmp_path)
    )

    assert result.status == "replied"
    assert result.final_reply == "done"
    assert model.calls and len(model.calls) == 2
    assert "echo:1" in model.seen_tool_result_text

    # Session mirror projects the engine context: user → assistant → tool →
    # assistant (the AS tool_call/tool_result blocks re-enter as HM roles).
    roles = [m.role for m in session.messages]
    assert "tool" in roles
    tool_msg = next(m for m in session.messages if m.role == "tool")
    assert tool_msg.tool_call_id == "tc1"

    event_types = [e.type for e in result.events]
    for expected in (
        "runtime.turn_started",
        "transport.request_started",
        "transport.response_completed",
        "tool.call_started",
        "tool.call_completed",
        "assistant.reply",
        "runtime.turn_completed",
    ):
        assert expected in event_types, event_types

    # Schema-v2 snapshot: engine-authoritative context persisted.
    snapshot_path = (
        tmp_path / "sess" / "as-runtime-test" / "session.json"
    )
    payload = json.loads(snapshot_path.read_text())
    assert payload["schema_version"] == 2
    assert payload["agentscope_state"]["context"], payload.keys()
    assert payload["agent_state"]["run_id"] == result.run_id


@pytest.mark.asyncio
async def test_as_runtime_text_only(tmp_path: Path) -> None:
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
        tmp_path,
        script=[[TextBlock(text="hello there")]],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert result.status == "replied"
    assert result.final_reply == "hello there"
    assert len(model.calls) == 1
    assert [e.type for e in result.events][:1] == ["runtime.turn_started"]


@pytest.mark.asyncio
async def test_as_runtime_permission_denied(tmp_path: Path) -> None:
    """denied_tools -> evaluate_tool DENY -> AS denied ToolResult; the tool
    body (boom) must never run."""
    def _boom(arguments: Any, context: Any) -> Any:
        raise AssertionError("denied tool must not execute")

    registry = ToolRegistry()
    registry.register(_echo_tool(execute=_boom))
    store = PermissionStore.open(tmp_path / "perm.sqlite3")
    checker = PermissionChecker(
        PermissionSettingsConfig(denied_tools=("echo",)),
        store=store,
    )
    executor = ToolExecutor(registry, permission_checker=checker)
    runtime, model = _runtime(
        tmp_path,
        script=[
            [ToolCallBlock(id="tc9", name="echo", input='{"x": 9}')],
            [TextBlock(text="sorry")],
        ],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(session, "echo", settings=_settings(tmp_path))

    assert result.status == "replied"
    assert result.final_reply == "sorry"
    event_types = [e.type for e in result.events]
    assert "tool.call_failed" in event_types


def _png_b64() -> str:
    import base64
    import io

    from PIL import Image

    img = Image.new("RGB", (2, 2), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.asyncio
async def test_as_runtime_automatic_observation(tmp_path: Path) -> None:
    """requires_model_observation tool -> runtime-owned observe call runs
    through the same HM executor; image evidence lands on the action result
    and the consume event fires on the next model call."""
    import base64
    import hashlib

    from homemaster.tools.contracts import ResultImage

    calls: list[str] = []
    png = _png_b64()
    sha = hashlib.sha256(base64.b64decode(png)).hexdigest()

    def _action(arguments: Any, context: Any) -> ToolExecutionResult:
        calls.append("robot_go_to")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="moved",
            backend_attempted=True,
        )

    def _observe(arguments: Any, context: Any) -> ToolExecutionResult:
        calls.append("observe")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="shot",
            images=[
                ResultImage(
                    media_type="image/png",
                    data_base64=png,
                    content_sha256=sha,
                )
            ],
        )

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="robot_go_to",
            description="Move.",
            input_schema={"type": "object", "properties": {}},
            execute=_action,
            requires_model_observation=True,
        )
    )
    registry.register(
        FunctionTool(
            name="observe",
            description="Observe.",
            input_schema={"type": "object", "properties": {}},
            execute=_observe,
        )
    )
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
        tmp_path,
        script=[
            [ToolCallBlock(id="a1", name="robot_go_to", input="{}")],
            [TextBlock(text="done")],
        ],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session, "move", settings=_settings(tmp_path), tool_registry=registry
    )

    assert result.status == "replied"
    # The auto-observe ran through the same executor funnel.
    assert calls == ["robot_go_to", "observe"]
    event_types = [e.type for e in result.events]
    assert "model_observation.automatic_started" in event_types
    assert "model_observation.automatic_completed" in event_types
    assert "model_observation.image_consumed" in event_types


@pytest.mark.asyncio
async def test_as_runtime_pending_barrier_resume(tmp_path: Path) -> None:
    """A restored pending ModelObservationBarrier restricts the model to the
    observe tool, validates the image, clears the barrier and records the
    consumed marker — mirroring the legacy runtime's resume semantics."""
    import base64
    import hashlib

    from homemaster.agent.state import ModelObservationBarrier
    from homemaster.tools.contracts import ResultImage

    calls: list[str] = []
    png = _png_b64()
    sha = hashlib.sha256(base64.b64decode(png)).hexdigest()

    def _observe(arguments: Any, context: Any) -> ToolExecutionResult:
        calls.append("observe")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="shot",
            images=[
                ResultImage(
                    media_type="image/png",
                    data_base64=png,
                    content_sha256=sha,
                )
            ],
        )

    def _read(arguments: Any, context: Any) -> ToolExecutionResult:
        calls.append("read_state")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="state"
        )

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="observe",
            description="Observe.",
            input_schema={"type": "object", "properties": {}},
            execute=_observe,
        )
    )
    registry.register(
        FunctionTool(
            name="read_state",
            description="Read.",
            input_schema={"type": "object", "properties": {}},
            execute=_read,
        )
    )
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
        tmp_path,
        script=[
            [ToolCallBlock(id="o1", name="observe", input="{}")],
            [TextBlock(text="cleared")],
        ],
        registry=registry,
        executor=executor,
    )
    agent_state = AgentState(
        run_id="resume",
        session_id="as-runtime-test",
        pending_model_observation=ModelObservationBarrier(
            source_tool_name="robot_go_to",
            source_tool_call_id="action-1",
            source_status="success",
            observe_tool_name="observe",
        ),
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session,
        "continue",
        settings=_settings(tmp_path),
        agent_state=agent_state,
        tool_registry=registry,
    )

    assert result.status == "replied"
    assert calls == ["observe"]
    # While the barrier was pending the model could only see `observe`.
    tools_seen = model.call_tools[0] or []
    names = {(s.get("function") or s).get("name") for s in tools_seen}
    assert names == {"observe"}
    event_types = [e.type for e in result.events]
    assert "model_observation.barrier_cleared" in event_types
    assert "model_observation.image_consumed" in event_types


@pytest.mark.asyncio
async def test_as_runtime_resume_from_v2_snapshot(tmp_path: Path) -> None:
    """Run -> v2 snapshot -> new runtime resumes with the same engine state;
    the model sees the prior assistant reply in context."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    settings = _settings(tmp_path)

    runtime1, model1 = _runtime(
        tmp_path,
        script=[[TextBlock(text="first answer")]],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result1 = await runtime1.run(session, "q1", settings=settings)
    assert result1.status == "replied"
    engine_state = result1.engine_state
    assert engine_state is not None and engine_state.context

    # Simulate a session reload: parse the on-disk snapshot like
    # ``_runtime_from_snapshot`` does.
    from homemaster.substrate.snapshot import parse_snapshot_payload

    payload = json.loads(
        (tmp_path / "sess" / "as-runtime-test" / "session.json").read_text()
    )
    parsed = parse_snapshot_payload(payload)
    session2 = AgentSession(parsed.session_id)
    session2.replace_messages(list(parsed.messages))

    runtime2, model2 = _runtime(
        tmp_path,
        script=[[TextBlock(text="second answer")]],
        registry=registry,
        executor=executor,
    )
    result2 = await runtime2.run(
        session2,
        "q2",
        settings=settings,
        engine_state=parsed.engine_state,
    )
    assert result2.status == "replied"
    assert result2.final_reply == "second answer"
    # Prior turn content survives the engine-state round-trip into the model.
    joined = json.dumps(
        [
            m.model_dump(mode="json")
            for m in model2.calls[0]
        ],
        default=str,
    )
    assert "first answer" in joined and "q1" in joined


@pytest.mark.asyncio
async def test_as_runtime_sigint_aborts_model_call(tmp_path: Path) -> None:
    """Real SIGINT mid-model-call: the interrupt shim cancels the pending
    ``__anext__`` task; the run ends cancelled with a durable snapshot."""
    import asyncio
    import os
    import signal

    class HungModel(ChatModelBase):
        def __init__(self) -> None:
            super().__init__(
                credential=OpenAICredential(api_key="sk-stub"),
                model="stub",
                parameters=ChatModelBase.Parameters(),
            )
            self.formatter = OpenAIChatFormatter()

        async def _call_api(self, model_name, messages, tools=None, **kw):
            await asyncio.sleep(30)
            return ChatResponse(content=[TextBlock(text="x")], is_last=True)

    runtime = AsAgentRuntime(model=HungModel(), system_prompt="s", tools=[])
    session = AgentSession("as-runtime-test")

    async def _sigint() -> None:
        await asyncio.sleep(0.2)
        os.kill(os.getpid(), signal.SIGINT)

    task = asyncio.create_task(_sigint())
    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    await task
    assert result.status == "cancelled"
    snapshot = tmp_path / "sess" / "as-runtime-test" / "session.json"
    assert snapshot.exists()
    payload = json.loads(snapshot.read_text())
    assert payload["agent_state"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_as_runtime_cancel_with_deadline(tmp_path: Path) -> None:
    """Deadline exceeds mid-model-call -> deadline_exceeded, snapshot
    still written."""
    import asyncio

    class HungModel(ChatModelBase):
        def __init__(self) -> None:
            super().__init__(
                credential=OpenAICredential(api_key="sk-stub"),
                model="stub",
                parameters=ChatModelBase.Parameters(),
            )
            self.formatter = OpenAIChatFormatter()

        async def _call_api(self, model_name, messages, tools=None, **kw):
            await asyncio.sleep(30)
            return ChatResponse(content=[TextBlock(text="x")], is_last=True)

    class _Deadline:
        def remaining_s(self) -> float:
            return 0.2

    runtime = AsAgentRuntime(model=HungModel(), system_prompt="s", tools=[])
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session,
        "hi",
        settings=_settings(tmp_path),
        deadline=_Deadline(),
    )
    assert result.status == "failed"
    assert result.error_code == "deadline_exceeded"
    snapshot = tmp_path / "sess" / "as-runtime-test" / "session.json"
    assert snapshot.exists()
    payload = json.loads(snapshot.read_text())
    assert payload["agent_state"]["status"] == "failed"
