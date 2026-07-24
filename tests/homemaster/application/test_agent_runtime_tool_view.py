from __future__ import annotations

import asyncio

from homemaster.agent.generic_runtime import AgentRuntime
from homemaster.agent.session import AgentSession
from homemaster.providers.transports.types import TransportDelta
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.contracts import (
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)


class _Transport:
    def __init__(self) -> None:
        self.tools = None

    async def stream(self, messages, *, tools=None, **kwargs):
        del messages, kwargs
        self.tools = tools
        yield TransportDelta(type="text", text_delta="done", finish_reason="stop")


class _Executor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS)


def test_agent_runtime_uses_explicit_universal_registry() -> None:
    tool = RegisteredTool(
        definition=ToolDefinition(
            internal_id="test.explicit.v1",
            model_alias="explicit",
            description="Explicit tool.",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source="test", reference="agent-runtime"),
            version="1.9.0",
        ),
        executor=_Executor(),
    )
    registry = ToolRegistry()
    registry.register(from_registered_tool(tool))
    transport = _Transport()
    runtime = AgentRuntime(transport=transport, tool_executor=lambda *_args: {})

    result = asyncio.run(
        runtime.run(
            AgentSession("tool-registry"),
            "hello",
            tool_registry=registry,
        )
    )

    assert result.final_reply == "done"
    assert transport.tools == registry.to_api_schema()
