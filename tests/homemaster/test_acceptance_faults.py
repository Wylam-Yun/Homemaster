"""Regression tests for the A5 fault-injection findings.

Covers the defects found by injection testing that have since been fixed:

- malformed provider ``ToolCallBlock.input`` must end the run as a typed
  failure instead of escaping ``run()`` raw and stranding the session;
- unknown tools and schema-invalid arguments must carry the canonical HM
  ``error_code`` values in the projected tool result data;
- type-coercing "repairs" of already well-formed arguments must not
  silently rewrite what the backend receives;
- config loading must fail typed for unreadable paths and name available
  providers for unknown ones.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import ToolCall, ToolResultMessage
from homemaster.application.runtime import ApplicationRuntime
from homemaster.application.session import SessionManager
from homemaster.config import (
    ConfigError,
    ContextPolicyConfig,
    HomeMasterConfig,
    ProviderProfileConfig,
    load_config,
)
from homemaster.events.bus import EventBus
from homemaster.providers.types import TransportDelta
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
from homemaster.tools.executor import ToolExecutor
from tests.homemaster.as_testkit import as_provider


class _ScriptTransport:
    """Pops one scripted delta list per call; records calls for asserts."""

    def __init__(self, scripts: list[list[TransportDelta]]) -> None:
        self._scripts = list(scripts)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    async def stream(self, messages, *, tools=None, **kwargs):
        del kwargs
        with self._lock:
            script = self._scripts.pop(0)
            self.calls.append({"messages": messages, "tools": tools})
        for delta in script:
            yield delta


def _text(text: str) -> list[TransportDelta]:
    return [TransportDelta(type="text", text_delta=text, finish_reason="stop")]


def _call(call_id: str, name: str, arguments: dict[str, Any]) -> list[TransportDelta]:
    return [
        TransportDelta(
            type="tool_call",
            tool_call_delta=ToolCall(id=call_id, name=name, arguments=arguments),
            finish_reason="tool_calls",
        )
    ]


class _Echo:
    async def execute(self, arguments: Any, context: Any) -> ToolExecutionResult:
        del context
        value = arguments.get("value") if isinstance(arguments, dict) else None
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"value": value},
        )


def _echo_tool() -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id="test.echo.v1",
            model_alias="echo",
            description="Echo the value.",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source="test", reference="test.echo.v1"),
            version="1.9.0",
        ),
        executor=_Echo(),
    )


def _application(
    tmp_path: Path,
    tools: list[RegisteredTool],
    transports: dict[str, Any],
) -> ApplicationRuntime:
    registry = ToolRegistry()
    registry.register_many([from_registered_tool(tool) for tool in tools])
    provider_profile = ProviderProfileConfig(
        name="fake",
        api_format="anthropic",
        base_url="https://example.invalid",
        model="fake",
        api_keys=["not-a-real-key"],
        context_window_tokens=100_000,
        max_output_tokens=None,
    )

    def context_factory(request, provider):
        del request
        return ContextAssembler(
            provider=provider_profile,
            policy=ContextPolicyConfig(),
            system_prompt="system",
            summary_client=provider,
        )

    settings = SimpleNamespace(
        runtime_guards=SimpleNamespace(
            max_consecutive_tool_errors=5,
            max_no_progress_iterations=20,
            reactive_compact_max_retries=2,
        ),
        context=ContextPolicyConfig(),
        provider_name="fake",
        application_services={},
    )

    def provider_factory(request, run_id):
        del run_id
        return as_provider(transports[request.text])

    return ApplicationRuntime(
        registry=registry,
        session_manager=SessionManager(session_root=tmp_path / "sessions"),
        event_bus=EventBus(),
        tool_executor=ToolExecutor(registry),
        provider_factory=provider_factory,
        context_assembler_factory=context_factory,
        settings=settings,
    )


def _tool_results(app: ApplicationRuntime, session_id: str) -> list[ToolResultMessage]:
    runtime = app.session_manager.get(session_id)
    return [
        message
        for message in runtime.session.messages
        if isinstance(message, ToolResultMessage)
    ]


class _DirectProvider:
    """Provider seam exposing a fixed ChatModelBase built from a responder."""

    api_format = "openai"

    def __init__(self, model: Any) -> None:
        self._model = model

    def chat_model(self, **_kwargs: Any) -> Any:
        return self._model

    async def aclose(self) -> None:
        pass


def _model_class(responder):
    from agentscope.credential import OpenAICredential
    from agentscope.formatter import OpenAIChatFormatter
    from agentscope.model import ChatModelBase

    class _Model(ChatModelBase):
        def __init__(self) -> None:
            super().__init__(
                credential=OpenAICredential(api_key="sk-stub"),
                model="stub-model",
                parameters=ChatModelBase.Parameters(),
            )
            self.formatter = OpenAIChatFormatter()

        async def _call_api(self, model_name, messages, tools=None, **kwargs):
            del model_name, kwargs
            return responder(messages, tools)

    return _Model()


@pytest.mark.asyncio
async def test_malformed_tool_call_input_degrades_gracefully(tmp_path: Path):
    """Provider emits a ToolCallBlock whose ``input`` is not valid JSON.

    The substrate rejects the arguments before dispatch (typed
    ``invalid_tool_arguments``, backend never attempted) and the session
    mirror sanitizes the unrepresentable call to empty arguments instead of
    crashing the whole run — the model sees the error and recovers on the
    next turn.
    """
    from agentscope.message import TextBlock, ToolCallBlock
    from agentscope.model import ChatResponse
    from agentscope.model._model_usage import ChatUsage
    from homemaster.application.runtime import RunRequest

    calls = {"count": 0}

    def responder(messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return ChatResponse(
                content=[
                    ToolCallBlock(id="call-bad", name="echo", input="not-json{{{"),
                ],
                is_last=True,
                usage=ChatUsage(input_tokens=1, output_tokens=1, time=0.0),
                metadata={"stop_reason": "tool_use"},
            )
        return ChatResponse(
            content=[TextBlock(text="recovered")],
            is_last=True,
            usage=ChatUsage(input_tokens=1, output_tokens=1, time=0.0),
            metadata={"stop_reason": "end_turn"},
        )

    provider = _DirectProvider(_model_class(responder))
    app = _application(tmp_path, [_echo_tool()], {"x": None})
    app.provider_factory = lambda request, run_id: provider

    result = await app.run(RunRequest(text="x", session_id="s-badjson"))
    assert result.status.value == "replied"
    results = _tool_results(app, "s-badjson")
    assert results, "the rejected tool call must still land in history"
    result_data = results[0].data
    assert result_data.get("backend_attempted") is False
    assert result_data.get("error_code") == "invalid_tool_arguments"
    runtime = app.session_manager.get("s-badjson")
    assert runtime.active_task is None
    assert app.status("s-badjson").status != "running"
    await app.aclose()


@pytest.mark.asyncio
async def test_unconvertible_response_block_becomes_placeholder(tmp_path: Path):
    """A provider block the mirror cannot represent (non-image DataBlock)
    must not crash the run — the canonical mirror records a placeholder
    while the raw block stays in engine context."""
    from agentscope.message import Base64Source, DataBlock, TextBlock
    from agentscope.model import ChatResponse
    from agentscope.model._model_usage import ChatUsage
    from homemaster.agent.messages import AssistantMessage
    from homemaster.application.runtime import RunRequest

    def responder(messages, tools):
        return ChatResponse(
            content=[
                DataBlock(
                    source=Base64Source(data="AAAA", media_type="video/mp4"),
                ),
                TextBlock(text="with attachment"),
            ],
            is_last=True,
            usage=ChatUsage(input_tokens=1, output_tokens=1, time=0.0),
            metadata={"stop_reason": "end_turn"},
        )

    provider = _DirectProvider(_model_class(responder))
    app = _application(tmp_path, [_echo_tool()], {"x": None})
    app.provider_factory = lambda request, run_id: provider

    result = await app.run(RunRequest(text="x", session_id="s-badblock"))
    assert result.status.value == "replied"
    runtime = app.session_manager.get("s-badblock")
    assistant = [
        m for m in runtime.session.messages if isinstance(m, AssistantMessage)
    ]
    texts = [
        b.text
        for m in assistant
        for b in m.content
        if getattr(b, "type", None) == "text"
    ]
    assert any("unconvertible provider content omitted" in t for t in texts)
    assert any("with attachment" in t for t in texts)
    await app.aclose()


@pytest.mark.asyncio
async def test_unknown_tool_carries_canonical_error_code(tmp_path: Path) -> None:
    from homemaster.application.runtime import RunRequest

    transport = _ScriptTransport(
        [_call("call-1", "no_such_tool", {"value": "x"}), _text("handled")]
    )
    app = _application(tmp_path, [_echo_tool()], {"go": transport})
    result = await app.run(RunRequest(text="go", session_id="s-unk"))
    assert result.status.value == "replied"
    result_data = _tool_results(app, "s-unk")[0].data
    assert result_data.get("backend_attempted") is False
    assert result_data.get("error_code") == "unknown_tool"
    await app.aclose()


@pytest.mark.asyncio
async def test_invalid_arguments_carry_canonical_error_code(tmp_path: Path) -> None:
    from homemaster.application.runtime import RunRequest

    transport = _ScriptTransport(
        [_call("call-1", "echo", {"value": [1, 2, 3]}), _text("handled")]
    )
    app = _application(tmp_path, [_echo_tool()], {"go": transport})
    result = await app.run(RunRequest(text="go", session_id="s-invargs"))
    assert result.status.value == "replied"
    result_data = _tool_results(app, "s-invargs")[0].data
    assert result_data.get("backend_attempted") is False
    assert result_data.get("error_code") == "invalid_tool_arguments"
    await app.aclose()


@pytest.mark.asyncio
async def test_well_formed_arguments_are_not_silently_retyped(tmp_path: Path) -> None:
    """An int sent for a string field must reach validation unchanged —
    the schema check then reports the real mismatch instead of the backend
    executing with a coerced value."""
    from homemaster.application.runtime import RunRequest

    transport = _ScriptTransport(
        [_call("call-1", "echo", {"value": 12345}), _text("done")]
    )
    app = _application(tmp_path, [_echo_tool()], {"go": transport})
    result = await app.run(RunRequest(text="go", session_id="s-coerce"))
    assert result.status.value == "replied"
    result_data = _tool_results(app, "s-coerce")[0].data
    # The backend must not have run with a coerced "12345".
    assert result_data.get("backend_attempted") is False
    assert result_data.get("error_code") == "invalid_tool_arguments"
    await app.aclose()


def test_load_config_rejects_directory_path_typed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read HomeMaster config"):
        load_config(tmp_path)


def test_unknown_provider_error_lists_available_providers() -> None:
    config = HomeMasterConfig.model_validate(
        {
            "providers": {
                "default": "Alpha",
                "items": [
                    {
                        "name": "Alpha",
                        "kind": "chat",
                        "api_format": "anthropic",
                        "model": "a",
                        "base_url": "https://example.invalid",
                        "api_keys": ["k"],
                    }
                ],
            }
        }
    )
    with pytest.raises(ConfigError, match="available: Alpha"):
        config.get_provider("NoSuchProvider")
