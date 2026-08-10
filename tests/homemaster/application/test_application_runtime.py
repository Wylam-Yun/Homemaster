from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
import io
import json
import re
import threading
import time
import weakref
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from homemaster.agent.context import ContextAssembler
from homemaster.agent.messages import ToolCall
from homemaster.application.contracts import ResourceBinding, RunPolicy, RunRequest, RunStatus
from homemaster.application.resources import ResourceCleanupError
from homemaster.application.runtime import ApplicationRuntime
from homemaster.application.session import SessionManager
from homemaster.artifacts import ArtifactPublisher, ToolOutputStore
from homemaster.config import ContextPolicyConfig, ProviderProfileConfig
from homemaster.devices import DeviceConnectionPool, DeviceLeaseError, DeviceLeaseManager
from homemaster.events.bus import EventBus
from homemaster.extensions import (
    ExtensionContributions,
    ExtensionGeneration,
    ExtensionManifest,
    HookContext,
    HookEvent,
    HookRunner,
    HookSpec,
    LoadedExtension,
)
from homemaster.memory.evidence import MemoryEvidenceLedger
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.memory.models import FactRecord
from homemaster.providers.attempts import (
    ProviderAttemptRecord,
)
from homemaster.providers.transports.types import TransportDelta
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.tools import make_task_progress_check_tool
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.contracts import (
    ExecutionProof,
    PermissionSubject,
    RegisteredTool,
    TerminalRule,
    ToolDefinition,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.executor import ToolExecutor
from homemaster.tools.legacy_adapter import adapt_legacy_tool_spec
from homemaster.tools.memory_tools import build_memory_tools
from homemaster.tools.observe import ScreenshotTool


class _FakeTransport:
    def __init__(self, responses: list[list[TransportDelta]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    async def stream(
        self,
        messages,
        *,
        tools=None,
        attempt_sink=None,
        model_attempt_id="attempt",
        **kwargs,
    ):
        del kwargs
        with self._lock:
            response = self._responses.pop(0)
            self.calls.append({"messages": messages, "tools": tools})
        if attempt_sink is not None:
            attempt_sink.record_attempt(
                ProviderAttemptRecord(
                    model_attempt_id=model_attempt_id,
                    request_sha256=hashlib.sha256(repr((messages, tools)).encode()).hexdigest(),
                    outbound_images=(),
                    stripped_images=False,
                    response_completed=True,
                    error_type=None,
                    cause_code=None,
                )
            )
        for delta in response:
            yield delta


class _BlockingTransport(_FakeTransport):
    def __init__(self) -> None:
        super().__init__([_text("late response")])
        self.entered = threading.Event()
        self.release = threading.Event()

    async def stream(self, *args, **kwargs):
        messages = args[0]
        tools = kwargs.get("tools")
        with self._lock:
            response = self._responses.pop(0)
            self.calls.append({"messages": messages, "tools": tools})
        self.entered.set()
        await asyncio.to_thread(self.release.wait, 5)
        for delta in response:
            yield delta


class _AsyncBlockingTransport:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def stream(self, *_args, **_kwargs):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        for delta in _text("released"):
            yield delta


class _CancellationSwallowingTransport:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def stream(self, *_args, **_kwargs):
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        for delta in _text("late response"):
            yield delta


class _ClosableTransport(_FakeTransport):
    def __init__(self, responses: list[list[TransportDelta]]) -> None:
        super().__init__(responses)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _FailingCloseTransport(_FakeTransport):
    def close(self) -> None:
        raise RuntimeError("provider close failed")


class _MemoryEvidenceTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.evidence_ref: str | None = None

    async def stream(self, messages, *, tools=None, **kwargs):
        del kwargs
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            context_text = "\n".join(
                block.text for message in messages for block in message.content if block.text
            )
            match = re.search(r"memory-evidence-[0-9a-f]{32}", context_text)
            assert match is not None
            self.evidence_ref = match.group(0)
            for delta in _tool(
                "memory-add-1",
                "add_memory",
                {
                    "memory_type": "fact",
                    "record": {
                        "memory_type": "fact",
                        "subject": {"type": "object", "name": "钥匙"},
                        "predicate": "location",
                        "value": {"container": "玄关抽屉"},
                        "source": "user_statement",
                    },
                    "evidence_refs": [self.evidence_ref],
                },
            ):
                yield delta
            return
        for delta in _text("记住了"):
            yield delta


class _RecordingMindMemOS(EmbeddedMindMemOS):
    def __init__(self) -> None:
        self.calls: list[tuple[FactRecord, int]] = []
        self.records: dict[str, dict[str, object]] = {}

    async def add(self, messages, context, **kwargs):
        del context
        metadata = kwargs["metadata"]
        record = (
            FactRecord.model_validate(json.loads(metadata["record_json"]))
            if metadata["homemaster_memory_type"] == "fact"
            else SimpleNamespace(
                memory_type="procedure",
                name=json.loads(metadata["record_json"])["name"],
            )
        )
        provenance_seq = int(metadata["provenance_seq"])
        self.calls.append((record, provenance_seq))
        memory_id = f"memory-runtime-{len(self.calls)}"
        self.records[memory_id] = dict(metadata)
        return SimpleNamespace(
            status="ok",
            memories=[
                SimpleNamespace(
                    memory_id=memory_id,
                    related_memory_ids=[memory_id],
                    memory_type=record.memory_type,
                    content=messages[0].text,
                )
            ],
        )

    async def get_raw(self, memory_id, context):
        del context
        metadata = self.records.get(memory_id)
        if metadata is None:
            return None
        return SimpleNamespace(
            memory_id=memory_id,
            mem_type="experience" if metadata["homemaster_memory_type"] == "procedure" else "fact",
            metadata={
                "request_metadata": {
                    "add_record_ids": ["add-1"],
                    "record_metadata": [metadata],
                }
            },
            created_at=None,
            update_at=None,
            status="active",
        )


class _MemoryRecallStore(EmbeddedMindMemOS):
    def __init__(self) -> None:
        pass

    async def search(self, query, context, **kwargs):
        from mindmemos.typing import MemorySearchItem

        del context
        assert query == "钥匙在哪里"
        assert kwargs["filters"] == {"mem_type": "fact"}
        return SimpleNamespace(
            status="ok",
            memories=[
                MemorySearchItem(
                    id="memory-recall-1",
                    memory="钥匙 的 location 是玄关抽屉",
                    memory_type="fact",
                    last_update_at="2026-07-28 00:00:00",
                )
            ],
        )

    async def get_raw(self, memory_id, context):
        del context
        record = FactRecord(
            memory_type="fact",
            subject={"type": "object", "name": "钥匙"},
            predicate="location",
            value={"container": "玄关抽屉"},
            source="user_statement",
        )
        return SimpleNamespace(
            memory_id=memory_id,
            metadata={"request_metadata": {"record_json": record.model_dump_json()}},
            created_at=None,
            update_at=None,
            status="active",
        )


class _MemoryRecallTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def stream(self, messages, *, tools=None, **kwargs):
        del kwargs
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            for delta in _tool(
                "memory-search-1",
                "search_memories",
                {"query": "钥匙在哪里", "memory_type": "fact"},
            ):
                yield delta
            return
        tool_message = next(
            message
            for message in messages
            if message.role == "tool" and message.name == "search_memories"
        )
        payload = json.loads(tool_message.content[0].text)
        assert payload["records"][0]["memory_id"] == "memory-recall-1"
        assert payload["records"][0]["record"]["value"] == {"container": "玄关抽屉"}
        for delta in _text("钥匙在玄关抽屉"):
            yield delta


class _ObservationExecutor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments, context
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"observed": True},
            backend_attempted=True,
        )


class _ProcedureEvidenceTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def stream(self, messages, *, tools=None, **kwargs):
        del kwargs
        self.calls.append({"messages": messages, "tools": tools})
        if len(self.calls) == 1:
            for call_id in ("observation-step", "observation-success"):
                for delta in _tool(call_id, "read_observation", {}):
                    yield delta
            return
        if len(self.calls) == 2:
            refs = [
                ref
                for message in messages
                if message.role == "tool" and message.name == "read_observation"
                for ref in re.findall(
                    r"memory-evidence-[0-9a-f]{32}",
                    "\n".join(block.text for block in message.content if block.text),
                )
            ]
            assert len(refs) == 2
            for delta in _tool(
                "procedure-add",
                "add_memory",
                {
                    "memory_type": "procedure",
                    "record": {
                        "memory_type": "procedure",
                        "name": "查看告警",
                        "entry_url": "https://monitor.example.com/alarms",
                        "steps": [
                            {
                                "order": 1,
                                "action": "open",
                                "target": {"url": "https://monitor.example.com/alarms"},
                                "expect": {"visible_text": "告警"},
                            }
                        ],
                        "success": {"visible_text": "告警"},
                        "source": "environment_observation",
                    },
                    "evidence_refs": refs,
                },
            ):
                yield delta
            return
        for delta in _text("流程已保存"):
            yield delta


class _FailingSaveBackend:
    def save(self, session_id, payload, *, expected_revision):
        del session_id, payload, expected_revision
        raise RuntimeError("snapshot save failed")

    def load(self, session_id):
        raise FileNotFoundError(session_id)

    def list_session_ids(self):
        return ()

    def export_markdown(self, session_id):
        raise FileNotFoundError(session_id)


class _Backend:
    backend_id = "backend-test"
    generation = 1
    state_sequence = 0
    event_sequence = 0

    def __init__(self) -> None:
        self.close_count = 0

    def advance(self) -> None:
        self.state_sequence += 1
        self.event_sequence += 1

    def close(self) -> None:
        self.close_count += 1


class _EchoExecutor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        del context
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"value": str(arguments["value"])},
            backend_attempted=False,
        )


