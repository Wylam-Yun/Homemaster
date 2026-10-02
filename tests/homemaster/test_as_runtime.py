"""Phase-2 gate: ``AsAgentRuntime`` drives the vendored AS ``Agent`` loop
while HomeMaster keeps the runtime contract — events, session mirror,
schema-v2 snapshot, permission funnel.

The scripted ``ChatModelBase`` stands in for the provider wire; everything
below it is the real chain: AS Agent → Toolkit → ``HomeToolAdapter`` →
``ToolExecutor`` → ``PermissionChecker``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
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


class _FakeAssembler:
    """Minimal ContextAssembler stand-in: returns canned HM messages and a
    marker system prompt; every prepare call is recorded."""

    def __init__(self, *, prompt: str = "HM_SYS_MARKER") -> None:
        self.calls: list[dict[str, Any]] = []
        self._prompt = prompt
        self.shrink = False

    async def aprepare(
        self, *, session, agent_state, task_state_store, tools, force_compact
    ):
        from types import SimpleNamespace

        from homemaster.agent.messages import UserMessage

        self.calls.append({"force_compact": force_compact})
        text = "short" if self.shrink else "filler-filler-filler"
        return SimpleNamespace(
            messages=[UserMessage.from_text(f"assembled:{text}")],
            system_prompt=self._prompt,
            tools=None,
            metrics=SimpleNamespace(
                estimated_input_tokens=11,
                estimated_tokens=11,
                compaction_triggered=bool(force_compact),
                compaction_kind="manual" if force_compact else "none",
            ),
        )


@pytest.mark.asyncio
async def test_as_runtime_assembler_wires_system_prompt(tmp_path: Path) -> None:
    """H1: with an assembler wired, the model input must carry the HM
    system prompt at messages[0] plus the assembled tail — the AS-side
    SystemMsg is replaced, not dropped."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    model = ScriptedModel([[TextBlock(text="ok")]])
    runtime = AsAgentRuntime(
        model=model,
        system_prompt="sys",
        tools=[HomeToolAdapter(t, executor, scope) for t in registry.list_tools()],
        context_assembler=_FakeAssembler(),
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert result.status == "replied"
    first_call = model.calls[0]
    assert getattr(first_call[0], "role", None) == "system"
    head = first_call[0]
    content = getattr(head, "content", "")
    text = content if isinstance(content, str) else "".join(
        getattr(b, "text", "") for b in content
    )
    assert "HM_SYS_MARKER" in text
    # The assembled tail follows the system head.
    tail = " ".join(
        getattr(b, "text", "")
        for m in first_call[1:]
        for b in getattr(m, "content", []) or []
        if getattr(b, "type", None) == "text"
    )
    assert "assembled:" in tail


@pytest.mark.asyncio
async def test_as_runtime_reactive_compaction_sends_fresh_messages(
    tmp_path: Path,
) -> None:
    """H6: a context-length failure on first stream chunk triggers
    compaction and the retry must carry the *compacted* messages."""

    class FlakyModel(ScriptedModel):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.attempt_inputs: list[list[Msg]] = []

        async def _call_api(self, model_name, messages, tools=None, **kw):
            # ScriptedModel._call_api records into ``self.calls``; record the
            # raw attempt inputs separately so failures-before-super still
            # show up (and don't double-count successes).
            self.attempt_inputs.append(messages)
            if len(self.attempt_inputs) == 1:
                raise ValueError("context length exceeded: 200k > 128k")
            return await super()._call_api(
                model_name, messages, tools=tools, **kw
            )

    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    assembler = _FakeAssembler()
    model = FlakyModel([[TextBlock(text="recovered")]])
    runtime = AsAgentRuntime(
        model=model,
        system_prompt="sys",
        tools=[HomeToolAdapter(t, executor, scope) for t in registry.list_tools()],
        context_assembler=assembler,
    )
    session = AgentSession("as-runtime-test")
    assembler.shrink = False
    # Compact on the retry: flip after the first prepare ran.
    original_aprepare = assembler.aprepare

    async def _aprepare(**kw):
        prepared = await original_aprepare(**kw)
        if len(assembler.calls) == 1:
            assembler.shrink = True
        return prepared

    assembler.aprepare = _aprepare  # type: ignore[method-assign]

    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert result.status == "replied", result.events
    assert result.final_reply == "recovered"
    assert len(model.attempt_inputs) == 2
    assert len(assembler.calls) == 2
    assert assembler.calls[1]["force_compact"] == "aggressive"
    retry = model.attempt_inputs[1]
    retry_text = json.dumps(
        [m.model_dump(mode="json") for m in retry], default=str
    )
    assert "assembled:short" in retry_text
    assert "filler-filler-filler" not in retry_text
    event_types = [e.type for e in result.events]
    assert "runtime.reactive_compact_started" in event_types
    assert "context.compaction" in event_types


@pytest.mark.asyncio
async def test_as_runtime_denied_result_carries_hm_data(tmp_path: Path) -> None:
    """M7/H2: a denied tool call must persist an hm pocket so the canonical
    ToolResultMessage.data keeps backend_attempted/status instead of None —
    and the run must not crash evaluating it."""
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
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert len(tool_msgs) == 1
    data = tool_msgs[0].data or {}
    assert data.get("backend_attempted") is False
    assert data.get("status") == "denied"
    # The result reached the model on the wire (denial text visible).
    assert model.seen_tool_result_text


@pytest.mark.asyncio
async def test_as_runtime_jsonl_attempt_sink(tmp_path: Path) -> None:
    """H3: the ProviderObservabilityMiddleware must emit real
    ProviderAttemptRecords — a JsonlProviderAttemptSink must serialize them
    without crashing and record response_completed."""
    from homemaster.providers.attempts import JsonlProviderAttemptSink

    sink_path = tmp_path / "attempts.jsonl"

    def _factory() -> JsonlProviderAttemptSink:
        return JsonlProviderAttemptSink(sink_path)

    registry = ToolRegistry()
    registry.register(_echo_tool())
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    model = ScriptedModel(
        [
            [ToolCallBlock(id="tc1", name="echo", input='{"x": 1}')],
            [TextBlock(text="done")],
        ]
    )
    runtime = AsAgentRuntime(
        model=model,
        system_prompt="sys",
        tools=[HomeToolAdapter(t, executor, scope) for t in registry.list_tools()],
        provider_attempt_sink_factory=_factory,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session, "say hi", settings=_settings(tmp_path)
    )
    assert result.status == "replied", result.events

    lines = sink_path.read_text().strip().splitlines()
    assert len(lines) >= 2  # one record per model call
    records = [json.loads(line) for line in lines]
    assert all(r["model_attempt_id"] for r in records)
    assert all(r["request_sha256"] for r in records)
    assert all(r["response_completed"] is True for r in records)
    assert all(r["error_type"] is None for r in records)


@pytest.mark.asyncio
async def test_as_runtime_sigint_during_tool_execution_closes_dangling(
    tmp_path: Path,
) -> None:
    """H7: a cancel landing inside tool execution must not leave an ALLOWED
    ToolCallBlock without a paired ToolResultBlock — providers reject
    unpaired tool_use on resume."""
    import asyncio
    import os
    import signal

    started = asyncio.Event()

    async def _hang(arguments: Any, context: Any) -> ToolExecutionResult:
        started.set()
        await asyncio.sleep(30)
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="unreachable"
        )

    registry = ToolRegistry()
    registry.register(_echo_tool(execute=_hang))
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
        tmp_path,
        script=[
            [ToolCallBlock(id="tc-hang", name="echo", input='{"x": 1}')],
            [TextBlock(text="done")],
        ],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")

    async def _sigint() -> None:
        await started.wait()
        os.kill(os.getpid(), signal.SIGINT)

    task = asyncio.create_task(_sigint())
    result = await runtime.run(session, "go", settings=_settings(tmp_path))
    await task

    assert result.status == "cancelled"
    # No dangling tool calls in the canonical engine context.
    engine_context = list(result.engine_state.context)
    call_ids = set()
    result_ids = set()
    for msg in engine_context:
        for block in getattr(msg, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "tool_call":
                call_ids.add(block.id)
            elif btype == "tool_result":
                result_ids.add(block.id)
    assert call_ids == result_ids, (
        f"dangling tool calls: {call_ids - result_ids}"
    )
    # And the paired result is marked interrupted, not successful.
    from agentscope.message import ToolResultState

    results = [
        block
        for msg in engine_context
        for block in getattr(msg, "content", []) or []
        if getattr(block, "type", None) == "tool_result"
    ]
    assert any(
        b.state in (ToolResultState.INTERRUPTED, "interrupted")
        for b in results
    )


@pytest.mark.asyncio
async def test_as_runtime_observation_lands_in_engine_context(
    tmp_path: Path,
) -> None:
    """H4: automatic-observation evidence must reach the persisted
    ToolResultBlock in engine_state.context (canonical store), not just the
    transient ToolResponse the middleware saw."""
    import base64
    import hashlib

    from homemaster.tools.contracts import ResultImage

    png = _png_b64()
    sha = hashlib.sha256(base64.b64decode(png)).hexdigest()

    def _action(arguments: Any, context: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="moved",
            backend_attempted=True,
        )

    def _observe(arguments: Any, context: Any) -> ToolExecutionResult:
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

    # The canonical store carries the image + machine evidence.
    action_block = None
    for msg in result.engine_state.context:
        for block in getattr(msg, "content", []) or []:
            if (
                getattr(block, "type", None) == "tool_result"
                and getattr(block, "id", None) == "a1"
            ):
                action_block = block
    assert action_block is not None
    image_blocks = [
        b
        for b in (action_block.output or [])
        if getattr(b, "type", None) in {"image", "data"}
    ]
    assert image_blocks, "observation image missing from canonical result"
    hm = (action_block.metadata or {}).get("hm") or {}
    auto = (hm.get("data") or {}).get("automatic_observation") or {}
    assert auto.get("status") == "success"
    assert auto.get("source_tool_call_id") == "a1"
    assert auto.get("content_sha256") == sha

    # The model-facing wire also saw the image (next call's input).
    second_call = json.dumps(
        [m.model_dump(mode="json") for m in model.calls[1]], default=str
    )
    assert "base64" in second_call or "image" in second_call


class _StreamScriptedModel(ScriptedModel):
    """Streaming variant: ``_call_api`` returns a lazy async generator, so
    failures surface during consumption — exercising the middleware's
    stream-wrapping retry rather than the eager ``await`` path."""

    def __init__(
        self,
        script: list[list[Any]],
        *,
        fail_at: int | None = None,
        error: str = "context length exceeded: 200k > 128k",
    ) -> None:
        super().__init__(script)
        self._fail_at = fail_at  # 0 = before first chunk, 1 = mid-stream
        self._error = error
        self.attempts = 0

    async def _call_api(self, model_name, messages, tools=None, **kw):
        self.attempts += 1
        self.calls.append(messages)
        self.call_tools.append(tools)

        async def _gen():
            if self._fail_at == 0 and self.attempts == 1:
                raise ValueError(self._error)
            blocks = (
                self._script.pop(0)
                if self._script
                else [TextBlock(text="done")]
            )
            yield ChatResponse(
                content=blocks,
                is_last=False,
                usage=ChatUsage(input_tokens=7, output_tokens=1, time=0.0),
            )
            if self._fail_at == 1:
                raise ValueError(self._error)
            yield ChatResponse(
                content=[],
                is_last=True,
                usage=ChatUsage(input_tokens=7, output_tokens=2, time=0.0),
                metadata={"stop_reason": "end_turn"},
            )

        return _gen()


@pytest.mark.asyncio
async def test_as_runtime_streaming_pre_chunk_retry_uses_compacted(
    tmp_path: Path,
) -> None:
    """H6 (streaming): a context-length error raised lazily at the first
    ``__anext__`` triggers compaction and the retry carries fresh messages."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    assembler = _FakeAssembler()
    model = _StreamScriptedModel(
        [[TextBlock(text="recovered")]], fail_at=0
    )
    runtime = AsAgentRuntime(
        model=model,
        system_prompt="sys",
        tools=[HomeToolAdapter(t, executor, scope) for t in registry.list_tools()],
        context_assembler=assembler,
    )
    session = AgentSession("as-runtime-test")
    assembler.shrink = False
    original_aprepare = assembler.aprepare

    async def _aprepare(**kw):
        prepared = await original_aprepare(**kw)
        if len(assembler.calls) == 1:
            assembler.shrink = True
        return prepared

    assembler.aprepare = _aprepare  # type: ignore[method-assign]

    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert result.status == "replied", result.events
    assert model.attempts == 2
    assert assembler.calls[1]["force_compact"] == "aggressive"
    retry_text = json.dumps(
        [m.model_dump(mode="json") for m in model.calls[-1]], default=str
    )
    assert "assembled:short" in retry_text


@pytest.mark.asyncio
async def test_as_runtime_streaming_mid_stream_failure_no_retry(
    tmp_path: Path,
) -> None:
    """A context-length error after the first yielded chunk must NOT retry —
    deltas already delivered to the consumer cannot be re-emitted safely."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    assembler = _FakeAssembler()
    model = _StreamScriptedModel(
        [[TextBlock(text="partial")]], fail_at=1
    )
    runtime = AsAgentRuntime(
        model=model,
        system_prompt="sys",
        tools=[HomeToolAdapter(t, executor, scope) for t in registry.list_tools()],
        context_assembler=assembler,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert model.attempts == 1, "mid-stream failure must not retry"
    assert result.status == "failed", (
        result.status,
        [(e.type, e.payload) for e in result.events],
    )


@pytest.mark.asyncio
async def test_as_runtime_native_context_transforms_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: the Agent must be built with native compression / image trimming /
    runtime-state injection off — HM's assembler is the sole authority."""
    import agentscope.agent as as_agent

    captured: dict[str, Any] = {}
    real_agent = as_agent.Agent

    class _SpyAgent(real_agent):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(as_agent, "Agent", _SpyAgent)

    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    runtime = AsAgentRuntime(
        model=ScriptedModel([[TextBlock(text="ok")]]),
        system_prompt="sys",
        tools=[HomeToolAdapter(t, executor, scope) for t in registry.list_tools()],
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert result.status == "replied"

    ctx_cfg = captured.get("context_config")
    inj_cfg = captured.get("injection_config")
    assert ctx_cfg is not None, "context_config must be pinned explicitly"
    assert inj_cfg is not None, "injection_config must be pinned explicitly"
    assert getattr(ctx_cfg, "compression_tool_enabled", True) is False
    assert getattr(inj_cfg, "inject_runtime_state", True) is False


def test_anthropic_formatter_empty_tool_result_fallback() -> None:
    """Vendored patch: an empty/error-only tool result must format to a
    non-empty Anthropic ``tool_result`` content list (Anthropic rejects an
    empty list or empty text), and the carrying message must be role=user."""
    from agentscope.formatter import AnthropicChatFormatter
    from agentscope.message import ToolResultBlock

    fmt = AnthropicChatFormatter()
    msg = Msg(
        name="tool",
        role="assistant",
        content=[
            ToolResultBlock(
                id="tc-empty",
                name="echo",
                output=[],
                state="error",
            )
        ],
    )
    payload = asyncio.run(fmt.format([msg]))
    assert payload and payload[0]["role"] == "user"
    contents = [
        b for b in payload[0]["content"] if b.get("type") == "tool_result"
    ]
    assert len(contents) == 1
    assert contents[0]["tool_use_id"] == "tc-empty"
    assert contents[0]["content"], "empty tool_result content must fall back"
    assert all(
        b.get("type") != "text" or b.get("text")
        for b in contents[0]["content"]
    )


@pytest.mark.asyncio
async def test_as_runtime_unavailable_tool_rejects_whole_batch(
    tmp_path: Path,
) -> None:
    """Legacy parity: a batch containing a call to a tool that was NOT
    offered must reject *every* call atomically — the valid companion call
    must not execute (no partial side effects)."""
    executed: list[str] = []

    def _echo_execute(arguments: Any, context: Any) -> ToolExecutionResult:
        executed.append("echo")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="ok"
        )

    registry = ToolRegistry()
    registry.register(_echo_tool(execute=_echo_execute))
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
        tmp_path,
        script=[
            [
                ToolCallBlock(id="tc-good", name="echo", input='{"x": 1}'),
                ToolCallBlock(
                    id="tc-bad", name="hallucinated_tool", input="{}"
                ),
            ],
            [TextBlock(text="done")],
        ],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session, "hi", settings=_settings(tmp_path), tool_registry=registry
    )

    assert result.status == "replied"
    assert executed == [], "companion call must not execute"
    tool_msgs = [m for m in session.messages if m.role == "tool"]
    assert len(tool_msgs) == 2
    by_id = {m.tool_call_id: m for m in tool_msgs}
    # The never-offered call resolves to AS's native ToolNotFoundError
    # (no tool object exists for the permission chain to evaluate).
    bad_text = "".join(
        b.text or "" for b in by_id["tc-bad"].content
    )
    assert "hallucinated_tool" in bad_text
    # The companion call is rejected by the protocol fence with the legacy
    # batch-contamination code in the model-facing payload.
    good = by_id["tc-good"]
    good_text = "".join(b.text or "" for b in good.content)
    assert "tool_batch_contains_unavailable_call" in good_text
    assert (good.data or {}).get("backend_attempted") is False
    assert any(
        e.type == "tool.protocol_rejected"
        and (e.payload or {}).get("error_code")
        == "tool_batch_contains_unavailable_call"
        for e in result.events
    )


@pytest.mark.asyncio
async def test_as_runtime_terminal_allowlist_rejects_batch(
    tmp_path: Path,
) -> None:
    """Legacy parity: with ``allowed_terminal_commands`` configured, a
    non-matching terminal call denies the whole batch and emits
    ``terminal.command_protocol_rejected``."""
    executed: list[str] = []

    def _term(arguments: Any, context: Any) -> ToolExecutionResult:
        executed.append(str(arguments.get("command")))
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="ran"
        )

    def _echo_execute(arguments: Any, context: Any) -> ToolExecutionResult:
        executed.append("echo")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="ok"
        )

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="terminal",
            description="Run a command.",
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
            },
            execute=_term,
        )
    )
    registry.register(_echo_tool(execute=_echo_execute))
    executor = ToolExecutor(registry)
    runtime, model = _runtime(
        tmp_path,
        script=[
            [
                ToolCallBlock(
                    id="tc-term",
                    name="terminal",
                    input='{"command": "rm -rf /"}',
                ),
                ToolCallBlock(id="tc-echo", name="echo", input='{"x": 1}'),
            ],
            [TextBlock(text="done")],
        ],
        registry=registry,
        executor=executor,
    )
    settings = _settings(tmp_path)
    settings.permissions = SimpleNamespace(
        allowed_terminal_commands=("ls", "pwd")
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session, "hi", settings=settings, tool_registry=registry
    )

    assert result.status == "replied"
    assert executed == []
    by_id = {m.tool_call_id: m for m in session.messages if m.role == "tool"}
    term_text = "".join(b.text or "" for b in by_id["tc-term"].content)
    echo_text = "".join(b.text or "" for b in by_id["tc-echo"].content)
    assert "terminal_command_not_allowed" in term_text
    assert "tool_batch_contains_disallowed_terminal_command" in echo_text
    for m in by_id.values():
        assert (m.data or {}).get("backend_attempted") is False
    assert any(
        e.type == "terminal.command_protocol_rejected"
        and (e.payload or {}).get("error_code")
        == "terminal_command_not_allowed"
        for e in result.events
    )


