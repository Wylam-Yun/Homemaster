"""Canonical V2.6 memory tools backed by application-owned services."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from homemaster.events.trace import append_jsonl_event
from homemaster.memory.add_queue import MemoryAddQueue
from homemaster.memory.evidence import MemoryEvidenceError, MemoryEvidenceLedger
from homemaster.memory.file_store import (
    FileMemoryError,
    FileMemoryOperation,
    FileMemoryState,
    FileMemoryStore,
)
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.memory.models import MEMORY_RECORD_ADAPTER, MemoryRecord, Subject
from homemaster.memory.serialization import serialize_record
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)

_REFERENCE = "homemaster.tools.memory_tools"
_READ_CAPABILITY = "tool.read"
_MUTATE_CAPABILITY = "tool.mutate"
_NonEmptyText = Annotated[str, Field(min_length=1)]
NativeMemoryType = Literal[
    "profile",
    "fact",
    "experience",
    "episodic",
    "tool_trace",
    "skill_candidate",
    "file_knowledge",
]
_NATIVE_MEMORY_TYPES = {
    "profile",
    "fact",
    "experience",
    "episodic",
    "tool_trace",
    "skill_candidate",
    "file_knowledge",
}


class MemoryToolServiceError(RuntimeError):
    def __init__(self, code: str, message: str, **details: object) -> None:
        self.code = code
        self.details = details
        super().__init__(message)


def _decode_record_object(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("record string must contain valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError("record string must decode to a JSON object")
    return decoded


MemoryRecordInput = Annotated[
    MemoryRecord,
    BeforeValidator(
        _decode_record_object,
        json_schema_input_type=MemoryRecord | str,
    ),
]


class _MemoryToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileMemoryOperationInput(_MemoryToolInput):
    action: Literal["add", "update", "delete"]
    content: str | None = None
    match: str | None = None


class FileMemoryInput(_MemoryToolInput):
    target: Literal["user", "memory"]
    action: Literal["add", "update", "delete"] | None = None
    content: str | None = None
    match: str | None = None
    operations: tuple[FileMemoryOperationInput, ...] | None = Field(default=None, min_length=1)


class AddMemoryInput(_MemoryToolInput):
    memory_type: Literal["fact", "procedure"]
    content: _NonEmptyText = Field(
        description=(
            "Exact memory text to persist. Preserve the submitted wording; do not add evidence refs, "
            "explanations, or a structured record."
        )
    )


class SearchMemoriesInput(_MemoryToolInput):
    query: _NonEmptyText
    memory_type: NativeMemoryType | None = Field(
        default=None,
        description=(
            "Optional native MindMemOS type: profile, fact, experience, episodic, "
            "tool_trace, skill_candidate, or file_knowledge. Omit to search all types."
        ),
    )
    limit: int = Field(default=5, ge=1, le=20)
    subject: Subject | None = None
    predicate: str | None = None
    entry_url: str | None = None
    name: str | None = None


class UpdateMemoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText
    record: MemoryRecordInput | None = Field(
        default=None,
        description=(
            "Complete replacement FactRecord or ProcedureRecord for a search result that "
            "contains a record field. Omit for a Vanilla result that contains only content."
        )
    )
    content: _NonEmptyText | None = Field(
        default=None,
        description=(
            "Complete replacement text for a Vanilla search result that contains content "
            "but no record. Omit for a structured FactRecord or ProcedureRecord."
        ),
    )
    @model_validator(mode="after")
    def _exactly_one_replacement(self) -> UpdateMemoryInput:
        if (self.record is None) == (self.content is None):
            raise ValueError("provide exactly one of record or content")
        return self


class MemoryHistoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText


class DeleteMemoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText


class FeedbackMemoryInput(_MemoryToolInput):
    feedback: _NonEmptyText = Field(
        description=(
            "The user's concrete correction, scope change, or instruction about "
            "remembered information. Preserve the user's actual meaning and include "
            "the corrected fact, applicable condition, or explicit obsolete/forget "
            "instruction. Do not submit only vague text such as 'that was wrong'."
        )
    )


class MemoryAuditExecutor:
    """Write one field-limited JSONL record around a canonical memory executor."""

    def __init__(self, operation: str, delegate: Any, input_model: type[_MemoryToolInput]) -> None:
        self.operation = operation
        self.delegate = delegate
        self.input_model = input_model

    def is_read_only(self, arguments: Mapping[str, object]) -> bool:
        dynamic = getattr(self.delegate, "is_read_only", None)
        return (
            bool(dynamic(arguments))
            if callable(dynamic)
            else self.operation
            not in {
                "mindmemos_add",
                "mindmemos_update",
                "mindmemos_delete",
                "mindmemos_feedback",
            }
        )

    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        started = time.monotonic()
        try:
            validated = self.input_model.model_validate(arguments)
        except ValidationError as exc:
            return _failure("memory_invalid_input", f"invalid memory tool input: {exc}")
        normalized_arguments = validated.model_dump(mode="python")
        result = await self.delegate.execute(normalized_arguments, context)
        path = context.services.get("memory_audit_path")
        if isinstance(path, Path):
            raw_id = normalized_arguments.get("memory_id")
            memory_id_hash = (
                hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
                if isinstance(raw_id, str) and raw_id
                else None
            )
            record = normalized_arguments.get("record")
            memory_type = (
                record.get("memory_type")
                if isinstance(record, Mapping)
                else normalized_arguments.get("memory_type")
            )
            append_jsonl_event(
                path,
                event="memory_operation",
                payload={
                    "operation": self.operation,
                    "memory_type": memory_type if memory_type in {"fact", "procedure"} else None,
                    "memory_id_hash": memory_id_hash,
                    "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                    "return_status": result.status.value,
                    "terminal_verified": bool(result.data.get("verified_terminal_state")),
                    "error_code": result.error.code if result.error is not None else None,
                    "session_id": context.session_id,
                    "run_id": context.run_id,
                    "tool_call_id": context.tool_call_id,
                },
            )
        return result


class FileMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        target = _required_string(arguments, "target")
        action = arguments.get("action")
        raw_operations = arguments.get("operations")
        try:
            store = _service(context, "file_memory_store", FileMemoryStore)
            if not _has_capability(context, _MUTATE_CAPABILITY):
                return _failure(
                    "memory_permission_denied",
                    "memory mutation requires tool.mutate",
                )
            if raw_operations is not None:
                if action is not None or any(
                    arguments.get(key) is not None for key in ("content", "match")
                ):
                    return _failure(
                        "memory_invalid_input",
                        "operations cannot be combined with a single operation",
                    )
                operations = _file_operations(raw_operations)
                state = store.apply(target, operations)
                return _file_success("batch", state, attempted=True)
            if action not in {"add", "update", "delete"}:
                return _failure("memory_invalid_input", "a supported action is required")
            state = store.apply(
                target,
                [
                    FileMemoryOperation(
                        action=action,
                        content=_optional_string(arguments, "content"),
                        match=_optional_string(arguments, "match"),
                    )
                ],
            )
            return _file_success(action, state, attempted=True)
        except FileMemoryError as exc:
            return _failure(exc.code, str(exc), details=exc.details, attempted=True)
        except MemoryToolServiceError as exc:
            return _store_failure(exc, attempted=True)


class AddMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        raw_content = arguments.get("content")
        content = raw_content if isinstance(raw_content, str) else ""
        memory_type = arguments.get("memory_type")
        if not content.strip() or memory_type not in {"fact", "procedure"}:
            return _failure("memory_invalid_input", "content and a supported memory_type are required")
        evidence = _validated_untyped_evidence(context)
        if isinstance(evidence, ToolExecutionResult):
            return evidence
        try:
            queue = _service(context, "memory_add_queue", MemoryAddQueue)
            receipt = await queue.enqueue(
                content=content,
                memory_type=memory_type,
                provenance_seq=max(entry.provenance_seq for entry in evidence),
                evidence_kind=max(evidence, key=lambda item: item.provenance_seq).kind,
                context=_mindmemos_context(context),
                run_id=context.run_id,
            )
        except Exception as exc:
            return _failure("memory_backend_unavailable", str(exc))
        return _accepted("add", receipt.job_id)


class SearchMemoriesExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        query = _required_string(arguments, "query")
        limit = arguments.get("limit", 5)
        memory_type = arguments.get("memory_type")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            return _failure("memory_invalid_input", "limit must be between 1 and 20")
        if memory_type is not None and memory_type not in _NATIVE_MEMORY_TYPES:
            return _failure("memory_invalid_input", "invalid memory_type")
        try:
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            memory_context = _mindmemos_context(context)
            filters = {"mem_type": memory_type} if memory_type is not None else None
            result = await store.search(
                query,
                memory_context,
                top_k=limit,
                search_pipeline="vanilla",
                filters=filters,
            )
            records: list[dict[str, object]] = []
            visible_hits: list[Any] = []
            diagnostics: list[dict[str, object]] = []
            for hit in result.memories:
                raw = await store.get_raw(hit.id, memory_context)
                parsed = _mindmemos_record(raw)
                if parsed is None:
                    flat_payload = _flat_memory_payload(hit, raw, arguments)
                    if flat_payload is not None:
                        records.append(flat_payload)
                        visible_hits.append(hit)
                        continue
                    if _is_active_native_memory(raw):
                        continue
                    diagnostics.append(
                        {
                            "code": "memory_record_corrupt",
                            "memory_id_hash": hashlib.sha256(hit.id.encode("utf-8")).hexdigest()[
                                :16
                            ],
                            "match_sources": ["semantic"],
                        }
                    )
                    continue
                if not _record_matches(parsed, arguments):
                    continue
                records.append(_mindmemos_payload(hit, raw, parsed))
                visible_hits.append(
                    hit.model_copy(
                        update={"structured_record": parsed.model_dump(mode="json")},
                        deep=True,
                    )
                    if callable(getattr(hit, "model_copy", None))
                    else hit
                )
        except Exception as exc:
            return _failure("memory_backend_unavailable", str(exc))
        run_context = context.metadata.get("run_context")
        deps = getattr(run_context, "deps", None)
        if isinstance(deps, dict):
            by_call = deps.setdefault("recalled_memories_by_tool_call_id", {})
            by_call[context.tool_call_id] = tuple(
                hit.model_copy(deep=True)
                if callable(getattr(hit, "model_copy", None))
                else hit
                for hit in visible_hits
            )
        return _success(
            "search",
            {
                "records": records,
                "count": len(records),
                "diagnostics": diagnostics,
                "verified_terminal_state": True,
            },
        )


class FeedbackMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        from homemaster.memory.feedback_context import (
            FeedbackContextSnapshot,
            snapshot_to_dialogue_messages,
        )

        feedback = _required_string(arguments, "feedback")
        await _emit_feedback_event(
            context,
            "memory.feedback.explicit.started",
            {"request_id": context.tool_call_id},
        )
        snapshot = context.metadata.get("memory_feedback_context")
        if not isinstance(snapshot, FeedbackContextSnapshot) or not snapshot.messages:
            await _emit_feedback_event(
                context,
                "memory.feedback.explicit.failed",
                {"request_id": context.tool_call_id, "error": "context_missing"},
            )
            return _failure(
                "memory_feedback_context_missing",
                "feedback requires the exact successful provider context",
            )
        try:
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            memory_context = _mindmemos_context(context)
            verified_recalled = []
            for item in snapshot.recalled_memories:
                raw = await store.get_raw(item.id, memory_context)
                if (
                    raw is None
                    or getattr(raw, "project_id", None) != memory_context.project_id
                    or getattr(raw, "user_id", memory_context.user_id)
                    != memory_context.user_id
                    or getattr(raw, "status", None) != "active"
                    or getattr(raw, "content", None) != item.memory
                ):
                    await _emit_feedback_event(
                        context,
                        "memory.feedback.explicit.failed",
                        {
                            "request_id": context.tool_call_id,
                            "error": "recalled_memory_invalid",
                            "raw_memory_id": item.id,
                        },
                    )
                    return _failure(
                        "memory_feedback_recalled_memory_invalid",
                        "feedback context contains an unavailable or changed raw memory",
                    )
                record = _mindmemos_record(raw)
                verified_recalled.append(
                    item.model_copy(
                        update={
                            "structured_record": (
                                record.model_dump(mode="json") if record is not None else None
                            )
                        },
                        deep=True,
                    )
                    if callable(getattr(item, "model_copy", None))
                    else item
                )
            evidence = _validated_untyped_evidence(context)
            if isinstance(evidence, ToolExecutionResult):
                await _emit_feedback_event(
                    context,
                    "memory.feedback.explicit.failed",
                    {"request_id": context.tool_call_id, "error": "evidence_missing"},
                )
                return evidence
            result = await store.feedback_explicit(
                feedback=feedback,
                messages=snapshot_to_dialogue_messages(snapshot),
                recalled_memories=verified_recalled,
                provenance_seq=max(item.provenance_seq for item in evidence),
                context=memory_context,
            )
            receipts = []
            failed = result.status != "ok"
            for action in result.actions:
                verified = await _verify_feedback_action(store, memory_context, action)
                failed = failed or action.status != "ok" or not verified
                receipts.append(
                    {
                        **action.model_dump(mode="json"),
                        "terminal_verified": verified,
                    }
                )
            if failed:
                await _emit_feedback_event(
                    context,
                    "memory.feedback.explicit.failed",
                    {
                        "request_id": context.tool_call_id,
                        "action_count": len(receipts),
                        "actions": receipts,
                    },
                )
                return _failure(
                    "memory_feedback_failed",
                    result.message or "one or more feedback actions failed verification",
                    details={"actions": receipts},
                    attempted=True,
                )
            await _emit_feedback_event(
                context,
                "memory.feedback.explicit.completed",
                {
                    "request_id": context.tool_call_id,
                    "action_count": len(receipts),
                    "actions": receipts,
                },
            )
            return _success(
                "feedback",
                {
                    "actions": receipts,
                    "action_count": len(receipts),
                    "verified_terminal_state": True,
                },
                attempted=True,
            )
        except Exception as exc:
            await _emit_feedback_event(
                context,
                "memory.feedback.explicit.failed",
                {
                    "request_id": context.tool_call_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            return _failure("memory_backend_unavailable", str(exc), attempted=True)


async def _verify_feedback_action(store: Any, context: Any, action: Any) -> bool:
    if action.status != "ok":
        return False
    if action.action == "noop":
        return True
    if action.action == "add":
        raw = await store.get_raw(action.result_memory_id, context)
        return raw is not None and getattr(raw, "status", None) == "active"
    if action.action == "delete":
        raw = await store.get_raw(action.target_memory_id, context)
        return raw is not None and getattr(raw, "status", None) == "archived"
    if action.action == "update":
        old = await store.get_raw(action.target_memory_id, context)
        new = await store.get_raw(action.result_memory_id, context)
        old_record = _mindmemos_record(old)
        expected_record = (
            _record(action.replacement_record, None)
            if action.replacement_record is not None
            else None
        )
        structured_terminal_ok = True
        if old_record is not None:
            if expected_record is None or isinstance(expected_record, ToolExecutionResult):
                structured_terminal_ok = False
            else:
                expected_content = serialize_record(expected_record, provenance_seq=0).text
                structured_terminal_ok = bool(
                    _mindmemos_record(new) == expected_record
                    and getattr(new, "content", None) == expected_content
                )
        return bool(
            old is not None
            and getattr(old, "status", None) == "archived"
            and new is not None
            and getattr(new, "status", None) == "active"
            and getattr(new, "content", None) == action.after_content
            and structured_terminal_ok
            and await store.has_memory_lineage(
                source_memory_id=action.result_memory_id,
                target_memory_id=action.target_memory_id,
                relationship="DERIVED_FROM",
                context=context,
            )
        )
    return False


async def _emit_feedback_event(
    context: ToolExecutionContext, event_type: str, payload: dict[str, Any]
) -> None:
    from homemaster.events.runtime_events import RuntimeEvent

    run_context = context.metadata.get("run_context")
    sink = getattr(run_context, "event_sink", None)
    if sink is None:
        return
    event = RuntimeEvent(
        type=event_type,
        session_id=context.session_id,
        run_id=context.run_id,
        turn_index=context.turn_index,
        tool_call_id=context.tool_call_id,
        name="mindmemos_feedback",
        payload=payload,
    )
    try:
        aemit = getattr(sink, "aemit", None)
        if callable(aemit):
            await aemit(event)
            return
        emitted = sink.emit(event)
        if hasattr(emitted, "__await__"):
            await emitted
    except Exception:
        return


def _mindmemos_context(context: ToolExecutionContext) -> Any:
    from mindmemos.typing import MemoryRequestContext

    tenant_id = context.permission_subject.tenant_id
    return MemoryRequestContext(
        request_id=context.tool_call_id,
        account_id=tenant_id,
        project_id=tenant_id,
        api_key_uuid="embedded-local",
        user_id=tenant_id,
        app_id="homemaster",
        session_id=context.session_id,
        agent_id="homemaster",
    )


def _mindmemos_record(raw: Any) -> MemoryRecord | None:
    record_json = _mindmemos_request_metadata(raw).get("record_json")
    if not isinstance(record_json, str):
        return None
    try:
        decoded = json.loads(record_json)
        return MEMORY_RECORD_ADAPTER.validate_python(decoded)
    except (json.JSONDecodeError, ValidationError):
        return None


def _mindmemos_request_metadata(raw: Any) -> dict[str, Any]:
    metadata = getattr(raw, "metadata", None)
    if not isinstance(metadata, Mapping):
        return {}
    nested = metadata.get("request_metadata")
    if isinstance(nested, Mapping):
        record_metadata = nested.get("record_metadata")
        if isinstance(record_metadata, Sequence) and not isinstance(record_metadata, (str, bytes)):
            for item in record_metadata:
                if isinstance(item, Mapping) and "record_json" in item:
                    return dict(item)
        return dict(nested)
    return dict(metadata)


def _record_matches(record: MemoryRecord, arguments: Mapping[str, object]) -> bool:
    memory_type = arguments.get("memory_type")
    native_record_type = "experience" if record.memory_type == "procedure" else "fact"
    if memory_type is not None and native_record_type != memory_type:
        return False
    if record.memory_type == "fact":
        subject = _optional_mapping(arguments, "subject")
        if subject is not None:
            expected = {key: value for key, value in subject.items() if value is not None}
            actual = record.subject.model_dump(mode="python")
            if any(actual.get(key) != value for key, value in expected.items()):
                return False
        predicate = _optional_string(arguments, "predicate")
        return predicate is None or record.predicate == predicate
    entry_url = _optional_string(arguments, "entry_url")
    name = _optional_string(arguments, "name")
    return (entry_url is None or record.entry_url == entry_url) and (
        name is None or record.name == name
    )


def _mindmemos_payload(hit: Any, raw: Any, record: MemoryRecord) -> dict[str, object]:
    created_at = getattr(raw, "created_at", None)
    updated_at = getattr(raw, "update_at", None)
    return {
        "memory_id": hit.id,
        "memory_type": getattr(raw, "mem_type", None)
        or ("experience" if record.memory_type == "procedure" else "fact"),
        "record": record.model_dump(mode="json"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
        "score": getattr(hit, "score", None),
        "match_sources": ["semantic"],
        "verified_terminal_state": True,
    }


def _flat_memory_payload(
    hit: Any, raw: Any, arguments: Mapping[str, object]
) -> dict[str, object] | None:
    """Project active record-free native MindMemOS memories as exact content."""
    metadata = _mindmemos_request_metadata(raw)
    native_type = getattr(raw, "mem_type", None)
    if (
        raw is None
        or native_type not in _NATIVE_MEMORY_TYPES
        or getattr(raw, "status", "active") != "active"
        or "record_json" in metadata
    ):
        return None
    content = getattr(raw, "content", None)
    if not isinstance(content, str) or not content.strip():
        return None
    if arguments.get("memory_type") not in {None, native_type}:
        return None
    if any(arguments.get(key) is not None for key in ("subject", "predicate", "entry_url", "name")):
        return None

    source = {
        key: metadata[key]
        for key in (
            "source_type",
            "source_session_id",
            "input_hash",
            "trace_schema_version",
            "trace_hash",
            "extractor_version",
            "provenance_seq",
            "evidence_kind",
            "homemaster_add_mode",
        )
        if key in metadata
    }
    created_at = getattr(raw, "created_at", None)
    updated_at = getattr(raw, "update_at", None)
    return {
        "memory_id": hit.id,
        "memory_type": native_type,
        "content": content,
        "source": source,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
        "score": getattr(hit, "score", None),
        "match_sources": ["semantic"],
        "verified_terminal_state": True,
    }


def _is_active_native_memory(raw: Any) -> bool:
    if raw is None or getattr(raw, "status", "active") != "active":
        return False
    if getattr(raw, "mem_type", None) not in _NATIVE_MEMORY_TYPES:
        return False
    content = getattr(raw, "content", None)
    if not isinstance(content, str) or not content.strip():
        return False
    return "record_json" not in _mindmemos_request_metadata(raw)


class UpdateMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        try:
            memory_id = _required_string(arguments, "memory_id")
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            memory_context = _mindmemos_context(context)
            current = await store.get_raw(memory_id, memory_context)
            if current is None:
                return _failure("memory_not_found", "memory id was not found", attempted=True)
            metadata = _mindmemos_request_metadata(current)
            if "record_json" not in metadata:
                return await self._update_vanilla(
                    arguments,
                    context,
                    store=store,
                    memory_context=memory_context,
                    current=current,
                )
            current_record = _mindmemos_record(current)
            if current_record is None:
                return _failure(
                    "memory_record_corrupt",
                    "record_json exists but is not a valid HomeMaster record",
                    attempted=True,
                )
            return await self._update_structured(
                arguments,
                context,
                store=store,
                memory_context=memory_context,
                current=current,
                current_record=current_record,
                metadata=metadata,
            )
        except Exception as exc:
            return _failure("memory_outcome_unknown", str(exc), attempted=True)

    async def _update_structured(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
        *,
        store: EmbeddedMindMemOS,
        memory_context: Any,
        current: Any,
        current_record: MemoryRecord,
        metadata: Mapping[str, Any],
    ) -> ToolExecutionResult:
        if arguments.get("record") is None:
            return _failure(
                "memory_update_mode_mismatch",
                "structured memory requires a complete replacement record",
                attempted=True,
            )
        parsed = _record(arguments.get("record"), None)
        if isinstance(parsed, ToolExecutionResult):
            return parsed
        evidence = _validated_evidence(context, parsed)
        if isinstance(evidence, ToolExecutionResult):
            return evidence
        provenance_seq = max(entry.provenance_seq for entry in evidence)
        if current_record.memory_type != parsed.memory_type:
            return _failure("memory_conflict", "memory type cannot change", attempted=True)
        current_serialized = serialize_record(
            current_record,
            provenance_seq=int(metadata.get("provenance_seq", 0)),
        )
        replacement = serialize_record(parsed, provenance_seq=provenance_seq)
        if current_serialized.dedupe_key != replacement.dedupe_key:
            return _failure(
                "memory_conflict",
                "structured memory identity cannot change during update",
                attempted=True,
            )
        if provenance_seq <= int(metadata.get("provenance_seq", 0)):
            return _failure(
                "memory_stale_observation",
                "evidence is not newer",
                attempted=True,
            )
        result = await store.update_versioned(
            memory_id=current.memory_id,
            content=replacement.text,
            metadata={
                **replacement.metadata,
                "homemaster_memory_type": parsed.memory_type,
            },
            context=memory_context,
        )
        if result.status != "ok" or not isinstance(result.memory_id, str):
            return _failure(
                "memory_backend_rejected",
                result.message or "MindMemOS versioned update was rejected",
                attempted=True,
            )
        old = await store.get_raw(current.memory_id, memory_context)
        new = await store.get_raw(result.memory_id, memory_context)
        lineage = await store.has_memory_lineage(
            source_memory_id=result.memory_id,
            target_memory_id=current.memory_id,
            relationship="DERIVED_FROM",
            context=memory_context,
        )
        if not (
            old is not None
            and getattr(old, "status", None) == "archived"
            and new is not None
            and getattr(new, "status", None) == "active"
            and _mindmemos_record(new) == parsed
            and lineage
        ):
            return _failure(
                "memory_outcome_unknown",
                "versioned update terminal state could not be verified",
                attempted=True,
            )
        item = _mindmemos_raw_payload(new, parsed)
        item.update(
            {
                "update_mode": "structured",
                "previous_memory_id": current.memory_id,
                "lineage": "DERIVED_FROM",
            }
        )
        return _success("update", item, attempted=True)

    async def _update_vanilla(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
        *,
        store: EmbeddedMindMemOS,
        memory_context: Any,
        current: Any,
    ) -> ToolExecutionResult:
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            return _failure(
                "memory_update_mode_mismatch",
                "Vanilla memory requires replacement content",
                attempted=True,
            )
        evidence = _validated_untyped_evidence(context)
        if isinstance(evidence, ToolExecutionResult):
            return evidence
        result = await store.update(current.memory_id, content, memory_context)
        if result.status != "ok":
            return _failure(
                "memory_backend_rejected",
                result.message or "MindMemOS update was rejected",
                attempted=True,
            )
        updated = await store.get_raw(current.memory_id, memory_context)
        if not (
            updated is not None
            and getattr(updated, "status", None) == "active"
            and getattr(updated, "content", None) == content
        ):
            return _failure(
                "memory_outcome_unknown",
                "Vanilla update terminal state could not be verified",
                attempted=True,
            )
        return _success(
            "update",
            {
                "memory_id": current.memory_id,
                "content": content,
                "update_mode": "vanilla",
                "verified_terminal_state": True,
            },
            attempted=True,
        )


class MemoryHistoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        memory_id = _required_string(arguments, "memory_id")
        try:
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            versions = await store.get_history(memory_id, _mindmemos_context(context))
        except Exception as exc:
            return _failure("memory_backend_unavailable", str(exc), attempted=True)
        if not versions:
            return _failure("memory_not_found", "memory id was not found", attempted=True)
        projected: list[dict[str, object]] = []
        for raw in versions:
            raw_metadata = _mindmemos_request_metadata(raw)
            record = _mindmemos_record(raw)
            if "record_json" in raw_metadata and record is None:
                return _failure(
                    "memory_record_corrupt",
                    "version history contains an invalid HomeMaster record",
                    attempted=True,
                )
            created_at = getattr(raw, "created_at", None)
            updated_at = getattr(raw, "update_at", None)
            item: dict[str, object] = {
                "memory_id": raw.memory_id,
                "status": getattr(raw, "status", None),
                "content": getattr(raw, "content", None),
                "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
                "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
            }
            if record is not None:
                item["record"] = record.model_dump(mode="json")
            projected.append(item)
        return _success(
            "history",
            {
                "memory_id": memory_id,
                "versions": projected,
                "version_count": len(projected),
                "verified_terminal_state": True,
            },
            attempted=True,
        )


class DeleteMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        memory_id = _required_string(arguments, "memory_id")
        try:
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            result = await store.delete(memory_id, _mindmemos_context(context))
        except Exception as exc:
            return _failure("memory_backend_unavailable", str(exc), attempted=True)
        if result.status != "ok":
            return _failure(
                "memory_not_found",
                result.message or "memory id was not found",
                attempted=True,
            )
        return _success(
            "delete",
            {
                "memory_id": memory_id,
                "verified_terminal_state": True,
            },
            attempted=True,
        )


def _mindmemos_raw_payload(raw: Any, record: MemoryRecord) -> dict[str, object]:
    created_at = getattr(raw, "created_at", None)
    updated_at = getattr(raw, "update_at", None)
    return {
        "memory_id": raw.memory_id,
        "memory_type": record.memory_type,
        "record": record.model_dump(mode="json"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
        "score": None,
        "match_sources": [],
        "verified_terminal_state": True,
    }


def _validated_evidence(
    context: ToolExecutionContext, record: MemoryRecord
) -> tuple[Any, ...] | ToolExecutionResult:
    subject = context.permission_subject
    try:
        evidence = _service(context, "memory_evidence_ledger", MemoryEvidenceLedger).for_scope(
            kind=record.source,
            tenant_id=subject.tenant_id,
            session_id=context.session_id,
            run_id=context.run_id,
            turn_id=f"turn-{context.turn_index}",
        )
        if not evidence:
            return _failure("memory_evidence_missing", "current execution has no matching evidence")
        if record.memory_type == "procedure":
            if len(evidence) < len(record.steps) + 1:
                return _failure(
                    "memory_evidence_invalid",
                    "procedure evidence must cover every ordered step and final success",
                )
            sequences = [item.provenance_seq for item in evidence]
            if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
                return _failure(
                    "memory_evidence_invalid",
                    "procedure evidence must be unique and ordered",
                )
        return evidence
    except MemoryEvidenceError as exc:
        return _failure(exc.code, str(exc))


def _validated_untyped_evidence(
    context: ToolExecutionContext,
) -> tuple[Any, ...] | ToolExecutionResult:
    subject = context.permission_subject
    try:
        ledger = _service(context, "memory_evidence_ledger", MemoryEvidenceLedger)
        evidence = tuple(
            item
            for kind in ("user_statement", "environment_observation")
            for item in ledger.for_scope(
                kind=kind,
                tenant_id=subject.tenant_id,
                session_id=context.session_id,
                run_id=context.run_id,
                turn_id=f"turn-{context.turn_index}",
            )
        )
        evidence = tuple(sorted(evidence, key=lambda item: item.provenance_seq))
        return (
            evidence
            if evidence
            else _failure(
                "memory_evidence_missing", "current execution has no current-scope evidence"
            )
        )
    except MemoryEvidenceError as exc:
        return _failure(exc.code, str(exc))


def _record(raw: object, declared_type: object) -> MemoryRecord | ToolExecutionResult:
    if not isinstance(raw, Mapping):
        return _failure("memory_invalid_input", "record must be an object")
    try:
        record = MEMORY_RECORD_ADAPTER.validate_python(dict(raw))
    except ValidationError as exc:
        return _failure("memory_invalid_input", f"invalid memory record: {exc}")
    if declared_type is not None and record.memory_type != declared_type:
        return _failure("memory_invalid_input", "memory_type does not match record")
    return record


def _file_operations(raw: object) -> tuple[FileMemoryOperation, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise FileMemoryError("memory_invalid_input", "operations must be a non-empty array")
    operations: list[FileMemoryOperation] = []
    for item in raw:
        if not isinstance(item, Mapping) or item.get("action") not in {"add", "update", "delete"}:
            raise FileMemoryError("memory_invalid_input", "invalid batch operation")
        operations.append(
            FileMemoryOperation(
                action=item["action"],
                content=_optional_string(item, "content"),
                match=_optional_string(item, "match"),
            )
        )
    return tuple(operations)


def _file_success(
    operation: str, state: FileMemoryState, *, attempted: bool = False
) -> ToolExecutionResult:
    return _success(
        operation,
        {
            "target": state.target,
            "entries": list(state.entries),
            "entry_count": len(state.entries),
            "usage": state.usage,
            "limit": state.limit,
            "blocked_entries": list(state.blocked_entries),
            "verified_terminal_state": True,
        },
        attempted=attempted,
    )


def _success(
    operation: str, data: Mapping[str, object], *, attempted: bool = False
) -> ToolExecutionResult:
    payload = {"success": True, "operation": operation, **data}
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=f"memory {operation} succeeded",
        data=payload,
        backend_attempted=attempted,
    )


def _accepted(operation: str, job_id: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        text=f"memory {operation} accepted",
        data={
            "success": True,
            "operation": operation,
            "status": "accepted",
            "job_id": job_id,
            "verified_terminal_state": False,
        },
        backend_attempted=True,
    )


def _store_failure(exc: MemoryToolServiceError, *, attempted: bool = False) -> ToolExecutionResult:
    return _failure(exc.code, str(exc), details=exc.details, attempted=attempted)


def _failure(
    code: str,
    message: str,
    *,
    details: Mapping[str, object] | None = None,
    attempted: bool = False,
) -> ToolExecutionResult:
    status = (
        ToolExecutionStatus.OUTCOME_UNKNOWN
        if code == "memory_outcome_unknown"
        else ToolExecutionStatus.FAILURE
    )
    from homemaster.tools.contracts import OutcomeCertainty

    return ToolExecutionResult(
        status=status,
        text=message,
        data={"success": False},
        error=ToolExecutionError(code=code, message=message, details=details or {}),
        retryable=False,
        outcome_certainty=(
            OutcomeCertainty.UNKNOWN
            if status is ToolExecutionStatus.OUTCOME_UNKNOWN
            else OutcomeCertainty.CONFIRMED
        ),
        backend_attempted=attempted,
    )


def _service(context: ToolExecutionContext, name: str, expected: type[Any]) -> Any:
    value = context.services.get(name)
    if not isinstance(value, expected):
        raise MemoryToolServiceError("memory_backend_unavailable", f"{name} is unavailable")
    return value


def _has_capability(context: ToolExecutionContext, capability: str) -> bool:
    return capability in tuple(context.permission_subject.capabilities)


def _required_string(arguments: Mapping[str, object], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) else ""


def _optional_string(arguments: Mapping[str, object], key: str) -> str | None:
    value = arguments.get(key)
    return value if isinstance(value, str) else None


def _optional_mapping(arguments: Mapping[str, object], key: str) -> dict[str, object] | None:
    value = arguments.get(key)
    return dict(value) if isinstance(value, Mapping) else None


def _definition(
    name: str,
    description: str,
    input_model: type[BaseModel],
    *,
    mutating: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        internal_id=f"homemaster.memory.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=_inline_local_refs(input_model.model_json_schema()),
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(
            execution_proof=ExecutionProof.STRUCTURED_RECEIPT if mutating else ExecutionProof.NONE
        ),
        provenance=ToolProvenance(source="homemaster", reference=f"{_REFERENCE}:{name}"),
        version="2.1.0",
        concurrency_policy=ConcurrencyPolicy.SERIALIZED,
        state_effects=("memory.write",) if mutating else ("read",),
        required_capabilities=(
            (_READ_CAPABILITY, _MUTATE_CAPABILITY) if mutating else (_READ_CAPABILITY,)
        ),
    )


def _inline_local_refs(schema: Mapping[str, object]) -> dict[str, object]:
    """Inline Pydantic's local refs for provider-facing tool schemas."""

    definitions = schema.get("$defs", {})
    if not isinstance(definitions, Mapping):
        return dict(schema)

    def expand(value: object, stack: tuple[str, ...] = ()) -> object:
        if isinstance(value, Mapping):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.removeprefix("#/$defs/")
                target = definitions.get(name)
                if isinstance(target, Mapping) and name not in stack:
                    expanded = expand(target, (*stack, name))
                    assert isinstance(expanded, dict)
                    siblings = {
                        key: expand(item, stack) for key, item in value.items() if key != "$ref"
                    }
                    return {**expanded, **siblings}
            result: dict[str, object] = {}
            for key, item in value.items():
                if key == "$defs":
                    continue
                if key == "discriminator" and isinstance(item, Mapping):
                    property_name = item.get("propertyName")
                    if isinstance(property_name, str):
                        result[key] = {"propertyName": property_name}
                    continue
                result[str(key)] = expand(item, stack)
            return result
        if isinstance(value, tuple | list):
            return [expand(item, stack) for item in value]
        return value

    expanded = expand(schema)
    assert isinstance(expanded, dict)
    return expanded


