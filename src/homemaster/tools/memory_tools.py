"""Six canonical V2.1 memory tools backed by application-owned services."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from homemaster.events.trace import append_jsonl_event
from homemaster.memory.evidence import MemoryEvidenceError, MemoryEvidenceLedger
from homemaster.memory.file_store import (
    FileMemoryError,
    FileMemoryOperation,
    FileMemoryState,
    FileMemoryStore,
)
from homemaster.memory.mem0_store import Mem0MemoryStore, Mem0StoreError, StoredMemory
from homemaster.memory.models import MEMORY_RECORD_ADAPTER, MemoryRecord
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


class MemoryAuditExecutor:
    """Write one field-limited JSONL record around a canonical memory executor."""

    def __init__(self, operation: str, delegate: Any) -> None:
        self.operation = operation
        self.delegate = delegate

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
        result = await self.delegate.execute(arguments, context)
        path = context.services.get("memory_audit_path")
        if isinstance(path, Path):
            raw_id = arguments.get("memory_id")
            memory_id_hash = (
                hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16]
                if isinstance(raw_id, str) and raw_id
                else None
            )
            record = arguments.get("record")
            memory_type = (
                record.get("memory_type")
                if isinstance(record, Mapping)
                else arguments.get("memory_type")
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
    @staticmethod
    def is_read_only(arguments: Mapping[str, object]) -> bool:
        return arguments.get("action") == "read" and arguments.get("operations") is None

    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        target = _required_string(arguments, "target")
        action = arguments.get("action")
        raw_operations = arguments.get("operations")
        try:
            store = _service(context, "file_memory_store", FileMemoryStore)
            if action == "read":
                if raw_operations is not None:
                    return _failure("memory_invalid_input", "read cannot include operations")
                return _file_success("read", store.read(target))
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
            return _failure(exc.code, str(exc), details=exc.details, attempted=action != "read")
        except Mem0StoreError as exc:
            return _store_failure(exc, attempted=action != "read")


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
    schema: Mapping[str, object],
    *,
    mutating: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        internal_id=f"homemaster.memory.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=schema,
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


def build_memory_tools() -> tuple[RegisteredTool, ...]:
    record = {"type": "object", "description": "Complete FactRecord or ProcedureRecord"}
    evidence = {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "uniqueItems": True,
    }
    identifier = {"type": "string", "minLength": 1}
    return (
        RegisteredTool(
            _definition(
                "memory",
                "Manage curated USER or MEMORY file entries. Use target=user only for stable identity, preferences, communication and usage habits; use target=memory for recent events, decisions, results and cross-session unfinished work. Object locations, device state and reusable procedures belong in the mem0 tools. The session prompt is frozen, so writes persist immediately but affect only new sessions; read is for live disk audit or confirming a prior write. Mutations additionally require tool.mutate.",
                {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "enum": ["user", "memory"]},
                        "action": {
                            "type": ["string", "null"],
                            "enum": ["read", "add", "update", "delete", None],
                        },
                        "content": {"type": ["string", "null"]},
                        "match": {"type": ["string", "null"]},
                        "operations": {
                            "type": ["array", "null"],
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": ["add", "update", "delete"],
                                    },
                                    "content": {"type": ["string", "null"]},
                                    "match": {"type": ["string", "null"]},
                                },
                                "required": ["action"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            MemoryAuditExecutor("memory", FileMemoryExecutor()),
        ),
        RegisteredTool(
            _definition(
                "add_memory",
                "Store one complete external-world fact or reusable procedure exactly with infer=false. USER preferences belong in memory(target=user), and recent narrative context belongs in memory(target=memory). Both fact sources require current opaque evidence refs; procedures require a fully successful environment-observation sequence. Never supply metadata, scope, IDs, dedupe keys, timestamps, confidence or credentials.",
                {
                    "type": "object",
                    "properties": {
                        "memory_type": {"type": "string", "enum": ["fact", "procedure"]},
                        "record": record,
                        "evidence_refs": evidence,
                    },
                    "required": ["memory_type", "record", "evidence_refs"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            MemoryAuditExecutor("add_memory", AddMemoryExecutor()),
        ),
        RegisteredTool(
            _definition(
                "search_memories",
                "Search external facts and reusable procedures when the current session lacks the answer. This does not search SOUL, USER or MEMORY files. Use separate queries for separate questions, include exact hints when known, and only pass returned IDs to get, update or delete.",
                {
                    "type": "object",
                    "properties": {
                        "query": identifier,
                        "memory_type": {
                            "type": ["string", "null"],
                            "enum": ["fact", "procedure", None],
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                        "subject": {"type": ["object", "null"]},
                        "predicate": {"type": ["string", "null"]},
                        "entry_url": {"type": ["string", "null"]},
                        "name": {"type": ["string", "null"]},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            MemoryAuditExecutor("search_memories", SearchMemoriesExecutor()),
        ),
        RegisteredTool(
            _definition(
                "get_memory",
                "Fetch a complete fact or procedure only by an exact ID returned by add or search. Use it before executing a procedure or confirming an item for update/delete; it is not semantic search and IDs must never be guessed.",
                {
                    "type": "object",
                    "properties": {"memory_id": identifier},
                    "required": ["memory_id"],
                    "additionalProperties": False,
                },
            ),
            MemoryAuditExecutor("get_memory", GetMemoryExecutor()),
        ),
        RegisteredTool(
            _definition(
                "update_memory",
                "Replace one existing memory in place with a complete validated record, normally after search/get. Update facts only from confirmed corrections or newer observations; update a procedure only after the entire new path succeeds. Partial patches and unconfirmed information are forbidden, and current opaque evidence refs are required.",
                {
                    "type": "object",
                    "properties": {
                        "memory_id": identifier,
                        "record": record,
                        "evidence_refs": evidence,
                    },
                    "required": ["memory_id", "record", "evidence_refs"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            MemoryAuditExecutor("update_memory", UpdateMemoryExecutor()),
        ),
        RegisteredTool(
            _definition(
                "delete_memory",
                "Delete one exact memory only when the user explicitly requests it or the record is confirmed wrong, duplicate or permanently obsolete. Search/get first, never guess an ID, and never perform delete-all; there is no automatic forgetting.",
                {
                    "type": "object",
                    "properties": {"memory_id": identifier},
                    "required": ["memory_id"],
                    "additionalProperties": False,
                },
                mutating=True,
            ),
            MemoryAuditExecutor("delete_memory", DeleteMemoryExecutor()),
        ),
    )


__all__ = ["build_memory_tools"]
