from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.adapters.thread_owned_sync import ThreadOwnedSyncBackendAdapter
from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionStatus,
)
from homemaster.tools.dispatcher import ToolDispatcher
from homemaster.tools.legacy_adapter import (
    LegacyObserverAdapter,
    LegacyToolExecutionContext,
    adapt_legacy_tool_spec,
    normalize_legacy_result,
)
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


def _run_context(**deps: Any) -> RunContext:
    return RunContext(
        session_id="session-1",
        run_id="run-1",
        turn_index=2,
        settings=SimpleNamespace(),
        event_sink=None,
        deps=deps,
    )


def _canonical_context(
    run_context: RunContext,
    *,
    tool_call_id: str = "call-1",
    internal_tool_id: str = "legacy.echo.v1",
) -> ToolExecutionContext:
    legacy_context = LegacyToolExecutionContext(
        run_context=run_context,
        tool_call_id=tool_call_id,
        internal_tool_id=internal_tool_id,
    )
    return ToolExecutionContext(
        session_id=run_context.session_id,
        run_id=run_context.run_id,
        turn_index=run_context.turn_index,
        tool_call_id=tool_call_id,
        internal_tool_id=internal_tool_id,
        permission_subject=PermissionSubject(subject_id="subject-1", channel="cli"),
        backend=legacy_context,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=Path.cwd(),
    )


def test_legacy_tool_spec_adapts_definition_registration_and_selectability() -> None:
    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        return ToolResult(success=True, tool_name="Echo Tool", data=arguments)

    spec = ToolSpec(
        name="Echo Tool",
        description="Echo input.",
        input_schema={"type": "object", "required": ["text"]},
        output_schema={"type": "object"},
        executor_mode="programmatic",
        selectable_by_model=False,
        state_effects=["memory.write"],
        executor=executor,
    )

    adapted = adapt_legacy_tool_spec(spec, internal_id="home.echo.v1", version="1.2.0")

    assert adapted.definition.internal_id == "home.echo.v1"
    assert adapted.definition.model_alias == "echo_tool"
    assert adapted.definition.to_model_manifest()["input_schema"] == spec.input_schema
    assert adapted.definition.to_dict()["output_schema"] == spec.output_schema
    assert adapted.definition.state_effects == ("memory.write",)
    assert adapted.registered_tool.definition is adapted.definition
    assert adapted.selectable_by_model is False
    assert adapted.debt.has("executor_mode")
    assert adapted.debt.has("not_selectable_by_model")
    assert not adapted.debt.has("empty_output_schema")


def test_empty_output_schema_is_debt_and_is_not_fabricated() -> None:
    spec = ToolSpec(
        name="empty_output",
        description="No declared output.",
        input_schema={"type": "object"},
        executor_mode="programmatic",
        executor=lambda **_: {"success": True},
    )

    adapted = adapt_legacy_tool_spec(spec)

    assert adapted.definition.to_dict()["output_schema"] == {}
    assert adapted.debt.has("empty_output_schema")


def test_sync_legacy_executor_is_awaitable_and_receives_explicit_run_context() -> None:
    run_context = _run_context(sentinel="kept")
    calls: list[tuple[dict[str, object], RunContext]] = []

    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        calls.append((arguments, run_context))
        return ToolResult(success=True, tool_name="echo", data={"echo": arguments["text"]})

    adapted = adapt_legacy_tool_spec(
        ToolSpec(
            name="echo",
            description="Echo.",
            output_schema={"type": "object"},
            executor_mode="programmatic",
            executor=executor,
        )
    )
    result = asyncio.run(
        adapted.executor.execute(
            {"text": "hello"},
            _canonical_context(run_context),
        )
    )

    assert result.status is ToolExecutionStatus.SUCCESS
    assert result.data == {"echo": "hello"}
    assert calls == [({"text": "hello"}, run_context)]