class _WaitingUserExecutor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=str(arguments["question"]),
            data={
                "waiting_user": True,
                "question": str(arguments["question"]),
                "tool_call_id": context.tool_call_id,
            },
            backend_attempted=True,
        )


class _BlockingTaskStateExecutor:
    def __init__(self, terminal_path: Path) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.terminal_path = terminal_path

    def __call__(self, *, arguments, run_context) -> ToolExecutionResult:
        del arguments
        store = run_context.deps["task_state_store"]
        store.create_or_replace_plan(
            goal="run-local",
            subtasks=[{"id": "a", "description": "A"}],
        )
        self.entered.set()
        self.release.wait(5)
        self.terminal_path.write_text("late-mutation-finished", encoding="utf-8")
        store.mark_completed(final_summary="late")
        return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS)


class _ActionExecutor:
    async def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments
        context.backend.advance()
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"ok": True},
            backend_attempted=True,
        )


class _OrderedActionExecutor:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def execute(self, arguments, context) -> ToolExecutionResult:
        del arguments
        self.order.append("action")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            data={"ok": True},
            backend_attempted=True,
        )


class _ScreenshotBackend(_Backend):
    async def screenshot(self) -> bytes:
        image = Image.new("RGB", (2, 2), color=(3, 5, 7))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


class _StatusVerifier:
    def __init__(self, status: VerificationStatus) -> None:
        self.status = status

    async def verify(self, result, context) -> VerificationRecord:
        del result, context
        return VerificationRecord(
            status=self.status,
            detail="verification is not complete",
            evidence_refs=("verification/failed",)
            if self.status is VerificationStatus.FAILED
            else (),
        )


def _definition(
    internal_id: str,
    alias: str,
    *,
    policy: VerificationPolicy | None = None,
    state_effects: tuple[str, ...] = (),
    input_schema: dict[str, object] | None = None,
    output_schema: dict[str, object] | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        internal_id=internal_id,
        model_alias=alias,
        description=f"Test {alias}.",
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema or {"type": "object"},
        verification_policy=policy or VerificationPolicy(),
        provenance=ToolProvenance(source="test", reference=internal_id),
        version="1.9.0",
        state_effects=state_effects,
    )


def _echo_tool(internal_id: str = "test.echo.v1", alias: str = "echo") -> RegisteredTool:
    return RegisteredTool(
        definition=_definition(
            internal_id,
            alias,
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        ),
        executor=_EchoExecutor(),
    )


