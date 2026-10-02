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