def test_legacy_executor_uses_injected_thread_owned_adapter() -> None:
    adapter = ThreadOwnedSyncBackendAdapter(name="legacy")
    run_context = _run_context(sync_backend_adapter=adapter)
    thread_ids: list[int] = []

    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
        del arguments, run_context
        thread_ids.append(threading.get_ident())
        return ToolResult(success=True, tool_name="owned")

    adapted = adapt_legacy_tool_spec(
        ToolSpec(
            name="owned",
            description="Owned thread.",
            executor_mode="programmatic",
            executor=executor,
        )
    )
    try:
        result = asyncio.run(
            adapted.executor.execute({}, _canonical_context(run_context))
        )
    finally:
        adapter.close()

    assert result.status is ToolExecutionStatus.SUCCESS
    assert thread_ids == [adapter.owner_thread_id]


def test_legacy_tool_result_success_and_failure_normalize_to_typed_results() -> None:
    success = normalize_legacy_result(
        ToolResult(
            success=True,
            tool_name="echo",
            executor_mode="programmatic",
            data={"value": 1},
            evidence_refs=["ledger/1.json"],
            summary="done",
        ),
        tool_call_id="call-success",
        name="echo",
    )
    failure = normalize_legacy_result(
        ToolResult(
            success=False,
            tool_name="echo",
            failure_reason="backend rejected",
            retryable=True,
        ),
        tool_call_id="call-failure",
        name="echo",
    )

    assert success.result.status is ToolExecutionStatus.SUCCESS
    assert success.result.text == "done"
    assert success.result.evidence_refs == ("ledger/1.json",)
    assert success.debt.fields == ("legacy_executor_mode",)
    assert failure.result.status is ToolExecutionStatus.FAILURE
    assert failure.result.error is not None
    assert failure.result.error.message == "backend rejected"
    assert failure.result.retryable is True
    assert failure.to_message().tool_call_id == "call-failure"


def test_dict_result_infers_success_explicitly_and_reports_loss() -> None:
    normalized = normalize_legacy_result(
        {"answer": 42},
        tool_call_id="call-dict",
        name="lookup",
    )

    assert normalized.result.status is ToolExecutionStatus.SUCCESS
    assert normalized.result.data == {"answer": 42}
    assert normalized.debt.fields == ("implicit_success",)
    assert normalized.tool_call_id == "call-dict"
    assert normalized.name == "lookup"


def test_tool_result_message_preserves_envelope_and_hashes_image_content() -> None:
    raw = b"legacy-png"
    encoded = base64.b64encode(raw).decode("ascii")
    message = ToolResultMessage(
        tool_call_id="recorded-call",
        name="observe",
        content=[
            ContentBlock(text="captured"),
            ContentBlock(
                type="image",
                source={"type": "base64", "media_type": "image/png", "data": encoded},
                metadata={"content_sha256": "0" * 64},
            ),
        ],
        data={"success": True, "frame": "fresh"},
    )

    normalized = normalize_legacy_result(
        message,
        tool_call_id="recorded-call",
        name="observe",
    )

    assert normalized.legacy_message is message
    assert normalized.tool_call_id == "recorded-call"
    assert normalized.name == "observe"
    assert normalized.result.text == "captured"
    assert normalized.result.images[0].content_sha256 == hashlib.sha256(raw).hexdigest()
    assert normalized.debt.has("image_content_hash_recomputed")
    projected = normalized.to_message()
    assert projected.tool_call_id == "recorded-call"
    assert projected.name == "observe"
    assert projected.content[1].source["data"] == encoded


