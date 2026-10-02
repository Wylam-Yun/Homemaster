"""Phase-1 runtime gate: ``AgentRuntime`` driven by ``AsLLMClient``.

The god-loop is unchanged; only the provider seam is swapped for the
AgentScope ``ChatModelBase``-backed client. Asserts the same iteration /
tool-dispatch / event / snapshot semantics as the legacy transport.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.model._model_usage import ChatUsage

from homemaster.agent.generic_runtime import AgentRuntime
from homemaster.agent.session import AgentSession
from homemaster.config.config import ProviderProfileConfig
from homemaster.substrate.as_llm_client import AsLLMClient
from homemaster.tools.base import FunctionTool, ToolRegistry
from homemaster.tools.contracts import ToolExecutionResult, ToolExecutionStatus
from homemaster.tools.executor import ToolExecutor


class _ScriptedAsModel:
    """Two-step script: echo tool call, then a text reply."""

    def __init__(self) -> None:
        self.calls = 0
        self.seen_tool_result_text = ""

    async def __call__(self, messages: list, tools: Any = None, **kw: Any) -> Any:
        self.calls += 1

        async def _gen():
            if self.calls == 1:
                yield ChatResponse(
                    content=[
                        ToolCallBlock(id="tc1", name="echo", input='{"x": 1}')
                    ],
                    is_last=True,
                    usage=ChatUsage(input_tokens=5, output_tokens=3, time=0.0),
                    metadata={"stop_reason": "tool_use"},
                )
            else:
                # The HM tool_result must have been converted into AS blocks.
                for msg in messages:
                    for block in msg.content:
                        data = getattr(block, "data", None) or {}
                        hm = data.get("hm") if isinstance(data, dict) else None
                        if isinstance(hm, dict) and hm.get("status"):
                            self.seen_tool_result_text += str(
                                getattr(block, "text", "") or ""
                            )
                yield ChatResponse(
                    content=[TextBlock(text="done", id="t")],
                    is_last=True,
                    usage=ChatUsage(input_tokens=9, output_tokens=2, time=0.0),
                    metadata={"stop_reason": "end_turn"},
                )

        return _gen()


def _profile() -> ProviderProfileConfig:
    return ProviderProfileConfig(
        name="p1",
        api_format="anthropic",
        transport="anthropic_sdk",
        base_url="https://api.example.com",
        model="claude-x",
        api_keys=("sk-a",),
        kind="chat",
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            name="echo",
            description="echo",
            input_schema={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
            },
            execute=lambda arguments, context: ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                text=f"echo:{arguments.get('x')}",
            ),
            read_only=True,
        )
    )
    return registry


@pytest.mark.asyncio
async def test_runtime_e2e_agentscope_engine() -> None:
    model = _ScriptedAsModel()
    client = AsLLMClient(
        _profile(), model_factory=lambda *a, **kw: model
    )
    session = AgentSession("as-engine-e2e")
    executor = ToolExecutor(_registry())
    result = await AgentRuntime(
        transport=client,
        tool_executor=executor,
        max_tool_iterations=4,
    ).run(
        session,
        "say hi",
        tool_registry=_registry(),
    )

    assert result.status == "replied"
    assert result.final_reply == "done"
    assert model.calls == 2
    # The HM tool result round-tripped through AS blocks back into the model.
    assert "echo:1" in model.seen_tool_result_text

    # Session contains user → assistant(tool_call) → tool_result → assistant.
    roles = [m.role for m in session.messages]
    assert roles[:3] == ["user", "assistant", "tool"]
    assistant_tool = session.messages[1]
    assert assistant_tool.tool_calls[0].name == "echo"
    assert assistant_tool.tool_calls[0].arguments == {"x": 1}
    assert session.messages[2].tool_call_id == "tc1"

    # Runtime events still fire through the unchanged shell.
    event_types = [e.type for e in result.events]
    assert "runtime.turn_started" in event_types
    assert "tool.call_started" in event_types
    assert "tool.call_completed" in event_types
    assert "runtime.turn_completed" in event_types
