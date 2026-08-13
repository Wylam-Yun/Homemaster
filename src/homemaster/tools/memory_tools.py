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
    memory_type: Literal["fact", "procedure"] | None = Field(
        default=None,
        description=(
            "Use fact for external-world state; use procedure for reusable guidance and "
            "Session-derived experience memories. Omit when both may be relevant."
        ),
    )
    limit: int = Field(default=5, ge=1, le=20)
    subject: Subject | None = None
    predicate: str | None = None
    entry_url: str | None = None
    name: str | None = None


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
                "mindmemos_add",
                "mindmemos_update",
                "mindmemos_delete",
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
        parsed = _record(arguments.get("record"), arguments.get("memory_type"))
        if isinstance(parsed, ToolExecutionResult):
            return parsed
        evidence = _validated_evidence(context, parsed, arguments.get("evidence_refs"))
        if isinstance(evidence, ToolExecutionResult):
            return evidence
        try:
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            memory_context = _mindmemos_context(context)
            item = await _add_mindmemos_record(
                store,
                parsed,
                provenance_seq=max(entry.provenance_seq for entry in evidence),
                context=memory_context,
            )
        except Exception as exc:
            return _failure("memory_backend_unavailable", str(exc), attempted=True)
        if item is None:
            return _failure(
                "memory_backend_rejected",
                "MindMemOS add returned no raw memory",
                attempted=True,
            )
        return _success("add", item, attempted=True)


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
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            memory_context = _mindmemos_context(context)
            filters = (
                {"mem_type": _mindmemos_memory_type(memory_type)}
                if memory_type is not None
                else None
            )
            result = await store.search(
                query,
                memory_context,
                top_k=limit,
                search_pipeline="vanilla",
                filters=filters,
            )
            records: list[dict[str, object]] = []
            diagnostics: list[dict[str, object]] = []
            for hit in result.memories:
                raw = await store.get_raw(hit.id, memory_context)
                parsed = _mindmemos_record(raw)
                if parsed is None:
                    vanilla_payload = _vanilla_experience_payload(hit, raw, arguments)
                    if vanilla_payload is not None:
                        records.append(vanilla_payload)
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
        except Exception as exc:
            return _failure("memory_backend_unavailable", str(exc))
        return _success(
            "search",
            {
                "records": records,
                "count": len(records),
                "diagnostics": diagnostics,
                "verified_terminal_state": True,
            },
        )


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


def _mindmemos_memory_type(memory_type: str) -> str:
    return "experience" if memory_type == "procedure" else memory_type


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
    if memory_type is not None and record.memory_type != memory_type:
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
        "memory_type": record.memory_type,
        "record": record.model_dump(mode="json"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
        "score": getattr(hit, "score", None),
        "match_sources": ["semantic"],
        "verified_terminal_state": True,
    }


