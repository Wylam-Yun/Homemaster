"""Application wrapper around the universal ordinary-name ToolExecutor."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.tools.base import ToolExecutionContext, ToolRegistry, ToolResult
from homemaster.tools.contracts import (
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.executor import ToolExecutor

_MEMORY_TOOL_NAMES = frozenset(
    {
        "context_memory",
        "mindmemos_add",
        "mindmemos_search",
        "mindmemos_history",
        "mindmemos_update",
        "mindmemos_delete",
        "mindmemos_feedback",
    }
)


class ApplicationToolExecutor:
    def __init__(
        self,
        *,
        executor: ToolExecutor,
        registry: ToolRegistry,
        runtime: Any,
        run_id: str,
        backend: object | None,
        request: Any,
        agent_state: Any,
        task_state_store: Any,
        settings: Any,
        event_sink: Any,
        working_directory: Path,
        completion_requires_external_owner: bool = False,
        verification_required_tool_names: frozenset[str] = frozenset(),
        artifact_publisher: Any | None = None,
        initial_memory_evidence_refs: tuple[str, ...] = (),
    ) -> None:
        self._executor = executor
        self._registry = registry
        self._runtime = runtime
        self._run_id = run_id
        self._backend = backend
        self._request = request
        self._agent_state = agent_state
        self._task_state_store = task_state_store
        self._settings = settings
        self._event_sink = event_sink
        self._working_directory = working_directory
        self._artifact_publisher = artifact_publisher
        self._completion_guard = _CompletionGuard(
            requires_external_owner=completion_requires_external_owner,
            external_owner=request.dependencies.get("external_terminal_owner"),
            verification_required_tool_names=verification_required_tool_names,
        )
        timeout = request.run_policy.deadline_s
        self._expires_at = None if timeout is None else time.monotonic() + timeout
        self.evidence_refs = tuple(
            dict.fromkeys((*runtime.canonical_evidence_refs, *initial_memory_evidence_refs))
        )

    async def dispatch(
        self,
        *,
        tool_calls: list[ToolCall],
        run_context: RunContext | None = None,
    ) -> list[ToolResultMessage]:
        observer = self._request.dependencies.get("tool_dispatch_observer")
        calls: list[tuple[ToolCall, ToolExecutionContext]] = []
        messages: list[ToolResultMessage | None] = []
        for call in tool_calls:
            if observer is not None:
                observer.on_call(call)
                terminal = observer.terminal_result(call)
                if terminal is not None:
                    messages.append(terminal)
                    continue
            messages.append(None)
            calls.append((call, self._context_for(call, run_context=run_context)))

        results = await self._executor.execute_many(calls)
        result_index = 0
        for index, message in enumerate(messages):
            if message is not None:
                continue
            call, context = calls[result_index]
            result = results[result_index]
            result_index += 1
            result = self._register_environment_memory_evidence(call, context, result)
            if observer is not None:
                observer.on_result(call, result)
            self._completion_guard.record(call.name, result)
            self._record_evidence(result)
            messages[index] = self._message(call, result)
        return [message for message in messages if message is not None]

    def _context_for(
        self,
        call: ToolCall,
        *,
        run_context: RunContext | None,
    ) -> ToolExecutionContext:
        tool = self._registry.get(call.name)
        stable_id = tool.stable_id if tool is not None else "homemaster.unknown.v1"
        deps = dict(getattr(self._settings, "application_services", {}))
        deps.update(self._request.dependencies)
        if run_context is not None:
            deps.update(run_context.deps)
        deps["task_state_store"] = self._task_state_store
        deps["task_completion_guard"] = self._completion_guard
        deps["current_tool_call_id"] = call.id
        _bind_backend(deps, self._request.profile, self._backend)
        tool_run_context = RunContext(
            session_id=self._runtime.session.session_id,
            run_id=self._run_id,
            turn_index=self._agent_state.turn_index,
            settings=self._settings,
            event_sink=self._event_sink,
            deps=deps,
            cancellation_token=self._runtime.cancellation,
        )
        feedback_by_call = deps.get("memory_feedback_context_by_tool_call_id", {})
        feedback_context = (
            feedback_by_call.get(call.id) if isinstance(feedback_by_call, dict) else None
        )
        metadata = {
            **deps,
            "services": deps,
            "tool_registry": self._registry,
            "run_context": tool_run_context,
            "backend": self._backend,
            "session_id": self._runtime.session.session_id,
            "run_id": self._run_id,
            "turn_index": self._agent_state.turn_index,
            "tool_call_id": call.id,
            "internal_tool_id": stable_id,
            "permission_subject": self._request.permission_subject,
            "cancellation": self._runtime.cancellation,
            "domain_observer": self._request.dependencies.get("domain_observer"),
            "deadline": self,
        }
        gateway_generation = self._request.metadata.get("gateway_generation")
        if (
            not isinstance(gateway_generation, bool)
            and isinstance(gateway_generation, int)
            and gateway_generation >= 1
        ):
            metadata["gateway_generation"] = gateway_generation
        if feedback_context is not None:
            metadata["memory_feedback_context"] = feedback_context
        return ToolExecutionContext(self._working_directory, metadata=metadata)

    def remaining_s(self) -> float | None:
        if self._expires_at is None:
            return None
        return max(0.0, self._expires_at - time.monotonic())

    @property
    def deadline(self) -> ApplicationToolExecutor:
        return self

    def _message(self, call: ToolCall, result: ToolResult) -> ToolResultMessage:
        canonical_result = result.canonical_result
        data = dict(result.metadata)
        if call.name in _MEMORY_TOOL_NAMES and data:
            model_payload = dict(data)
            if result.output:
                model_payload.setdefault("text", result.output)
            content = [
                ContentBlock(
                    text=json.dumps(
                        model_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            ]
            is_error = result.is_error
        elif canonical_result is not None:
            projected = canonical_result.to_message(tool_call_id=call.id, name=call.name)
            content = list(projected.content)
            is_error = projected.is_error
        else:
            model_text = result.output
            content = [ContentBlock(text=model_text)] if model_text else []
            for image in data.get("images", []):
                if isinstance(image, dict) and image.get("data_base64"):
                    content.append(
                        ContentBlock(
                            type="image",
                            source={
                                "type": "base64",
                                "media_type": image.get("media_type", "image/png"),
                                "data": image["data_base64"],
                            },
                        )
                    )
            is_error = result.is_error
        data.pop("images", None)
        data.pop("attachments", None)
        if self._artifact_publisher is not None:
            artifacts = self._artifact_publisher.publish(
                canonical_result or result,
                tenant_id=self._request.permission_subject.tenant_id,
                session_id=self._runtime.session.session_id,
                run_id=self._run_id,
            )
            if artifacts:
                data["artifacts"] = [dict(item) for item in artifacts]
        return ToolResultMessage(
            tool_call_id=call.id,
            name=call.name,
            content=content,
            is_error=is_error,
            data=data,
        )

    def _record_evidence(self, result: ToolResult) -> None:
        raw = result.metadata.get("evidence_refs", ())
        refs = [raw] if isinstance(raw, str) else list(raw) if isinstance(raw, list | tuple) else []
        if refs:
            self.evidence_refs = tuple(dict.fromkeys((*self.evidence_refs, *refs)))

    def _register_environment_memory_evidence(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
        result: ToolResult,
    ) -> ToolResult:
        if result.is_error or call.name in {
            "context_memory",
            "mindmemos_add",
            "mindmemos_search",
            "mindmemos_history",
            "mindmemos_update",
            "mindmemos_delete",
        }:
            return result
        if not bool(result.metadata.get("backend_attempted")):
            return result
        tool = self._registry.get(call.name)
        if tool is None:
            return result
        try:
            arguments = tool.input_model.model_validate(call.arguments)
        except Exception:
            return result
        verified = result.metadata.get("verification_status") == "passed"
        if not verified and not tool.is_read_only(arguments):
            return result
        ledger = context.services.get("memory_evidence_ledger")
        register = getattr(ledger, "register", None)
        if not callable(register):
            return result
        evidence = register(
            kind="environment_observation",
            tenant_id=self._request.permission_subject.tenant_id,
            session_id=self._runtime.session.session_id,
            run_id=self._run_id,
            turn_id=f"turn-{self._agent_state.turn_index}",
            tool_call_id=call.id,
            verification="passed" if verified else "read_observation",
        )
        metadata = dict(result.metadata)
        raw_refs = metadata.get("evidence_refs", ())
        refs = (
            [raw_refs]
            if isinstance(raw_refs, str)
            else list(raw_refs)
            if isinstance(raw_refs, (list, tuple))
            else []
        )
        metadata["evidence_refs"] = list(dict.fromkeys((*refs, evidence.ref)))
        return replace(result, metadata=metadata)


def _bind_backend(deps: dict[str, object], profile: str, backend: object | None) -> None:
    if backend is None:
        return
    deps.setdefault("backend", backend)
    if profile == "alfworld":
        deps.setdefault("alfworld_env", backend)
    elif profile == "coworker":
        deps.setdefault("coworker_backend", backend)


class _CompletionGuard:
    """Application-owned terminal policy exposed to the legacy task-state tool."""

    def __init__(
        self,
        *,
        requires_external_owner: bool,
        external_owner: object | None,
        verification_required_tool_names: frozenset[str],
    ) -> None:
        self._requires_external_owner = requires_external_owner
        self._external_owner = external_owner
        self._verification_required_tool_names = verification_required_tool_names
        self._verification: dict[str, bool] = {}

    def record(self, tool_name: str, result: ToolResult) -> None:
        if tool_name not in self._verification_required_tool_names:
            return
        self._verification[tool_name] = result.metadata.get("verification_status") == "passed"

    def __call__(self) -> ToolExecutionResult | None:
        if any(not passed for passed in self._verification.values()):
            message = "task completion is waiting for required verification"
            return ToolExecutionResult(
                status=ToolExecutionStatus.VERIFICATION_PENDING,
                error=ToolExecutionError(code="verification_pending", message=message),
                backend_attempted=False,
                verification=VerificationRecord(
                    status=VerificationStatus.PENDING,
                    detail=message,
                ),
            )
        if not self._requires_external_owner or _external_owner_succeeded(self._external_owner):
            return None
        message = "the external terminal owner has not reported success"
        return ToolExecutionResult(
            status=ToolExecutionStatus.VERIFICATION_PENDING,
            error=ToolExecutionError(code="external_terminal_pending", message=message),
            backend_attempted=False,
            verification=VerificationRecord(
                status=VerificationStatus.PENDING,
                detail=message,
            ),
        )


def _external_owner_succeeded(owner: object | None) -> bool:
    if owner is None:
        return False
    value = getattr(owner, "succeeded", None)
    if callable(value):
        value = value()
    if value is None:
        value = getattr(owner, "success", False)
    return bool(value)


__all__ = ["ApplicationToolExecutor"]
