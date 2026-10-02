"""Physical-safety vertical slice through the AgentScope substrate.

An AgentScope ``Agent`` + ``Toolkit`` drives a HomeMaster physical tool
wrapped by ``HomeToolAdapter`` against the real device subprocess. The
HomeMaster permission chain (prepare -> approval -> revalidate -> lease ->
execute -> observe -> release) must remain authoritative; assertions are
black-box against the subprocess state, never the adapter's own claims.
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeClock
from pydantic import BaseModel
from test_blackbox import DecisionHandler, DeviceProcess, HomeDeviceAdapter

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.message import Msg, TextBlock, ToolCallBlock, ToolResultBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.permissions.config import PermissionMode
from homemaster.permissions.store import PermissionStore
from homemaster.substrate import HomeToolAdapter, RunScope, RunScopeMiddleware
from homemaster.tools.base import FunctionTool, ToolRegistry
from homemaster.tools.contracts import PermissionSubject
from homemaster.tools.executor import ToolExecutor
from tests.homemaster.tools.test_support import ToolExecutionContext  # noqa: F401


def _boom(arguments: Any, context: Any) -> Any:
    raise AssertionError("physical tools must run through the adapter")


class ScriptedModel(ChatModelBase):
    """Canned chat model: pops one block-list per call, then says 'done'."""

    def __init__(self, script: list[list[Any]]) -> None:
        super().__init__(
            credential=OpenAICredential(api_key="sk-stub"),
            model="stub-model",
            parameters=BaseModel(),
        )
        self._script = list(script)
        self.calls: list[list[Msg]] = []

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls.append(messages)
        blocks = self._script.pop(0) if self._script else [TextBlock(text="done")]
        return ChatResponse(content=blocks, is_last=True)


class Rig:
    """Real device subprocess + real store/checker/executor under AS agent."""

    def __init__(self, tmp_path, *, settings: PermissionSettingsConfig | None = None,
                 rules: dict[tuple[str, str], str] | None = None) -> None:
        self.device = DeviceProcess()
        self.adapter = HomeDeviceAdapter(self.device)
        tool = FunctionTool(
            name="home_device", description="Household device.",
            input_schema={"type": "object"}, execute=_boom,
            physical=True, physical_adapter=self.adapter,
        )
        registry = ToolRegistry()
        registry.register(tool)
        self.clock = FakeClock()
        self.store = PermissionStore.open(
            tmp_path / "spike.sqlite3", clock=self.clock
        )
        self.checker = PermissionChecker(
            settings or PermissionSettingsConfig(),
            store=self.store, clock=self.clock,
        )
        self.handler = DecisionHandler(self.store)
        for (action, resource), choice in (rules or {}).items():
            self.handler.allow(action, resource, choice)
        self.executor = ToolExecutor(
            registry, permission_checker=self.checker,
            confirmation_handler=self.handler, permission_store=self.store,
        )
        self.scope = RunScope(
            session_id="bb-session", run_id="bb-run",
            permission_subject=PermissionSubject(
                subject_id="test", channel="pytest"
            ),
            working_directory=tmp_path,
        )
        self.as_tool = HomeToolAdapter(tool, self.executor, self.scope)

    def agent(self, script: list[list[Any]]) -> Agent:
        return Agent(
            name="hm", system_prompt="test agent",
            model=ScriptedModel(script),
            toolkit=Toolkit(tools=[self.as_tool]),
            middlewares=[RunScopeMiddleware()],
        )

    def world(self) -> dict[str, Any]:
        return self.device.read()

    def result_block(self, agent: Agent) -> ToolResultBlock:
        results = [
            block
            for msg in agent.state.context
            for block in msg.content
            if isinstance(block, ToolResultBlock)
        ]
        assert len(results) == 1, agent.state.context
        return results[0]

    def close(self) -> None:
        self.store.close()
        stderr = self.device.close()
        assert stderr == b"", f"device stderr not clean: {stderr!r}"


def _take_script(obj: str = "cup-a") -> list[list[Any]]:
    return [
        [ToolCallBlock(
            id="tc-1", name="home_device",
            input='{"op": "take", "object": "%s"}' % obj,
        )],
        [TextBlock(text="done")],
    ]


@pytest.mark.asyncio
async def test_approved_physical_call_reaches_device(tmp_path) -> None:
    rig = Rig(tmp_path, rules={("pick_up", "cup-a"): "allow_once"})
    try:
        agent = rig.agent(_take_script())
        await agent.reply(
            Msg(name="u", role="user", content=[TextBlock(text="拿杯子")])
        )
        # Black-box: the subprocess world actually changed.
        assert rig.world()["objects"]["cup-a"]["held"] is True
        assert rig.world()["ops"]["pick_up"] == 1
        result = rig.result_block(agent)
        assert result.state == "success", result
        hm = result.metadata["hm"]
        assert hm["status"] == "success"
        assert hm["data"]["backend_attempted"] is True
        # release ran on the terminal path.
        assert rig.adapter.released, "adapter.release must run"
        # Exactly one assistant Msg carries the call+result pair.
        assert len(rig.handler.calls) == 1  # one approval round
    finally:
        rig.close()


@pytest.mark.asyncio
async def test_denied_tool_never_reaches_device(tmp_path) -> None:
    rig = Rig(
        tmp_path,
        settings=PermissionSettingsConfig(
            mode=PermissionMode.FULL_AUTO, denied_tools=("home_device",)
        ),
    )
    try:
        agent = rig.agent(_take_script())
        await agent.reply(
            Msg(name="u", role="user", content=[TextBlock(text="拿杯子")])
        )
        assert rig.world()["objects"]["cup-a"]["held"] is False
        assert rig.world()["ops"]["pick_up"] == 0
        result = rig.result_block(agent)
        assert result.state == "denied", result
        assert rig.adapter.exec_attempts == {}
    finally:
        rig.close()


@pytest.mark.asyncio
async def test_approval_cancelled_blocks_device(tmp_path) -> None:
    # No rule -> DecisionHandler raises ApprovalCancelled.
    rig = Rig(tmp_path)
    try:
        agent = rig.agent(_take_script())
        await agent.reply(
            Msg(name="u", role="user", content=[TextBlock(text="拿杯子")])
        )
        assert rig.world()["objects"]["cup-a"]["held"] is False
        assert rig.world()["ops"]["pick_up"] == 0
        result = rig.result_block(agent)
        hm = result.metadata["hm"]
        assert hm["status"] == "permission_denied", hm
        assert rig.adapter.exec_attempts == {}
    finally:
        rig.close()


@pytest.mark.asyncio
async def test_backend_error_after_start_is_outcome_unknown(tmp_path) -> None:
    rig = Rig(tmp_path, rules={("pick_up", "cup-a"): "allow_once"})
    try:
        def _raise(binding_ref: str) -> None:
            raise RuntimeError("backend exploded after lease")

        rig.adapter.on_execute = _raise
        agent = rig.agent(_take_script())
        await agent.reply(
            Msg(name="u", role="user", content=[TextBlock(text="拿杯子")])
        )
        result = rig.result_block(agent)
        hm = result.metadata["hm"]
        assert hm["status"] == "outcome_unknown", hm
        assert hm["backend_attempted"] is True
        assert hm["outcome_certainty"] == "unknown"
        assert rig.adapter.released, "release must run on failure path"
    finally:
        rig.close()


@pytest.mark.asyncio
async def test_navigated_take_runs_two_steps(tmp_path) -> None:
    # cup-b lives in the bedroom; robot starts in living -> move + pick_up.
    rig = Rig(tmp_path, rules={("pick_up", "cup-b"): "allow_once"})
    try:
        agent = rig.agent(_take_script("cup-b"))
        await agent.reply(
            Msg(name="u", role="user", content=[TextBlock(text="拿蓝杯子")])
        )
        world = rig.world()
        assert world["objects"]["cup-b"]["held"] is True
        assert world["area"] == "bedroom"
        assert world["ops"]["move"] == 1
        assert world["ops"]["pick_up"] == 1
        # Both steps rode one approval item; one confirmation call.
        assert len(rig.handler.calls) == 1
    finally:
        rig.close()