def build_memory_tools() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            _definition(
                "context_memory",
                "Save compact, high-signal notes that are injected into future sessions. Use target='user' for the user's identity, preferences, communication style, and stable habits. Use target='memory' for recent decisions, results, and unfinished work that should carry across sessions. Do not use this for searchable external facts, procedures, or past task experiences; use the MindMemOS tools for those.",
                FileMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("context_memory", FileMemoryExecutor(), FileMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_add",
                "Queue a verified external fact or reusable procedure for searchable long-term memory. A successful call returns an accepted job ID; persistence completes asynchronously and immediate search may not see it yet. Use this for stable information learned from the environment, such as an object location, device state, or a procedure that has actually succeeded. Do not use it for user preferences, temporary task progress, or unverified guesses.",
                AddMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("mindmemos_add", AddMemoryExecutor(), AddMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_search",
                "Search long-term memory by meaning across native MindMemOS types: profiles, facts, experiences, episodes, tool traces, skill candidates, and file knowledge. Use it when the current request may benefit from prior knowledge, previous attempts, or reusable tool behavior. Search again with different wording or follow-up queries when earlier results reveal useful clues.",
                SearchMemoriesInput,
            ),
            MemoryAuditExecutor("mindmemos_search", SearchMemoriesExecutor(), SearchMemoriesInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_history",
                "Read every available version of one long-term memory by exact memory ID. Returns the active version and archived ancestors so you can explain what changed or recover an earlier value. Take the ID from mindmemos_search or a previous update result.",
                MemoryHistoryInput,
            ),
            MemoryAuditExecutor(
                "mindmemos_history",
                MemoryHistoryExecutor(),
                MemoryHistoryInput,
            ),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_update",
                "Replace an existing long-term memory by exact ID. For a search result containing record, send a complete corrected record; HomeMaster creates a linked structured version. For a Vanilla result containing only content, send complete replacement content; HomeMaster updates that ID in place. Never send both record and content. Take the memory ID from mindmemos_search.",
                UpdateMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("mindmemos_update", UpdateMemoryExecutor(), UpdateMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_delete",
                "Delete one long-term memory by its exact ID. Use this when the user asks to forget it or when the memory is confirmed wrong, duplicated, or permanently obsolete. Take the memory ID from mindmemos_search; prefer mindmemos_update when the information has merely changed.",
                DeleteMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("mindmemos_delete", DeleteMemoryExecutor(), DeleteMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_feedback",
                "Review concrete user feedback about long-term memory and let MindMemOS decide whether to add a new memory, create a corrected version of an existing memory, archive an incorrect memory, or leave memory unchanged. Use this when the user has corrected a fact, changed the scope of a preference or procedure, or said that remembered information is outdated, but the correct memory action is not already determined by one exact memory ID and one complete replacement record. Pass the user's concrete correction, scope change, or instruction. Do not use this when you already have an exact memory ID and a complete replacement record; use mindmemos_update instead. Do not use this when the user explicitly asks to forget one exact memory; use mindmemos_delete instead. Do not use it for a vague complaint with no correction, ordinary task failure, or praise with no requested memory change.",
                FeedbackMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor(
                "mindmemos_feedback",
                FeedbackMemoryExecutor(),
                FeedbackMemoryInput,
            ),
        ),
    )


__all__ = ["build_memory_tools"]
