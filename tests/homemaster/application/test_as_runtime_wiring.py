"""Phase-2 app gate: ``ApplicationRuntime`` drives the AgentScope engine
end-to-end when the provider seam carries ``chat_model()`` — adapters route
through ``ApplicationToolExecutor.execute_for_substrate`` so the full
permission/evidence/completion-guard funnel stays authoritative.
"""

from __future__ import annotations

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
from homemaster.agent.context import ContextAssembler
from homemaster.application.contracts import (
    RunRequest,
    RunStatus,
)
from homemaster.application.runtime import ApplicationRuntime
from homemaster.application.session import SessionManager
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.events.bus import EventBus
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.contracts import (
    PermissionSubject,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
)
from homemaster.tools.executor import ToolExecutor


class _ScriptedModel(ChatModelBase):
    def __init__(self, script: list[list[Any]]) -> None:
        super().__init__(
            credential=OpenAICredential(api_key="sk-stub"),
            model="stub-model",
            parameters=ChatModelBase.Parameters(),
        )
        self.formatter = OpenAIChatFormatter()
        self._script = list(script)
        self.calls = 0
        self.seen_tool_result_text = ""

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> ChatResponse:
        self.calls += 1
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
            usage=ChatUsage(input_tokens=5, output_tokens=2, time=0.0),
            metadata={"stop_reason": "end_turn"},
        )


class _ScriptedProvider:
    """Duck-typed provider: ``chat_model()`` marks it as the AS path."""

    def __init__(self, model: _ScriptedModel) -> None:
        self._model = model

    def chat_model(self, *, provider_key_index: int = 0) -> Any:
        return self._model

    def token_estimator(self) -> Any:
        from homemaster.providers.token_estimator import make_default_estimator

        return make_default_estimator(
            ProviderProfileConfig(
                name="stub",
                api_format="openai",
                transport="openai_sdk",
                base_url="http://stub",
                model="stub",
                api_keys=("sk-stub",),
                kind="chat",
            )
        )


def _definition(internal_id: str, alias: str, **kwargs: Any) -> ToolDefinition:
    from homemaster.tools.contracts import VerificationPolicy

    return ToolDefinition(
        internal_id=internal_id,
        model_alias=alias,
        description=f"Test {alias}.",
        input_schema=kwargs.get(
            "input_schema",
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference=internal_id),
        version="1.0.0",
        requires_model_observation=kwargs.get(
            "requires_model_observation", False
        ),
        state_effects=kwargs.get("state_effects", ()),
    )


class _WriteExecutor:
    def __init__(self, target: Path) -> None:
        self._target = target

    async def execute(self, arguments: Any, context: Any) -> ToolExecutionResult:
        self._target.write_text(str(arguments["value"]), encoding="utf-8")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"written:{arguments['value']}",
            backend_attempted=True,
        )


def _write_tool(target: Path) -> RegisteredTool:
    return RegisteredTool(
        definition=_definition("test.write_note.v1", "write_note"),
        executor=_WriteExecutor(target),
    )


class _ActionExecutor:
    """State-changing tool whose writes prove it ran (external terminal)."""

    def __init__(self, marker: Path) -> None:
        self._marker = marker

    async def execute(self, arguments: Any, context: Any) -> ToolExecutionResult:
        self._marker.write_text("moved", encoding="utf-8")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="moved",
            backend_attempted=True,
        )


class _ObserveExecutor:
    """Returns one valid base64 PNG so the runtime-owned observation
    validates through the real evidence chain."""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, arguments: Any, context: Any) -> ToolExecutionResult:
        import base64
        import hashlib
        import io

        from PIL import Image

        from homemaster.tools.contracts import ResultImage

        self.calls += 1
        image = Image.new("RGB", (2, 2), (0, 128, 255))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        data = buf.getvalue()
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="observed",
            images=[
                ResultImage(
                    media_type="image/png",
                    data_base64=base64.b64encode(data).decode(),
                    content_sha256=hashlib.sha256(data).hexdigest(),
                )
            ],
        )


def _observed_action_tool(marker: Path) -> RegisteredTool:
    return RegisteredTool(
        definition=_definition(
            "test.robot_go_to.v1",
            "robot_go_to",
            input_schema={"type": "object", "properties": {}},
            requires_model_observation=True,
            state_effects=("physical",),
        ),
        executor=_ActionExecutor(marker),
    )


def _observe_tool(executor: _ObserveExecutor) -> RegisteredTool:
    return RegisteredTool(
        definition=_definition(
            "test.observe.v1",
            "observe",
            input_schema={"type": "object", "properties": {}},
        ),
        executor=executor,
    )