def _waiting_user_tool() -> RegisteredTool:
    return RegisteredTool(
        definition=_definition(
            "test.ask_user.v1",
            "ask_user_question",
            input_schema={
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
            output_schema={"type": "object"},
        ),
        executor=_WaitingUserExecutor(),
    )


def _observation_tool() -> RegisteredTool:
    return RegisteredTool(
        definition=_definition(
            "test.read_observation.v1",
            "read_observation",
            state_effects=("read",),
            input_schema={"type": "object", "additionalProperties": False},
        ),
        executor=_ObservationExecutor(),
    )


def _progress_tool(*, external: bool = False) -> RegisteredTool:
    adapted = adapt_legacy_tool_spec(
        make_task_progress_check_tool(),
        internal_id="test.task_progress_check.v1",
        version="1.9.0",
        output_schema={"type": "object"},
    ).registered_tool
    if not external:
        return adapted
    return RegisteredTool(
        definition=replace(
            adapted.definition,
            verification_policy=VerificationPolicy(
                terminal_rule=TerminalRule.EXTERNAL_TERMINAL_OWNER
            ),
        ),
        executor=adapted.executor,
    )


def _text(value: str) -> list[TransportDelta]:
    return [TransportDelta(type="text", text_delta=value, finish_reason="stop")]


def _tool(call_id: str, name: str, arguments: dict[str, Any]) -> list[TransportDelta]:
    return [
        TransportDelta(
            type="tool_call",
            tool_call_delta=ToolCall(id=call_id, name=name, arguments=arguments),
            finish_reason="tool_calls",
        )
    ]


def _application(
    tmp_path,
    tools: list[RegisteredTool],
    transports: dict[str, Any],
    *,
    profile_name: str = "test",
    extension_runner: HookRunner | None = None,
    artifact_publisher: ArtifactPublisher | None = None,
    application_services: dict[str, object] | None = None,
) -> ApplicationRuntime:
    del profile_name
    registry = ToolRegistry()
    registry.register_many([from_registered_tool(tool) for tool in tools])
    bus = EventBus()
    provider_profile = ProviderProfileConfig(
        name="fake",
        protocol="anthropic",
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
        application_services=dict(application_services or {}),
    )

    def provider_factory(request, run_id):
        del run_id
        if request.text == "internal compact control":
            return next(iter(transports.values()))
        return transports[request.text]

    return ApplicationRuntime(
        registry=registry,
        tool_executor=ToolExecutor(registry),
        event_bus=bus,
        session_manager=SessionManager(session_root=tmp_path),
        provider_factory=provider_factory,
        context_assembler_factory=context_factory,
        settings=settings,
        extension_runner=extension_runner,
        artifact_publisher=artifact_publisher,
    )


@pytest.mark.asyncio
async def test_runtime_registers_user_evidence_and_dispatches_memory_write(tmp_path) -> None:
    ledger = MemoryEvidenceLedger(tmp_path / "evidence.sqlite3")
    ledger.start()
    store = _RecordingMindMemOS()
    transport = _MemoryEvidenceTransport()
    app = _application(
        tmp_path,
        list(build_memory_tools()),
        {"钥匙在玄关抽屉": transport},
        application_services={
            "memory_evidence_ledger": ledger,
            "mindmemos": store,
        },
    )

    result = await app.run(
        RunRequest(
            text="钥匙在玄关抽屉",
            session_id="memory-runtime",
            profile="home",
        )
    )

    assert result.status is RunStatus.REPLIED
    assert result.final_reply == "记住了"
    assert transport.evidence_ref is not None
    assert len(store.calls) == 1
    record, provenance_seq = store.calls[0]
    assert record.value == {"container": "玄关抽屉"}
    assert provenance_seq > 0
    first_messages = transport.calls[0]["messages"]
    assert first_messages[-1].content[0].text == "钥匙在玄关抽屉"
    assert transport.evidence_ref not in first_messages[-1].content[0].text
    add_result = next(
        message
        for message in transport.calls[1]["messages"]
        if message.role == "tool" and message.name == "add_memory"
    )
    add_payload = json.loads(add_result.content[0].text)
    assert add_payload["memory_id"] == "memory-runtime-1"
    assert add_payload["record"]["value"] == {"container": "玄关抽屉"}
    await app.aclose()
    ledger.close()


@pytest.mark.asyncio
async def test_runtime_projects_memory_search_records_into_model_tool_content(
    tmp_path,
) -> None:
    transport = _MemoryRecallTransport()
    app = _application(
        tmp_path,
        list(build_memory_tools()),
        {"钥匙在哪里": transport},
        application_services={"mindmemos": _MemoryRecallStore()},
    )

    result = await app.run(
        RunRequest(text="钥匙在哪里", session_id="memory-recall-runtime", profile="home")
    )

    assert result.status is RunStatus.REPLIED
    assert result.final_reply == "钥匙在玄关抽屉"
    assert len(transport.calls) == 2
    await app.aclose()


@pytest.mark.asyncio
async def test_runtime_commits_ordered_observations_before_procedure_write(tmp_path) -> None:
    ledger = MemoryEvidenceLedger(tmp_path / "procedure-evidence.sqlite3")
    ledger.start()
    store = _RecordingMindMemOS()
    transport = _ProcedureEvidenceTransport()
    app = _application(
        tmp_path,
        [*build_memory_tools(), _observation_tool()],
        {"学习查看告警流程": transport},
        application_services={
            "memory_evidence_ledger": ledger,
            "mindmemos": store,
        },
    )

    result = await app.run(
        RunRequest(text="学习查看告警流程", session_id="procedure-runtime", profile="home")
    )

    assert result.status is RunStatus.REPLIED
    assert result.final_reply == "流程已保存"
    assert len(store.calls) == 1
    procedure, provenance_seq = store.calls[0]
    assert procedure.memory_type == "procedure"
    assert procedure.name == "查看告警"
    assert provenance_seq > 0
    assert len(transport.calls) == 3
    await app.aclose()
    ledger.close()


@pytest.mark.asyncio
async def test_waiting_user_result_persists_and_resumes_with_answer_in_history(tmp_path) -> None:
    first_transport = _FakeTransport(
        [_tool("call-question", "ask_user_question", {"question": "Which room?"})]
    )
    answer_transport = _FakeTransport([_text("I will use the kitchen.")])
    app = _application(
        tmp_path,
        [_waiting_user_tool()],
        {"start": first_transport, "kitchen": answer_transport},
    )

    first = await app.run(RunRequest(text="start", session_id="remote-wait", profile="test"))

    assert first.status is RunStatus.WAITING_USER
    assert first.final_reply == "Which room?"
    assert app.status("remote-wait").status == "waiting_user"
    restored = await SessionManager(session_root=tmp_path).resume("remote-wait")
    assert restored.agent_state.status == "waiting_user"

    second = await app.run(
        RunRequest(
            text="kitchen",
            session_id="remote-wait",
            profile="test",
            resume=True,
        )
    )

    assert second.status is RunStatus.REPLIED
    assert second.final_reply == "I will use the kitchen."
    messages = answer_transport.calls[0]["messages"]
    assert [message.role for message in messages[-4:]] == [
        "user",
        "assistant",
        "tool",
        "user",
    ]
    assert messages[-1].content[0].text == "kitchen"
    await app.aclose()


@pytest.mark.asyncio
async def test_extension_lifecycle_is_once_per_application_and_run(tmp_path) -> None:
    events: list[str] = []

    async def application_start(context: HookContext) -> None:
        del context
        events.append("application_start")

    async def run_start(context: HookContext) -> bool:
        events.append(f"run_start:{context.payload['prompt']}")
        return context.payload["prompt"] != "blocked"

    async def run_end(context: HookContext) -> None:
        events.append(f"run_end:{context.payload['run_id']}")

    async def application_stop(context: HookContext) -> None:
        del context
        events.append("application_stop")

    async def cleanup() -> None:
        events.append("cleanup")

    hooks = (
        HookSpec(
            "test.extensions",
            "app-start",
            HookEvent.APPLICATION_START,
            application_start,
            "hook.lifecycle",
        ),
        HookSpec(
            "test.extensions",
            "run-start",
            HookEvent.RUN_START,
            run_start,
            "hook.lifecycle",
            block_on_failure=True,
        ),
        HookSpec("test.extensions", "run-end", HookEvent.RUN_END, run_end, "hook.lifecycle"),
        HookSpec(
            "test.extensions",
            "app-stop",
            HookEvent.APPLICATION_STOP,
            application_stop,
            "hook.lifecycle",
        ),
    )
    runner = HookRunner(
        ExtensionGeneration(
            generation=1,
            extensions=(
                LoadedExtension(
                    manifest=ExtensionManifest(
                        schema_version=1,
                        extension_id="test.extensions",
                        version="1.0.0",
                        requested_capabilities=("hook.lifecycle",),
                        entrypoint="extension.py",
                    ),
                    root=tmp_path,
                    content_sha256="0" * 64,
                    granted_capabilities=("hook.lifecycle",),
                    enabled_tool_ids=(),
                    contributions=ExtensionContributions(
                        hooks=hooks,
                        cleanup=cleanup,
                    ),
                ),
            ),
            hooks=hooks,
            tools=(),
            enabled_tool_ids=(),
            tool_plane_digest="0" * 64,
        )
    )
    app = _application(
        tmp_path,
        [_echo_tool()],
        {"ok": _FakeTransport([_text("ok")])},
        extension_runner=runner,
    )
    subject = PermissionSubject(
        "operator",
        "cli",
        capabilities=("tool.read", "hook.lifecycle"),
    )

    blocked = await app.run(RunRequest(text="blocked", profile="test", permission_subject=subject))
    assert blocked.status is RunStatus.FAILED
    assert blocked.error_code == "extension_run_start_blocked"
    assert "application_start" in events
    assert events.count("application_start") == 1
    assert events.count("run_start:blocked") == 1
    assert sum(item.startswith("run_end:") for item in events) == 1

    replied = await app.run(RunRequest(text="ok", profile="test", permission_subject=subject))
    assert replied.status is RunStatus.REPLIED
    assert sum(item.startswith("run_start:") for item in events) == 2
    assert sum(item.startswith("run_end:") for item in events) == 2

    await app.aclose()
    await app.aclose()
    assert events.count("application_stop") == 1
    assert events[-2:] == ["application_stop", "cleanup"]
    hook_events = [
        event for event in app.event_bus.events if event.type == "extension.hook_completed"
    ]
    assert [event.payload["event"] for event in hook_events] == [
        "application_start",
        "run_start",
        "run_end",
        "run_start",
        "run_end",
        "application_stop",
    ]
    cleanup_events = [
        event for event in app.event_bus.events if event.type == "extension.cleanup_completed"
    ]
    assert len(cleanup_events) == 1
    assert cleanup_events[0].payload["success"] is True


@pytest.mark.asyncio
async def test_blocked_application_start_rolls_back_extension_and_resources(tmp_path) -> None:
    cleaned = False

    async def block(context: HookContext) -> bool:
        del context
        return False

    async def cleanup() -> None:
        nonlocal cleaned
        cleaned = True

    hook = HookSpec(
        "test.extensions",
        "block-start",
        HookEvent.APPLICATION_START,
        block,
        "hook.lifecycle",
        block_on_failure=True,
    )
    loaded = LoadedExtension(
        manifest=ExtensionManifest(
            1,
            "test.extensions",
            "1.0.0",
            ("hook.lifecycle",),
            "extension.py",
        ),
        root=tmp_path,
        content_sha256="0" * 64,
        granted_capabilities=("hook.lifecycle",),
        enabled_tool_ids=(),
        contributions=ExtensionContributions(hooks=(hook,), cleanup=cleanup),
    )
    runner = HookRunner(
        ExtensionGeneration(
            generation=1,
            extensions=(loaded,),
            hooks=(hook,),
            tools=(),
            enabled_tool_ids=(),
            tool_plane_digest="0" * 64,
        )
    )
    app = _application(tmp_path, [_echo_tool()], {}, extension_runner=runner)

    with pytest.raises(RuntimeError, match="application_start extension hook blocked"):
        await app.start()

    assert cleaned is True
    assert app.resource_scope.closed is True


@pytest.mark.asyncio
async def test_run_end_executes_once_for_provider_failure_and_cancellation(tmp_path) -> None:
    run_end_calls: list[str] = []

    async def run_end(context: HookContext) -> None:
        run_end_calls.append(str(context.payload["run_id"]))

    hook = HookSpec(
        "test.extensions",
        "run-end",
        HookEvent.RUN_END,
        run_end,
        "hook.lifecycle",
    )
    runner = HookRunner(
        ExtensionGeneration(
            generation=1,
            extensions=(),
            hooks=(hook,),
            tools=(),
            enabled_tool_ids=(),
            tool_plane_digest="0" * 64,
        )
    )
    cancelling = _AsyncBlockingTransport()
    app = _application(
        tmp_path,
        [_echo_tool()],
        {"failure": _FakeTransport([]), "cancel": cancelling},
        extension_runner=runner,
    )
    subject = PermissionSubject(
        "operator",
        "test",
        capabilities=("tool.read", "hook.lifecycle"),
    )

    failed = await app.run(RunRequest(text="failure", profile="test", permission_subject=subject))
    assert failed.status is RunStatus.FAILED
    assert len(run_end_calls) == 1

    task = asyncio.create_task(
        app.run(RunRequest(text="cancel", profile="test", permission_subject=subject))
    )
    await cancelling.entered.wait()
    task.cancel()
    result = await task

    assert result.status is RunStatus.CANCELLED
    assert len(run_end_calls) == 2
    assert len(set(run_end_calls)) == 2
    await app.aclose()


@pytest.mark.asyncio
async def test_profile_and_request_tool_ids_do_not_filter_registry(tmp_path) -> None:
    first = _echo_tool()
    second = RegisteredTool(
        definition=replace(
            first.definition,
            internal_id="test.other.v1",
            model_alias="other",
        ),
        executor=first.executor,
    )
    transport = _FakeTransport([_text("ok")])
    app = _application(
        tmp_path,
        [first, second],
        {"spoof": transport},
    )
    result = await app.run(
        RunRequest(
            text="spoof",
            profile="alfworld",
        )
    )

    assert result.status is RunStatus.REPLIED
    assert {tool["name"] for tool in transport.calls[0]["tools"]} == {
        "echo",
        "other",
    }


@pytest.mark.asyncio
async def test_fake_entry_runs_pipeline_persists_and_keeps_backend_borrowed(tmp_path) -> None:
    transport = _FakeTransport(
        [
            _tool("call-1", "echo", {"value": "ok"}),
            _text("done"),
        ]
    )
    app = _application(tmp_path, [_echo_tool()], {"request": transport})
    backend = _Backend()

    result = await app.run(RunRequest(text="request", profile="test", environment=backend))

    assert result.status is RunStatus.REPLIED
    assert result.final_reply == "done"
    assert backend.close_count == 0
    assert transport.calls[0]["tools"][0]["name"] == "echo"
    status = app.status(result.session_id)
    assert status.active is False
    assert status.revision == 1
    roles = [
        message.role for message in app.session_manager.get(result.session_id).session.messages
    ]
    assert roles == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_runtime_binds_untyped_backend_to_authoritative_tenant_once(tmp_path) -> None:
    app = _application(
        tmp_path,
        [_echo_tool()],
        {
            "first": _FakeTransport([_text("first done")]),
            "second": _FakeTransport([_text("second done")]),
        },
    )
    manager = DeviceLeaseManager()
    pool = DeviceConnectionPool(manager)
    app.settings.device_connection_pool = pool
    app.tool_executor.resource_manager = manager
    backend = _Backend()

    def subject(tenant_id: str) -> PermissionSubject:
        return PermissionSubject(
            subject_id=f"operator-{tenant_id}",
            tenant_id=tenant_id,
            channel="gateway",
            capabilities=("device.control",),
        )

    first = await app.run(
        RunRequest(
            text="first",
            profile="test",
            environment=backend,
            permission_subject=subject("tenant-a"),
        )
    )

    assert first.status is RunStatus.REPLIED
    with pytest.raises(DeviceLeaseError) as error:
        await app.run(
            RunRequest(
                text="second",
                profile="test",
                environment=backend,
                permission_subject=subject("tenant-b"),
            )
        )
    assert error.value.error_code == "cross_tenant_device"


@pytest.mark.asyncio
async def test_every_registered_tool_is_callable_without_request_filtering(tmp_path) -> None:
    enabled = _echo_tool()
    disabled = _echo_tool("test.disabled.v1", "disabled")
    transport = _FakeTransport(
        [
            _tool("call-disabled", "disabled", {"value": "works"}),
            _text("stopped"),
        ]
    )
    app = _application(tmp_path, [enabled, disabled], {"request": transport})

    result = await app.run(
        RunRequest(
            text="request",
            profile="test",
        )
    )

    messages = app.session_manager.get(result.session_id).session.messages
    tool_result = next(message for message in messages if message.role == "tool")
    assert tool_result.is_error is False
    assert tool_result.data is not None
    assert tool_result.data["value"] == "works"


@pytest.mark.asyncio
async def test_action_does_not_block_task_completion_without_observe(tmp_path) -> None:
    action = RegisteredTool(
        definition=_definition(
            "test.action.v1",
            "action",
            policy=VerificationPolicy(),
            state_effects=("backend.advance",),
            output_schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        ),
        executor=_ActionExecutor(),
    )
    progress = adapt_legacy_tool_spec(
        make_task_progress_check_tool(),
        internal_id="test.task_progress_check.v1",
        version="1.9.0",
        output_schema={"type": "object"},
    ).registered_tool
    transport = _FakeTransport(
        [
            _tool("call-action", "action", {}),
            _tool(
                "call-complete",
                "task_progress_check",
                {"updates": [], "task_status": "completed", "completion_summary": "done"},
            ),
            _text("cannot complete yet"),
        ]
    )
    app = _application(tmp_path, [action, progress], {"request": transport})
    runtime = await app.session_manager.open_or_resume("completion")
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    result = await app.run(
        RunRequest(
            text="request",
            session_id="completion",
            profile="test",
            resume=True,
            environment=_Backend(),
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert runtime.task_state_store.snapshot is not None
    assert runtime.task_state_store.snapshot.status is TaskStatus.COMPLETED
    completion_result = next(
        message
        for message in runtime.session.messages
        if message.role == "tool" and message.name == "task_progress_check"
    )
    assert completion_result.data is not None
    assert completion_result.data["status"] == "completed"


@pytest.mark.asyncio
async def test_external_terminal_rule_blocks_model_completion_claim(tmp_path) -> None:
    adapted = adapt_legacy_tool_spec(
        make_task_progress_check_tool(),
        internal_id="test.task_progress_check.v1",
        version="1.9.0",
        output_schema={"type": "object"},
    ).registered_tool
    progress = RegisteredTool(
        definition=replace(
            adapted.definition,
            verification_policy=VerificationPolicy(
                terminal_rule=TerminalRule.EXTERNAL_TERMINAL_OWNER
            ),
        ),
        executor=adapted.executor,
    )
    transport = _FakeTransport(
        [
            _tool(
                "call-complete",
                "task_progress_check",
                {"updates": [], "task_status": "completed"},
            ),
            _text("pending"),
        ]
    )
    app = _application(tmp_path, [progress], {"request": transport})
    runtime = await app.session_manager.open_or_resume("external")
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    await app.run(RunRequest(text="request", session_id="external", profile="test", resume=True))

    assert runtime.task_state_store.snapshot is not None
    assert runtime.task_state_store.snapshot.status is TaskStatus.ACTIVE
    blocked = next(message for message in runtime.session.messages if message.role == "tool")
    assert blocked.data is not None
    assert blocked.data["status"] == "verification_pending"
    assert blocked.data["error_code"] == "external_terminal_pending"


@pytest.mark.asyncio
async def test_cancel_fences_late_return_and_control_adds_no_message(tmp_path) -> None:
    transport = _BlockingTransport()
    app = _application(tmp_path, [_echo_tool()], {"blocking": transport})
    run_task = asyncio.create_task(
        app.run(RunRequest(text="blocking", session_id="cancelled", profile="test"))
    )
    await asyncio.to_thread(transport.entered.wait, 2)

    assert app.cancel("cancelled") is True
    transport.release.set()
    result = await run_task

    assert result.status is RunStatus.CANCELLED
    runtime = app.session_manager.get("cancelled")
    assert [message.role for message in runtime.session.messages] == ["user"]
    message_count = len(runtime.session.messages)
    await app.compact("cancelled")
    assert len(runtime.session.messages) == message_count


@pytest.mark.asyncio
async def test_provider_deadline_cancels_native_async_stream(tmp_path) -> None:
    transport = _AsyncBlockingTransport()
    app = _application(tmp_path, [_echo_tool()], {"deadline": transport})

    result = await app.run(
        RunRequest(
            text="deadline",
            session_id="provider-deadline",
            profile="test",
            run_policy=RunPolicy(deadline_s=0.02),
        )
    )

    assert result.status is RunStatus.FAILED
    assert result.error_code == "deadline_exceeded"
    assert transport.entered.is_set()
    assert transport.cancelled is True


@pytest.mark.asyncio
async def test_blocked_native_provider_does_not_block_another_session(tmp_path) -> None:
    blocked = _AsyncBlockingTransport()
    fast = _FakeTransport([_text("fast")])
    app = _application(tmp_path, [_echo_tool()], {"blocked": blocked, "fast": fast})
    blocked_task = asyncio.create_task(
        app.run(RunRequest(text="blocked", session_id="blocked", profile="test"))
    )
    await blocked.entered.wait()

    fast_result = await asyncio.wait_for(
        app.run(RunRequest(text="fast", session_id="fast", profile="test")),
        timeout=0.5,
    )
    blocked.release.set()
    blocked_result = await blocked_task

    assert fast_result.status is RunStatus.REPLIED
    assert fast_result.final_reply == "fast"
    assert blocked_result.status is RunStatus.REPLIED


@pytest.mark.asyncio
async def test_cancel_does_not_wait_for_blocked_tool_or_publish_run_local_task_state(
    tmp_path,
) -> None:
    terminal_path = tmp_path / "cancelled-tool-terminal.txt"
    blocking = _BlockingTaskStateExecutor(terminal_path)
    adapted = adapt_legacy_tool_spec(
        make_task_progress_check_tool(),
        internal_id="test.blocking_state.v1",
        version="1.9.0",
        executor=blocking,
        output_schema={"type": "object"},
    ).registered_tool
    tool = RegisteredTool(
        definition=replace(adapted.definition, state_effects=("external.write",)),
        executor=adapted.executor,
    )
    transport = _FakeTransport(
        [
            _tool("call-block", tool.definition.model_alias, {"updates": []}),
            _text("late"),
        ]
    )
    app = _application(tmp_path, [tool], {"block": transport})
    run_task = asyncio.create_task(
        app.run(RunRequest(text="block", session_id="tool-cancel", profile="test"))
    )
    assert await asyncio.to_thread(blocking.entered.wait, 2)

    started = time.monotonic()
    assert app.cancel("tool-cancel") is True
    assert time.monotonic() - started < 0.2
    result = await asyncio.wait_for(run_task, timeout=0.5)

    runtime = app.session_manager.get("tool-cancel")
    assert result.status is RunStatus.CANCELLED
    failed_event = next(event for event in result.events if event.type == "tool.call_failed")
    assert failed_event.payload["data"]["status"] == "outcome_unknown"
    assert failed_event.payload["data"]["backend_attempted"] is True
    assert not terminal_path.exists()
    assert runtime.task_state_store.snapshot is None
    assert runtime.last_result is None
    async with app.session_manager.turn("tool-cancel"):
        pass
    blocking.release.set()
    for _ in range(100):
        if terminal_path.exists():
            break
        await asyncio.sleep(0.01)
    assert terminal_path.read_text(encoding="utf-8") == "late-mutation-finished"


@pytest.mark.asyncio
async def test_provider_that_swallows_cancellation_cannot_publish_late_events(
    tmp_path,
) -> None:
    transport = _CancellationSwallowingTransport()
    app = _application(tmp_path, [_echo_tool()], {"late": transport})
    run_task = asyncio.create_task(
        app.run(RunRequest(text="late", session_id="late-events", profile="test"))
    )
    await transport.entered.wait()
    events_before_cancel = tuple(app.event_bus.events)

    assert app.cancel("late-events") is True
    result = await run_task

    runtime = app.session_manager.get("late-events")
    assert result.status is RunStatus.CANCELLED
    assert result.error_code == "stale_generation"
    assert tuple(app.event_bus.events) == events_before_cancel
    assert runtime.last_result is None
    assert runtime.revision == 0
    await app.event_bus.aclose()


@pytest.mark.asyncio
async def test_screenshot_does_not_authorize_or_block_the_next_action(tmp_path) -> None:
    order: list[str] = []
    observe = RegisteredTool(
        definition=_definition(
            "core.observe.v1",
            "observe",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={"type": "object", "properties": {}, "additionalProperties": False},
            policy=VerificationPolicy(),
        ),
        executor=ScreenshotTool(),
    )
    action = RegisteredTool(
        definition=_definition(
            "test.bound_action.v1",
            "bound_action",
            policy=VerificationPolicy(),
        ),
        executor=_OrderedActionExecutor(order),
    )
    transport = _FakeTransport(
        [
            _tool("call-observe", "observe", {}),
            _tool("call-action", "bound_action", {}),
            _text("done"),
        ]
    )
    backend = _ScreenshotBackend()
    store = ToolOutputStore(tmp_path / "gateway-artifacts", quota_bytes=4096, ttl_seconds=60)
    app = _application(
        tmp_path,
        [observe, action],
        {"observe then act": transport},
        artifact_publisher=ArtifactPublisher(store),
    )

    result = await app.run(
        RunRequest(
            text="observe then act",
            profile="test",
            environment=backend,
            permission_subject=PermissionSubject(
                subject_id="operator",
                tenant_id="tenant-a",
                channel="gateway",
            ),
        )
    )

    assert result.status is RunStatus.REPLIED
    assert order == ["action"]
    observe_result = next(
        message
        for message in app.session_manager.get(result.session_id).session.messages
        if message.role == "tool" and message.name == "observe"
    )
    assert len(observe_result.content) == 1
    assert observe_result.content[0].type == "image"
    next_request = transport.calls[1]["messages"]
    outbound_observe = next(
        message for message in next_request if message.role == "tool" and message.name == "observe"
    )
    assert len(outbound_observe.content) == 1
    assert outbound_observe.content[0].type == "image"
    image_bytes = base64.b64decode(outbound_observe.content[0].source["data"], validate=True)
    completed = next(
        event
        for event in app.event_bus.events
        if event.type == "tool.call_completed" and event.name == "observe"
    )
    assert outbound_observe.content[0].source["data"] not in repr(completed.payload)
    artifact = completed.payload["data"]["artifacts"][0]
    assert (
        store.read(
            artifact["artifact_handle"],
            tenant_id="tenant-a",
            session_id=result.session_id,
            run_id=result.run_id,
        )
        == image_bytes
    )
    action_result = next(
        message
        for message in app.session_manager.get(result.session_id).session.messages
        if message.role == "tool" and message.name == "bound_action"
    )
    assert action_result.is_error is False


@pytest.mark.asyncio
async def test_different_sessions_isolate_view_backend_and_cancellation(tmp_path) -> None:
    first_transport = _BlockingTransport()
    second_transport = _BlockingTransport()
    first_tool = _echo_tool()
    second_tool = _echo_tool("test.second.v1", "second")
    app = _application(
        tmp_path,
        [first_tool, second_tool],
        {"first": first_transport, "second": second_transport},
    )
    first_backend = _Backend()
    second_backend = _Backend()
    first_run = asyncio.create_task(
        app.run(
            RunRequest(
                text="first",
                session_id="session-first",
                profile="test",
                environment=first_backend,
            )
        )
    )
    second_run = asyncio.create_task(
        app.run(
            RunRequest(
                text="second",
                session_id="session-second",
                profile="test",
                environment=second_backend,
            )
        )
    )
    entered = await asyncio.gather(
        asyncio.to_thread(first_transport.entered.wait, 2),
        asyncio.to_thread(second_transport.entered.wait, 2),
    )
    assert entered == [True, True]

    assert app.cancel("session-first") is True
    first_transport.release.set()
    second_transport.release.set()
    first_result, second_result = await asyncio.gather(first_run, second_run)

    assert first_result.status is RunStatus.CANCELLED
    assert second_result.status is RunStatus.REPLIED
    assert [tool["name"] for tool in first_transport.calls[0]["tools"]] == ["echo", "second"]
    assert [tool["name"] for tool in second_transport.calls[0]["tools"]] == ["echo", "second"]
    assert first_backend.close_count == second_backend.close_count == 0
    assert [
        message.role for message in app.session_manager.get("session-first").session.messages
    ] == ["user"]
    assert [
        message.role for message in app.session_manager.get("session-second").session.messages
    ] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_same_session_turns_are_serialized(tmp_path) -> None:
    first_transport = _BlockingTransport()
    second_transport = _BlockingTransport()
    app = _application(
        tmp_path,
        [_echo_tool()],
        {"first": first_transport, "second": second_transport},
    )
    first_run = asyncio.create_task(
        app.run(RunRequest(text="first", session_id="shared", profile="test"))
    )
    assert await asyncio.to_thread(first_transport.entered.wait, 2)
    second_run = asyncio.create_task(
        app.run(
            RunRequest(
                text="second",
                session_id="shared",
                profile="test",
                resume=True,
            )
        )
    )
    await asyncio.sleep(0.05)
    assert second_transport.entered.is_set() is False

    first_transport.release.set()
    assert (await first_run).status is RunStatus.REPLIED
    assert await asyncio.to_thread(second_transport.entered.wait, 2)
    second_transport.release.set()
    assert (await second_run).status is RunStatus.REPLIED

    runtime = app.session_manager.get("shared")
    assert runtime.revision == 2
    assert [message.role for message in runtime.session.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_normal_completion_is_not_blocked_by_external_gate(tmp_path) -> None:
    progress = _progress_tool()
    transport = _FakeTransport(
        [
            _tool(
                "call-complete",
                "task_progress_check",
                {"updates": [], "task_status": "completed"},
            ),
            _text("complete"),
        ]
    )
    app = _application(tmp_path, [progress], {"complete": transport})
    runtime = await app.session_manager.open_or_resume("normal-completion")
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    result = await app.run(
        RunRequest(
            text="complete",
            session_id="normal-completion",
            profile="test",
            resume=True,
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert runtime.task_state_store.snapshot is not None
    assert runtime.task_state_store.snapshot.status is TaskStatus.COMPLETED


@pytest.mark.parametrize(
    "verification_status",
    [VerificationStatus.PENDING, VerificationStatus.FAILED],
)
@pytest.mark.asyncio
async def test_incomplete_verification_blocks_completion(
    tmp_path,
    verification_status: VerificationStatus,
) -> None:
    verified = RegisteredTool(
        definition=_definition(
            "test.verified.v1",
            "verified",
            policy=VerificationPolicy(execution_proof=ExecutionProof.EXTERNAL_STATE),
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),
        executor=_EchoExecutor(),
        verifier=_StatusVerifier(verification_status),
    )
    progress = _progress_tool()
    transport = _FakeTransport(
        [
            _tool("call-verified", "verified", {"value": "x"}),
            _tool(
                "call-complete",
                "task_progress_check",
                {"updates": [], "task_status": "completed"},
            ),
            _text("pending"),
        ]
    )
    app = _application(tmp_path, [verified, progress], {"verify": transport})
    runtime = await app.session_manager.open_or_resume("verification")
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    await app.run(
        RunRequest(
            text="verify",
            session_id="verification",
            profile="test",
            resume=True,
        )
    )

    assert runtime.task_state_store.snapshot is not None
    assert runtime.task_state_store.snapshot.status is TaskStatus.ACTIVE
    completion = next(
        message
        for message in runtime.session.messages
        if message.role == "tool" and message.name == "task_progress_check"
    )
    assert completion.data is not None
    assert completion.data["error_code"] == "verification_pending"


@pytest.mark.asyncio
async def test_external_owner_success_allows_completion(tmp_path) -> None:
    progress = _progress_tool(external=True)
    transport = _FakeTransport(
        [
            _tool(
                "call-complete",
                "task_progress_check",
                {"updates": [], "task_status": "completed"},
            ),
            _text("complete"),
        ]
    )
    app = _application(tmp_path, [progress], {"complete": transport})
    runtime = await app.session_manager.open_or_resume("external-success")
    runtime.task_state_store.create_or_replace_plan(
        goal="goal",
        subtasks=[{"id": "a", "description": "A"}],
    )

    result = await app.run(
        RunRequest(
            text="complete",
            session_id="external-success",
            profile="test",
            resume=True,
            dependencies={"external_terminal_owner": SimpleNamespace(succeeded=True)},
        )
    )

    assert result.status is RunStatus.COMPLETED
    assert runtime.task_state_store.snapshot is not None
    assert runtime.task_state_store.snapshot.status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_owned_provider_closes_once_and_borrowed_provider_stays_open(tmp_path) -> None:
    owned = _ClosableTransport([_text("owned")])
    borrowed = _ClosableTransport([_text("borrowed")])
    app = _application(
        tmp_path,
        [_echo_tool()],
        {
            "owned": owned,
            "borrowed": ResourceBinding.borrowed("shared-provider", borrowed),
        },
    )

    owned_result = await app.run(RunRequest(text="owned", profile="test"))
    borrowed_result = await app.run(RunRequest(text="borrowed", profile="test"))
    await app.aclose()

    assert owned_result.status is RunStatus.REPLIED
    assert borrowed_result.status is RunStatus.REPLIED
    assert owned.close_count == 1
    assert borrowed.close_count == 0


@pytest.mark.asyncio
async def test_context_error_remains_primary_when_provider_cleanup_fails(tmp_path) -> None:
    provider = _FailingCloseTransport([_text("unused")])
    app = _application(tmp_path, [_echo_tool()], {"context": provider})

    def fail_context(request, value):
        del request, value
        raise ValueError("context construction failed")

    app.context_assembler_factory = fail_context
    with pytest.raises(ValueError, match="context construction failed") as error:
        await app.run(RunRequest(text="context", profile="test"))

    assert isinstance(getattr(error.value, "cleanup_error", None), ResourceCleanupError)


@pytest.mark.asyncio
async def test_save_error_remains_primary_when_provider_cleanup_fails(tmp_path) -> None:
    provider = _FailingCloseTransport([_text("reply")])
    app = _application(tmp_path, [_echo_tool()], {"save": provider})
    app.session_manager = SessionManager(backend=_FailingSaveBackend())

    with pytest.raises(RuntimeError, match="snapshot save failed") as error:
        await app.run(RunRequest(text="save", profile="test"))

    assert isinstance(getattr(error.value, "cleanup_error", None), ResourceCleanupError)


@pytest.mark.asyncio
async def test_completed_run_does_not_retain_borrowed_request_objects(tmp_path) -> None:
    transport = _FakeTransport([_text("reply")])
    app = _application(tmp_path, [_echo_tool()], {"gc": transport})
    backend = _Backend()

    class Observer:
        pass

    observer = Observer()
    backend_ref = weakref.ref(backend)
    observer_ref = weakref.ref(observer)
    request = RunRequest(
        text="gc",
        profile="test",
        environment=backend,
        dependencies={"domain_observer": observer},
    )
    await app.run(request)

    del request, backend, observer
    gc.collect()
    assert backend_ref() is None
    assert observer_ref() is None