def _vanilla_experience_payload(
    hit: Any, raw: Any, arguments: Mapping[str, object]
) -> dict[str, object] | None:
    """Project native Vanilla experience memories without weakening Schema validation."""
    if (
        raw is None
        or getattr(raw, "mem_extract_type", None) != "vanilla"
        or getattr(raw, "mem_type", None) != "experience"
        or getattr(raw, "status", "active") != "active"
    ):
        return None
    content = getattr(raw, "content", None)
    if not isinstance(content, str) or not content.strip():
        return None
    if arguments.get("memory_type") not in {None, "procedure"}:
        return None
    if any(arguments.get(key) is not None for key in ("subject", "predicate", "entry_url", "name")):
        return None

    metadata = _mindmemos_request_metadata(raw)
    source = {
        key: metadata[key]
        for key in (
            "source_type",
            "source_session_id",
            "input_hash",
            "trace_schema_version",
            "trace_hash",
            "extractor_version",
        )
        if key in metadata
    }
    created_at = getattr(raw, "created_at", None)
    updated_at = getattr(raw, "update_at", None)
    return {
        "memory_id": hit.id,
        "memory_type": "procedure",
        "content": content,
        "source": source,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else None,
        "score": getattr(hit, "score", None),
        "match_sources": ["semantic"],
        "verified_terminal_state": True,
    }


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
        provenance_seq = max(entry.provenance_seq for entry in evidence)
        try:
            memory_id = _required_string(arguments, "memory_id")
            store = _service(context, "mindmemos", EmbeddedMindMemOS)
            memory_context = _mindmemos_context(context)
            current = await store.get_raw(memory_id, memory_context)
            current_record = _mindmemos_record(current)
            if current is None or current_record is None:
                return _failure("memory_not_found", "memory id was not found", attempted=True)
            if current_record.memory_type != parsed.memory_type:
                return _failure(
                    "memory_conflict",
                    "memory type cannot change",
                    attempted=True,
                )
            metadata = _mindmemos_request_metadata(current)
            current_seq = int(metadata.get("provenance_seq", 0))
            if provenance_seq <= current_seq:
                return _failure(
                    "memory_stale_observation",
                    "evidence is not newer",
                    attempted=True,
                )
            deleted = await store.delete(memory_id, memory_context)
            if deleted.status != "ok":
                return _failure(
                    "memory_backend_rejected",
                    deleted.message or "MindMemOS update archive was rejected",
                    attempted=True,
                )
            item = await _add_mindmemos_record(
                store,
                parsed,
                provenance_seq=provenance_seq,
                context=memory_context,
            )
        except Exception as exc:
            return _failure("memory_outcome_unknown", str(exc), attempted=True)
        if item is None:
            return _failure(
                "memory_outcome_unknown",
                "old memory was archived but MindMemOS returned no replacement raw memory",
                attempted=True,
            )
        return _success("update", item, attempted=True)


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


async def _add_mindmemos_record(
    store: EmbeddedMindMemOS,
    record: MemoryRecord,
    *,
    provenance_seq: int,
    context: Any,
) -> dict[str, object] | None:
    from mindmemos.typing import TextMessage

    serialized = serialize_record(record, provenance_seq=provenance_seq)
    metadata = {
        **serialized.metadata,
        "homemaster_memory_type": record.memory_type,
    }
    result = await store.add(
        [TextMessage(text=serialized.text)],
        context,
        force_generation=True,
        metadata=metadata,
    )
    candidate_ids: list[str] = []
    for event in result.memories:
        candidate_ids.extend(
            item
            for item in getattr(event, "related_memory_ids", [])
            if isinstance(item, str) and item
        )
        event_id = getattr(event, "memory_id", None)
        if isinstance(event_id, str) and event_id:
            candidate_ids.append(event_id)
    for memory_id in dict.fromkeys(candidate_ids):
        raw = await store.get_raw(memory_id, context)
        if getattr(raw, "mem_type", None) != _mindmemos_memory_type(record.memory_type):
            continue
        parsed = _mindmemos_record(raw)
        if parsed == record:
            return _mindmemos_raw_payload(raw, parsed)
    return None


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
                "Store a verified external fact or reusable procedure in searchable long-term memory. Use this for stable information learned from the environment, such as an object location, device state, or a procedure that has actually succeeded. Do not use it for user preferences, temporary task progress, or unverified guesses.",
                AddMemoryInput,
                mutating=True,
            ),
            MemoryAuditExecutor("mindmemos_add", AddMemoryExecutor(), AddMemoryInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_search",
                "Search long-term memory by meaning. Returns ranked external facts, verified procedures, and experiences learned from past sessions. Use it when the current request may benefit from prior knowledge or previous attempts. Search again with different wording or follow-up queries when earlier results reveal useful clues.",
                SearchMemoriesInput,
            ),
            MemoryAuditExecutor("mindmemos_search", SearchMemoriesExecutor(), SearchMemoriesInput),
        ),
        RegisteredTool(
            _definition(
                "mindmemos_update",
                "Replace an existing long-term memory with a complete corrected record. Use this when a stored fact is confirmed wrong or outdated, or when a verified procedure has been replaced by a better successful procedure. Take the memory ID from mindmemos_search.",
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
    )


__all__ = ["build_memory_tools"]