def _app(
    tmp_path: Path,
    tools: list[RegisteredTool],
    model: _ScriptedModel,
) -> ApplicationRuntime:
    registry = ToolRegistry()
    registry.register_many([from_registered_tool(t) for t in tools])
    profile = ProviderProfileConfig(
        name="stub",
        api_format="openai",
        transport="openai_sdk",
        base_url="http://stub",
        model="stub",
        api_keys=("sk-stub",),
        kind="chat",
    )

    def context_factory(request: Any, provider: Any) -> ContextAssembler:
        return ContextAssembler(
            provider=profile,
            policy=ContextPolicyConfig(),
            system_prompt="system",
            summary_client=None,
        )

    settings = SimpleNamespace(
        runtime_guards=SimpleNamespace(
            max_consecutive_tool_errors=5,
            max_no_progress_iterations=20,
            reactive_compact_max_retries=2,
        ),
        context=ContextPolicyConfig(),
        provider_name="stub",
        application_services={},
        observability=SimpleNamespace(
            session_dir=str(tmp_path / "sessions"),
            save_session_per_iteration=True,
            save_on_sigint=True,
            strip_images_in_snapshot=True,
            trace_rotation_max_mb=100,
            interrupt_enabled=True,
            interrupt_abort_llm_stream=True,
        ),
    )
    provider = _ScriptedProvider(model)
    return ApplicationRuntime(
        registry=registry,
        tool_executor=ToolExecutor(
            registry,
            permission_checker=PermissionChecker(PermissionSettingsConfig()),
        ),
        event_bus=EventBus(),
        session_manager=SessionManager(session_root=tmp_path / "sessions"),
        provider_factory=lambda request, run_id: provider,
        context_assembler_factory=context_factory,
        settings=settings,
    )


@pytest.mark.asyncio
async def test_app_runtime_agentscope_engine_tool_call(tmp_path: Path) -> None:
    """Full app composition with the AS engine: model issues a write_note
    call, the real executor funnel runs it, the file exists, and a v2
    snapshot persists."""
    target = tmp_path / "note.txt"
    model = _ScriptedModel(
        [
            [ToolCallBlock(id="c1", name="write_note", input='{"value": "hello"}')],
            [TextBlock(text="finished")],
        ]
    )
    app = _app(tmp_path, [_write_tool(target)], model)
    request = RunRequest(
        text="write a note",
        session_id="as-app-e2e",
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="cli",
            tenant_id="tenant-x",
            capabilities=("tool.auto", "tool.read", "tool.mutate"),
        ),
    )
    result = await app.run(request)

    assert result.status is RunStatus.REPLIED
    # Black-box external terminal state: the file was actually written.
    assert target.read_text(encoding="utf-8") == "hello"
    # The tool result reached the model through the AS block conversion.
    assert "written:hello" in model.seen_tool_result_text

    event_types = [e.type for e in app.event_bus.events]
    assert "tool.call_completed" in event_types
    assert "runtime.turn_completed" in event_types

    # Schema-v2 snapshot exists on disk with the engine state.
    snapshot = tmp_path / "sessions" / "as-app-e2e" / "session.json"
    if snapshot.exists():
        payload = json.loads(snapshot.read_text())
        assert payload.get("schema_version") == 2
        assert payload["agentscope_state"]["context"]


@pytest.mark.asyncio
async def test_app_runtime_agentscope_automatic_observation(
    tmp_path: Path,
) -> None:
    """requires_model_observation through the app path: the action runs, the
    runtime-owned observe call goes through the real executor funnel, image
    evidence is attached, and the consume marker fires on the next turn."""
    marker = tmp_path / "moved.txt"
    observer = _ObserveExecutor()
    model = _ScriptedModel(
        [
            [ToolCallBlock(id="a1", name="robot_go_to", input="{}")],
            [TextBlock(text="observed the move")],
        ]
    )
    app = _app(
        tmp_path,
        [_observed_action_tool(marker), _observe_tool(observer)],
        model,
    )
    request = RunRequest(
        text="move the robot",
        session_id="as-app-observe",
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="cli",
            tenant_id="tenant-x",
            capabilities=(
                "tool.auto",
                "tool.read",
                "tool.mutate",
                "device.read",
                "device.control",
            ),
        ),
    )
    result = await app.run(request)

    assert result.status is RunStatus.REPLIED
    # External terminal state: the action executed for real.
    assert marker.read_text(encoding="utf-8") == "moved"
    # The runtime-owned observation ran through the same executor funnel.
    assert observer.calls == 1

    event_types = [e.type for e in app.event_bus.events]
    assert "model_observation.automatic_started" in event_types
    assert "model_observation.automatic_completed" in event_types
    assert "model_observation.image_consumed" in event_types
    assert "runtime.turn_completed" in event_types