@pytest.mark.asyncio
async def test_as_runtime_manual_compaction_emits_event_and_callback(
    tmp_path: Path,
) -> None:
    """Review-H1: a compaction inside ``_assemble`` (manual/force_compact,
    threshold) must emit ``context.compaction`` and invoke ``on_compaction``
    — previously only the reactive-retry path did, so ``require_recall``
    re-arms were silently dropped on the AS path."""
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    runtime = AsAgentRuntime(
        model=ScriptedModel([[TextBlock(text="ok")]]),
        system_prompt="sys",
        tools=[
            HomeToolAdapter(t, executor, scope)
            for t in registry.list_tools()
        ],
        context_assembler=_FakeAssembler(),
    )
    seen_kinds: list[str] = []

    async def _on_compaction(metrics: Any) -> None:
        seen_kinds.append(getattr(metrics, "compaction_kind", ""))

    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session,
        "hi",
        settings=_settings(tmp_path),
        force_compact=True,
        on_compaction=_on_compaction,
    )
    assert result.status == "replied"
    assert seen_kinds == ["manual"]
    compaction_events = [
        e for e in result.events if e.type == "context.compaction"
    ]
    assert len(compaction_events) == 1
    assert compaction_events[0].payload["trigger"] == "manual"
    assert compaction_events[0].payload["kind"] == "manual"


