"""Canonical memory tools backed by the MindMemOS client facade (V3.8)."""

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
from homemaster.memory.file_store import (
    FileMemoryError,
    FileMemoryOperation,
    FileMemoryState,
    FileMemoryStore,
)
from homemaster.memory.mindmemos_client import (
    MindMemOSClient,
    MindMemOSClientError,
    OperationConflictError,
    OperationNotFoundError,
    OutcomeUnknownError,
)
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
_CONTEXT_MEMORY_DESCRIPTION = (
    "Save compact, high-signal notes that are injected into future sessions. "
    "Use target='user' for the user's identity, preferences, communication "
    "style, and stable habits. Use target='memory' for recent decisions, "
    "results, and unfinished work that should carry across sessions. Do not "
    "use this for searchable external facts, procedures, or past task "
    "experiences."
)
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


JsonObjectInput = Annotated[
    dict[str, Any],
    BeforeValidator(
        _decode_record_object,
        json_schema_input_type=dict[str, Any],
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


class SearchMemoriesInput(_MemoryToolInput):
    query: _NonEmptyText
    memory_type: NativeMemoryType | None = Field(
        default=None,
        description=(
            "Optional memory type filter: profile, fact, experience, episodic, "
            "tool_trace, skill_candidate, or file_knowledge. Omit to search all types."
        ),
    )
    limit: int = Field(default=5, ge=1, le=20)


class UpdateMemoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText
    record: JsonObjectInput | None = Field(
        default=None,
        description=(
            "Replacement structured fields for a memory whose content is a typed "
            "record. Omit for a plain-text memory that contains only content."
        ),
    )
    content: _NonEmptyText | None = Field(
        default=None,
        description=(
            "Complete replacement text for a plain memory that contains content "
            "but no structured record. Omit for a structured fields update."
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
            client = _service(context, "mindmemos_client", MindMemOSClient)
            receipt = await client.search(
                query,
                session_id=context.session_id,
                top_k=limit,
                filters={"mem_type": memory_type} if memory_type is not None else None,
            )
        except MindMemOSClientError as exc:
            return _failure("memory_backend_unavailable", str(exc))
        except MemoryToolServiceError as exc:
            return _store_failure(exc)
        records: list[dict[str, object]] = [
            {
                "memory_id": hit.id,
                "memory_type": hit.memory_type,
                "memory": hit.memory,
                "score": hit.score,
                "metadata": dict(hit.metadata),
            }
            for hit in receipt.hits
        ]
        run_context = context.metadata.get("run_context")
        deps = getattr(run_context, "deps", None)
        if isinstance(deps, dict):
            by_call = deps.setdefault("recalled_memories_by_tool_call_id", {})
            by_call[context.tool_call_id] = tuple(receipt.hits)
        return _success(
            "search",
            {
                "records": records,
                "count": len(records),
                "search_record_id": receipt.search_record_id,
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
        operation_id = (
            f"explicitfb:{context.session_id}:"
            f"turn-{context.turn_index}:{context.tool_call_id}"
        )
        try:
            client = _service(context, "mindmemos_client", MindMemOSClient)
            receipt = await client.feedback_explicit(
                operation_id=operation_id,
                feedback=feedback,
                messages=snapshot_to_dialogue_messages(snapshot),
                recalled_memories=[
                    _recalled_memory_payload(item) for item in snapshot.recalled_memories
                ],
                session_id=context.session_id,
            )
        except MemoryToolServiceError as exc:
            await _emit_feedback_event(
                context,
                "memory.feedback.explicit.failed",
                {"request_id": context.tool_call_id, "error": exc.code},
            )
            return _store_failure(exc, attempted=True)
        except MindMemOSClientError as exc:
            code = _client_error_code(exc)
            await _emit_feedback_event(
                context,
                "memory.feedback.explicit.failed",
                {"request_id": context.tool_call_id, "error": code},
            )
            return _failure(code, str(exc), attempted=True)
        actions = [
            {
                "action_id": action.action_id,
                "action": action.action,
                "memory_id": action.memory_id,
            }
            for action in receipt.actions
        ]
        await _emit_feedback_event(
            context,
            "memory.feedback.explicit.completed",
            {
                "request_id": context.tool_call_id,
                "action_count": len(actions),
                "actions": actions,
            },
        )
        return _success(
            "feedback",
            {
                "feedback_record_id": receipt.feedback_record_id,
                "operation_id": receipt.operation_id,
                "status": receipt.status,
                "actions": actions,
                "action_count": len(actions),
                "verified_terminal_state": receipt.status in {"completed", "no_action"},
            },
            attempted=True,
        )


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


def _client_error_code(exc: MindMemOSClientError) -> str:
    """Map typed client failures onto canonical memory tool error codes."""

    if isinstance(exc, OutcomeUnknownError):
        return "memory_outcome_unknown"
    if isinstance(exc, OperationConflictError):
        return "memory_conflict"
    if isinstance(exc, OperationNotFoundError):
        return "memory_not_found"
    return "memory_backend_unavailable"


def _memory_item_ids(item: Mapping[str, object]) -> set[object]:
    return {value for value in (item.get("id"), item.get("memory_id")) if value is not None}


def _recalled_field(item: object, key: str) -> object:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


def _recalled_memory_payload(item: object) -> dict[str, Any]:
    """Project one recalled search hit into the SDK ``MemorySearchItem`` shape."""

    metadata = _recalled_field(item, "metadata")
    payload: dict[str, Any] = {
        "id": str(_recalled_field(item, "id") or ""),
        "memory": str(_recalled_field(item, "memory") or ""),
        "memory_type": str(_recalled_field(item, "memory_type") or "fact"),
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
    }
    last_update = _recalled_field(item, "last_update_at")
    if not isinstance(last_update, str) or not last_update:
        last_update = next(
            (
                candidate
                for key in ("last_update_at", "updated_at", "created_at")
                if isinstance((candidate := payload["metadata"].get(key)), str) and candidate
            ),
            "",
        )
    payload["last_update_at"] = last_update
    status = _recalled_field(item, "status")
    if isinstance(status, str) and status:
        payload["status"] = status
    return payload


class UpdateMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        memory_id = _required_string(arguments, "memory_id")
        record = arguments.get("record")
        fields = dict(record) if isinstance(record, Mapping) else None
        raw_content = arguments.get("content")
        content = (
            raw_content.strip() if isinstance(raw_content, str) and raw_content.strip() else None
        )
        if (fields is None) == (content is None):
            return _failure(
                "memory_invalid_input", "provide exactly one of record or content"
            )
        operation_id = f"memupdate:{context.session_id}:{context.tool_call_id}"
        try:
            client = _service(context, "mindmemos_client", MindMemOSClient)
            receipt = await client.update_memory(
                memory_id=memory_id,
                operation_id=operation_id,
                content=content,
                fields=fields,
            )
        except MindMemOSClientError as exc:
            return _failure(_client_error_code(exc), str(exc), attempted=True)
        except MemoryToolServiceError as exc:
            return _store_failure(exc, attempted=True)
        return _success(
            "update",
            {
                "memory_id": receipt.memory_id or memory_id,
                "update_mode": "structured" if fields is not None else "vanilla",
                "status": receipt.status,
                "version": receipt.version,
                "operation_id": operation_id,
                "verified_terminal_state": receipt.status in {"completed", "no_action"},
            },
            attempted=True,
        )


class MemoryHistoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        memory_id = _required_string(arguments, "memory_id")
        try:
            client = _service(context, "mindmemos_client", MindMemOSClient)
            data = await client.get_memory(filters={"memory_id": memory_id})
        except MindMemOSClientError as exc:
            return _failure(_client_error_code(exc), str(exc), attempted=True)
        except MemoryToolServiceError as exc:
            return _store_failure(exc, attempted=True)
        raw_memories = data.get("memories")
        items = (
            [dict(item) for item in raw_memories if isinstance(item, Mapping)]
            if isinstance(raw_memories, Sequence) and not isinstance(raw_memories, (str, bytes))
            else []
        )
        memory = next(
            (
                item
                for item in items
                if item.get("id") == memory_id or item.get("memory_id") == memory_id
            ),
            items[0] if items else None,
        )
        if memory is None:
            return _failure("memory_not_found", "memory id was not found", attempted=True)
        history = data.get("history")
        history_items = (
            [dict(item) for item in history if isinstance(item, Mapping)]
            if isinstance(history, Sequence) and not isinstance(history, (str, bytes))
            else []
        )
        seen_ids = _memory_item_ids(memory)
        versions = [
            memory,
            *(
                item
                for item in history_items
                if not seen_ids & _memory_item_ids(item)
            ),
        ]
        return _success(
            "history",
            {
                "memory_id": memory_id,
                "memory": memory,
                "versions": versions,
                "version_count": len(versions),
                "verified_terminal_state": True,
            },
            attempted=True,
        )


class DeleteMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        memory_id = _required_string(arguments, "memory_id")
        operation_id = f"memdelete:{context.session_id}:{context.tool_call_id}"
        try:
            client = _service(context, "mindmemos_client", MindMemOSClient)
            receipt = await client.delete_memory(
                memory_id=memory_id,
                operation_id=operation_id,
            )
        except MindMemOSClientError as exc:
            return _failure(_client_error_code(exc), str(exc), attempted=True)
        except MemoryToolServiceError as exc:
            return _store_failure(exc, attempted=True)
        return _success(
            "delete",
            {
                "memory_id": receipt.memory_id or memory_id,
                "status": receipt.status,
                "operation_id": operation_id,
                "verified_terminal_state": receipt.status in {"completed", "no_action"},
            },
            attempted=True,
        )


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


def build_memory_tools(
    *,
    mode: Literal["files", "full"] = "full",
) -> tuple[RegisteredTool, ...]:
    """Build the memory tool surface for one configured ``memory.mode``.

    The ``files`` tier registers only ``context_memory``: the file-backed store
    is the whole memory capability, and exposing the MindMemOS tools would
    hand the model calls that can never succeed.  The ``full`` tier keeps the
    complete surface.
    """

    if mode not in ("files", "full"):
        raise ValueError(f"unsupported memory.mode: {mode!r}")
    # The files tier has no mindmemos_* tools, so the description must not
    # point the model at a memory search surface that does not exist.
    context_memory_description = _CONTEXT_MEMORY_DESCRIPTION + (
        " Use the memory search tools for those."
        if mode == "full"
        else " No separate long-term memory tools exist on this tier."
    )
    file_tools = (
        RegisteredTool(
            _definition(
                "context_memory",
                context_memory_description,
                FileMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("context_memory", FileMemoryExecutor(), FileMemoryInput),
        ),
    )
    if mode == "files":
        return file_tools
    return file_tools + (
        RegisteredTool(
            _definition(
                "mindmemos_search",
                "Search long-term memory by meaning across memory types: profiles, facts, experiences, episodes, tool traces, skill candidates, and file knowledge. Use it when the current request may benefit from prior knowledge, previous attempts, or reusable tool behavior. Search again with different wording or follow-up queries when earlier results reveal useful clues.",
                SearchMemoriesInput,
            ),
            MemoryAuditExecutor("mindmemos_search", SearchMemoriesExecutor(), SearchMemoriesInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_history",
                "Read one long-term memory by exact memory ID together with its version history when the backend provides it. Returns the active memory detail plus earlier or archived versions so you can explain what changed or recover an earlier value. Take the ID from mindmemos_search or a previous update result.",
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
                "Replace an existing long-term memory by exact ID through the native version update. Send record for a structured memory to replace its typed fields, or send content as the complete replacement text for a plain memory. Never send both record and content. Take the memory ID from mindmemos_search.",
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
                "Review concrete user feedback about long-term memory and let the memory backend decide whether to add a new memory, create a corrected version of an existing memory, archive an incorrect memory, or leave memory unchanged. Use this when the user has corrected a fact, changed the scope of a preference or procedure, or said that remembered information is outdated, but the correct memory action is not already determined by one exact memory ID and one complete replacement record. Pass the user's concrete correction, scope change, or instruction. Do not use this when you already have an exact memory ID and a complete replacement record; use mindmemos_update instead. Do not use this when the user explicitly asks to forget one exact memory; use mindmemos_delete instead. Do not use it for a vague complaint with no correction, ordinary task failure, or praise with no requested memory change.",
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
