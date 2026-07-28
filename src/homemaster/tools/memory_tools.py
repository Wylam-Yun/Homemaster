"""Six canonical V2.1 memory tools backed by application-owned services."""

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
from homemaster.memory.evidence import MemoryEvidenceError, MemoryEvidenceLedger
from homemaster.memory.file_store import (
    FileMemoryError,
    FileMemoryOperation,
    FileMemoryState,
    FileMemoryStore,
)
from homemaster.memory.mem0_store import Mem0MemoryStore, Mem0StoreError, StoredMemory
from homemaster.memory.models import MEMORY_RECORD_ADAPTER, MemoryRecord, Subject
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
    record: MemoryRecordInput = Field(
        description=(
            "Complete FactRecord or ProcedureRecord. Send a JSON object; a JSON-encoded "
            "object string is accepted only for provider compatibility. Fact predicate must "
            "be lowercase English snake_case, such as location."
        )
    )
    evidence_refs: tuple[_NonEmptyText, ...] = Field(
        min_length=1, json_schema_extra={"uniqueItems": True}
    )

    @model_validator(mode="after")
    def _memory_types_match(self) -> AddMemoryInput:
        if self.memory_type != self.record.memory_type:
            raise ValueError("memory_type must match record.memory_type")
        return self


class SearchMemoriesInput(_MemoryToolInput):
    query: _NonEmptyText
    memory_type: Literal["fact", "procedure"] | None = None
    limit: int = Field(default=5, ge=1, le=20)
    subject: Subject | None = None
    predicate: str | None = None
    entry_url: str | None = None
    name: str | None = None


class GetMemoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText


class UpdateMemoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText
    record: MemoryRecordInput = Field(
        description=(
            "Complete replacement FactRecord or ProcedureRecord. Send a JSON object; a "
            "JSON-encoded object string is accepted only for provider compatibility. Fact "
            "predicate must be lowercase English snake_case."
        )
    )
    evidence_refs: tuple[_NonEmptyText, ...] = Field(
        min_length=1, json_schema_extra={"uniqueItems": True}
    )


class DeleteMemoryInput(_MemoryToolInput):
    memory_id: _NonEmptyText


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
                "add_memory",
                "update_memory",
                "delete_memory",
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
        except Mem0StoreError as exc:
            return _store_failure(exc, attempted=True)


class AddMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        parsed = _record(arguments.get("record"), arguments.get("memory_type"))
        if isinstance(parsed, ToolExecutionResult):
            return parsed
        evidence = _validated_evidence(context, parsed, arguments.get("evidence_refs"))
        if isinstance(evidence, ToolExecutionResult):
            return evidence
        try:
            item = await _service(context, "mem0_memory_store", Mem0MemoryStore).add(
                parsed, provenance_seq=max(entry.provenance_seq for entry in evidence)
            )
        except Mem0StoreError as exc:
            return _store_failure(exc, attempted=True)
        return _stored_success("add", item, attempted=True)


class SearchMemoriesExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        query = _required_string(arguments, "query")
        limit = arguments.get("limit", 5)
        memory_type = arguments.get("memory_type")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            return _failure("memory_invalid_input", "limit must be between 1 and 20")
        if memory_type not in {None, "fact", "procedure"}:
            return _failure("memory_invalid_input", "invalid memory_type")
        try:
            store = _service(context, "mem0_memory_store", Mem0MemoryStore)
            result = await store.search_with_diagnostics(
                query,
                memory_type=memory_type,
                limit=limit,
                subject=_optional_mapping(arguments, "subject"),
                predicate=_optional_string(arguments, "predicate"),
                entry_url=_optional_string(arguments, "entry_url"),
                name=_optional_string(arguments, "name"),
            )
        except Mem0StoreError as exc:
            return _store_failure(exc)
        return _success(
            "search",
            {
                "records": [_stored_payload(item) for item in result.records],
                "count": len(result.records),
                "diagnostics": [
                    {
                        "code": item.code,
                        "memory_id_hash": item.memory_id_hash,
                        "match_sources": list(item.match_sources),
                    }
                    for item in result.diagnostics
                ],
                "verified_terminal_state": True,
            },
        )


class GetMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        try:
            item = await _service(context, "mem0_memory_store", Mem0MemoryStore).get(
                _required_string(arguments, "memory_id")
            )
        except Mem0StoreError as exc:
            return _store_failure(exc)
        return _stored_success("get", item)


class UpdateMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        parsed = _record(arguments.get("record"), None)
        if isinstance(parsed, ToolExecutionResult):
            return parsed
        evidence = _validated_evidence(context, parsed, arguments.get("evidence_refs"))
        if isinstance(evidence, ToolExecutionResult):
            return evidence
        try:
            item = await _service(context, "mem0_memory_store", Mem0MemoryStore).update(
                _required_string(arguments, "memory_id"),
                parsed,
                provenance_seq=max(entry.provenance_seq for entry in evidence),
            )
        except Mem0StoreError as exc:
            return _store_failure(exc, attempted=True)
        return _stored_success("update", item, attempted=True)


class DeleteMemoryExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        memory_id = _required_string(arguments, "memory_id")
        try:
            await _service(context, "mem0_memory_store", Mem0MemoryStore).delete(memory_id)
        except Mem0StoreError as exc:
            return _store_failure(exc, attempted=True)
        return _success(
            "delete",
            {
                "memory_id": memory_id,
                "verified_terminal_state": True,
            },
            attempted=True,
        )


def _validated_evidence(
    context: ToolExecutionContext, record: MemoryRecord, raw_refs: object
) -> tuple[Any, ...] | ToolExecutionResult:
    if not isinstance(raw_refs, Sequence) or isinstance(raw_refs, str):
        return _failure("memory_evidence_missing", "evidence_refs are required")
    refs = [item for item in raw_refs if isinstance(item, str) and item]
    if len(refs) != len(raw_refs):
        return _failure("memory_evidence_invalid", "evidence refs must be non-empty strings")
    subject = context.permission_subject
    try:
        evidence = _service(context, "memory_evidence_ledger", MemoryEvidenceLedger).validate(
            refs,
            expected_kind=record.source,
            tenant_id=subject.tenant_id,
            session_id=context.session_id,
            run_id=context.run_id,
            turn_id=f"turn-{context.turn_index}",
        )
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


def _stored_success(
    operation: str, item: StoredMemory, *, attempted: bool = False
) -> ToolExecutionResult:
    return _success(operation, _stored_payload(item), attempted=attempted)


def _stored_payload(item: StoredMemory) -> dict[str, object]:
    return {
        "memory_id": item.memory_id,
        "memory_type": item.memory_type,
        "record": item.record.model_dump(mode="json"),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "score": item.score,
        "match_sources": list(item.match_sources),
        "verified_terminal_state": True,
    }


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


def _store_failure(exc: Mem0StoreError, *, attempted: bool = False) -> ToolExecutionResult:
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
        raise Mem0StoreError("memory_backend_unavailable", f"{name} is unavailable")
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
                "memory",
                "Update curated user-profile or persistent-memory entries. Their frozen contents are already present in the current session context and this tool has no read action. Use target=user only for stable identity, preferences, communication and usage habits; use target=memory for recent events, decisions, results and cross-session unfinished work. Object locations, device state and reusable procedures belong in the mem0 tools. Writes persist immediately, are independently read back from disk, and affect only new session snapshots. Requires tool.mutate.",
                FileMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("memory", FileMemoryExecutor(), FileMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "add_memory",
                "Store only structured external-world facts (such as object locations or device state) and reusable, environment-verified procedures. Never use this for the user's identity, preferences, habits, health guidance or long-term schedule; those must use memory(target=user). Recent events, decisions, results and unfinished work must use memory(target=memory). Supply one complete FactRecord or ProcedureRecord with infer=false and current opaque evidence refs. Never supply metadata, scope, IDs, dedupe keys, timestamps, confidence or credentials.",
                AddMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("add_memory", AddMemoryExecutor(), AddMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "search_memories",
                "Search structured external facts and reusable procedures that are not already present in the current context. One call combines exact metadata with mem0 hybrid retrieval, so use one query covering the current request and do not repeat it unless the requested information or search hints change. This does not search SOUL, USER or MEMORY files. Only pass returned IDs to get, update or delete.",
                SearchMemoriesInput,
            ),
            MemoryAuditExecutor("search_memories", SearchMemoriesExecutor(), SearchMemoriesInput),
        ),
        RegisteredTool(
            _definition(
                "get_memory",
                "Fetch a complete fact or procedure only by an exact ID returned by add or search. Use it before executing a procedure or confirming an item for update/delete; it is not semantic search and IDs must never be guessed.",
                GetMemoryInput,
            ),
            MemoryAuditExecutor("get_memory", GetMemoryExecutor(), GetMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "update_memory",
                "Replace one existing memory in place with a complete validated record, normally after search/get. Update facts only from confirmed corrections or newer observations; update a procedure only after the entire new path succeeds. Partial patches and unconfirmed information are forbidden, and current opaque evidence refs are required.",
                UpdateMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("update_memory", UpdateMemoryExecutor(), UpdateMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "delete_memory",
                "Delete one exact memory only when the user explicitly requests it or the record is confirmed wrong, duplicate or permanently obsolete. Search/get first, never guess an ID, and never perform delete-all; there is no automatic forgetting.",
                DeleteMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("delete_memory", DeleteMemoryExecutor(), DeleteMemoryInput),
        ),
    )


__all__ = ["build_memory_tools"]