def test_terminal_message_normalizes_to_typed_terminal_result() -> None:
    message = ToolResultMessage(
        tool_call_id="call-terminal",
        name="robot_move",
        content=[ContentBlock(text="already terminal")],
        is_error=True,
        data={
            "success": False,
            "error": "episode ended",
            "terminal": True,
            "classification": "runtime_failure",
            "score_eligible": False,
        },
    )

    normalized = normalize_legacy_result(
        message,
        tool_call_id="call-terminal",
        name="robot_move",
    )

    assert normalized.result.status is ToolExecutionStatus.FAILURE
    assert normalized.result.terminal is not None
    assert normalized.result.terminal.classification == "runtime_failure"
    assert normalized.to_message().tool_call_id == message.tool_call_id


def test_message_without_data_recovers_structured_error_from_text() -> None:
    payload = {"success": False, "error": "rejected", "retryable": False}
    message = ToolResultMessage(
        tool_call_id="call-text",
        name="write",
        content=[ContentBlock(text=json.dumps(payload))],
        is_error=True,
    )

    normalized = normalize_legacy_result(
        message,
        tool_call_id="call-text",
        name="write",
    )

    assert normalized.result.status is ToolExecutionStatus.FAILURE
    assert normalized.result.data == payload
    assert normalized.result.error is not None
    assert normalized.result.error.message == "rejected"
    assert normalized.debt.has("message_data_recovered_from_content")


def test_observer_adapter_preserves_exception_and_result_order() -> None:
    order: list[str] = []

    class Observer:
        def on_call(self, tool_call: ToolCall) -> None:
            order.append(f"call:{tool_call.id}")

        def terminal_result(self, tool_call: ToolCall) -> None:
            order.append(f"terminal:{tool_call.id}")
            return None

        def on_exception(self, tool_call: ToolCall, error: Exception) -> ToolResultMessage:
            order.append(f"exception:{tool_call.id}:{type(error).__name__}")
            return ToolResultMessage(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=[ContentBlock(text="failed")],
                is_error=True,
                data={"success": False, "error": "failed"},
            )

        def on_result(self, tool_call: ToolCall, result: Any) -> None:
            order.append(f"result:{tool_call.id}:{type(result).__name__}")

    def executor(**_: Any) -> ToolResult:
        order.append("execute")
        raise RuntimeError("boom")

    dispatcher = ToolDispatcher()
    dispatcher.register(
        ToolSpec(
            name="explode",
            description="Explodes.",
            input_schema={"type": "object"},
            executor_mode="programmatic",
            executor=executor,
        )
    )
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call-1", name="explode", arguments={})],
        run_context=_run_context(tool_dispatch_observer=LegacyObserverAdapter(Observer())),
    )

    assert order == [
        "call:call-1",
        "terminal:call-1",
        "execute",
        "exception:call-1:RuntimeError",
        "result:call-1:ToolResultMessage",
    ]
    assert result[0].tool_call_id == "call-1"


def test_observer_terminal_fence_skips_executor_and_result_callback() -> None:
    order: list[str] = []

    class Observer:
        def on_call(self, tool_call: ToolCall) -> None:
            order.append("call")

        def terminal_result(self, tool_call: ToolCall) -> ToolResultMessage:
            order.append("terminal")
            return ToolResultMessage(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                content=[ContentBlock(text="terminal")],
                is_error=True,
            )

        def on_exception(self, tool_call: ToolCall, error: Exception) -> ToolResultMessage:
            raise AssertionError("exception callback must not run")

        def on_result(self, tool_call: ToolCall, result: Any) -> None:
            raise AssertionError("result callback must not run")

    def executor(**_: Any) -> ToolResult:
        raise AssertionError("terminal fence must skip executor")

    dispatcher = ToolDispatcher()
    dispatcher.register(
        ToolSpec(
            name="fenced",
            description="Fenced.",
            executor_mode="programmatic",
            executor=executor,
        )
    )
    result = dispatcher.dispatch(
        tool_calls=[ToolCall(id="call-terminal", name="fenced", arguments={})],
        run_context=_run_context(tool_dispatch_observer=LegacyObserverAdapter(Observer())),
    )

    assert order == ["call", "terminal"]
    assert result[0].tool_call_id == "call-terminal"