@pytest.mark.asyncio
async def test_as_runtime_native_compress_context_fenced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review-H5: AgentScope's per-iteration ``compress_context`` must never
    reach ``_compress_context_impl`` — the HM assembler owns all context
    mutation. The fence is the middleware not calling next_handler."""
    from agentscope.agent._agent import Agent as _ASAgent

    impl_calls: list[None] = []
    original = _ASAgent._compress_context_impl

    async def _spy(self: Any, *args: Any, **kwargs: Any) -> None:
        impl_calls.append(None)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(_ASAgent, "_compress_context_impl", _spy)

    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    runtime, _model = _runtime(
        tmp_path,
        script=[[TextBlock(text="ok")]],
        registry=registry,
        executor=executor,
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(session, "hi", settings=_settings(tmp_path))
    assert result.status == "replied"
    assert impl_calls == []


@pytest.mark.asyncio
async def test_as_runtime_propagate_exceptions_surface_raw(
    tmp_path: Path,
) -> None:
    """Review-H3/C+: types listed in ``propagate_exceptions`` re-raise raw
    after cleanup instead of being classified as transport_error — the app
    layer relies on this for SessionGenerationError/recall-deadline parity."""
    class _Fence(RuntimeError):
        pass

    registry = ToolRegistry()
    registry.register(_echo_tool())
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)

    # ToolCallBlock carries a mutable state field — each run needs fresh
    # block instances or the second run sees them as already-dispatched.
    def _script() -> list[list[Any]]:
        return [
            [ToolCallBlock(id="tc1", name="echo", input='{"x": 1}')],
            [TextBlock(text="done")],
        ]

    # stop_condition evaluates each tool result — a tool-call round is
    # required for it to fire.
    def _bad_stop(_session: Any, _results: Any) -> Any:
        raise _Fence("stale generation")

    runtime = AsAgentRuntime(
        model=ScriptedModel(_script()),
        system_prompt="sys",
        tools=[
            HomeToolAdapter(t, executor, scope)
            for t in registry.list_tools()
        ],
        stop_condition=_bad_stop,
    )
    session = AgentSession("as-runtime-test")
    with pytest.raises(_Fence):
        await runtime.run(
            session,
            "hi",
            settings=_settings(tmp_path),
            propagate_exceptions=(_Fence,),
        )

    # A non-listed exception still classifies to transport_error.
    def _other_stop(_session: Any, _results: Any) -> Any:
        raise ValueError("unlisted")

    runtime2 = AsAgentRuntime(
        model=ScriptedModel(_script()),
        system_prompt="sys",
        tools=[
            HomeToolAdapter(t, executor, scope)
            for t in registry.list_tools()
        ],
        stop_condition=_other_stop,
    )
    session2 = AgentSession("as-runtime-test-2")
    result2 = await runtime2.run(
        session2,
        "hi",
        settings=_settings(tmp_path),
    )
    assert result2.status == "failed"
    assert result2.error_code == "transport_error"


@pytest.mark.asyncio
async def test_as_runtime_provider_attempt_context_binder_fires(
    tmp_path: Path,
) -> None:
    """Review-M4: provider_attempt_context_binder must be invoked with
    canonical ToolCall objects + frozen request messages BEFORE the calls
    dispatch — mindmemos_feedback's deps write-back was dead on AS."""
    order: list[str] = []
    bound: dict[str, Any] = {}

    def _binder(*, tool_calls, frozen_messages, deps):
        order.append("bind")
        bound["tool_calls"] = list(tool_calls)
        bound["frozen"] = list(frozen_messages)
        deps["memory_feedback_context_by_tool_call_id"] = {"tc1": {"k": 1}}

    def _exec(arguments: Any, context: Any) -> Any:
        order.append("exec")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS, text="echo:1"
        )

    registry = ToolRegistry()
    registry.register(_echo_tool(_exec))
    executor = ToolExecutor(registry)
    scope = _scope(tmp_path)
    scope.services = {
        "run_context": SimpleNamespace(
            deps={"provider_attempt_context_binder": _binder}
        )
    }
    model = ScriptedModel(
        [
            [ToolCallBlock(id="tc1", name="echo", input='{"x": 1}')],
            [TextBlock(text="done")],
        ]
    )
    runtime = AsAgentRuntime(
        model=model,
        system_prompt="sys",
        tools=[
            HomeToolAdapter(t, executor, scope)
            for t in registry.list_tools()
        ],
        context_assembler=_FakeAssembler(),
    )
    session = AgentSession("as-runtime-test")
    result = await runtime.run(
        session, "hi", settings=_settings(tmp_path), scope=scope
    )

    assert result.status == "replied"
    assert order[:1] == ["bind"] and "exec" in order
    assert order.index("bind") < order.index("exec")
    assert [c.name for c in bound["tool_calls"]] == ["echo"]
    assert bound["tool_calls"][0].id == "tc1"
    assert bound["tool_calls"][0].arguments == {"x": 1}
    assert bound["frozen"], "frozen request messages must reach the binder"
