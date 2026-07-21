from __future__ import annotations

from homemaster.agent.generic_runtime import AgentRuntime, GenericAgentRuntime
from homemaster.agent.session import AgentSession
from homemaster.providers.transports.types import TransportDelta
from homemaster.tools.catalog import ToolCatalog
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

    def stream(self, messages, *, tools=None, **kwargs):
        del messages, kwargs
        self.tools = tools
        yield TransportDelta(type="text", text_delta="done", finish_reason="stop")


class _Executor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS)


def test_agent_runtime_uses_explicit_frozen_tool_view() -> None:
    catalog = ToolCatalog()
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
    catalog.register(tool)
    view = catalog.freeze((tool.definition.internal_id,))
    transport = _Transport()
    runtime = AgentRuntime(transport=transport, tool_executor=lambda *_args: {})

    result = runtime.run(
        AgentSession("tool-view"),
        "hello",
        tools=[],
        tool_view=view,
    )

    assert result.final_reply == "done"
    assert transport.tools == list(view.manifests())


def test_generic_runtime_name_is_a_compatibility_alias() -> None:
    assert GenericAgentRuntime is AgentRuntime
