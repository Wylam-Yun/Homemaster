"""Compatibility adapters for the pre-canonical tool APIs.

The adapters in this module are deliberately one-way.  They let the future
execution pipeline consume the existing ``ToolSpec``/``ToolResult`` values
without changing the legacy dispatcher or making claims about fields that the
old API did not carry.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.tools.contracts import (
    ExecutionBackend,
    RegisteredTool,
    ResultImage,
    TerminalInfo,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)
from homemaster.tools.results import ToolResult

_SAFE_ALIAS_RE = re.compile(r"[^a-z0-9_]+")
_SAFE_ID_RE = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True)
class LegacyAdapterDebt:
    """Explicitly recorded information that the legacy API cannot express."""

    fields: tuple[str, ...] = ()
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        fields = tuple(self.fields)
        if len(fields) != len(set(fields)):
            raise ValueError("legacy adapter debt fields must be unique")
        if any(not isinstance(item, str) or not item.strip() for item in fields):
            raise ValueError("legacy adapter debt fields must be non-empty strings")
        object.__setattr__(self, "fields", fields)
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @property
    def empty(self) -> bool:
        return not self.fields

    def has(self, field_name: str) -> bool:
        return field_name in self.fields


@dataclass(frozen=True)
class LegacyToolExecutionContext:
    """Canonical-call envelope that exposes the old ``RunContext`` explicitly."""

    run_context: RunContext
    tool_call_id: str
    internal_tool_id: str
    canonical_context: ToolExecutionContext | None = None
    actual_backend: object | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_context, RunContext):
            raise TypeError("run_context must be a RunContext")
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip():
            raise ValueError("tool_call_id is required")
        if not isinstance(self.internal_tool_id, str) or not self.internal_tool_id.strip():
            raise ValueError("internal_tool_id is required")

    @classmethod
    def from_canonical(cls, context: ToolExecutionContext) -> LegacyToolExecutionContext:
        """Recover the explicit legacy context carried by a canonical call."""

        backend = context.backend
        if isinstance(backend, cls):
            if backend.canonical_context is context:
                return backend
            return cls(
                run_context=backend.run_context,
                tool_call_id=context.tool_call_id,
                internal_tool_id=context.internal_tool_id,
                canonical_context=context,
                actual_backend=backend.actual_backend,
            )
        if isinstance(backend, RunContext):
            return cls(
                run_context=backend,
                tool_call_id=context.tool_call_id,
                internal_tool_id=context.internal_tool_id,
                canonical_context=context,
            )
        raise TypeError(
            "canonical context backend must carry a LegacyToolExecutionContext or RunContext"
        )

    def __getattr__(self, name: str) -> Any:
        backend = self.actual_backend
        if backend is None:
            raise AttributeError(name)
        return getattr(backend, name)


class LegacyExecutorAdapter:
    """Expose a synchronous legacy callable through the async executor protocol."""

    def __init__(self, executor: Callable[..., Any], *, internal_tool_id: str) -> None:
        if not callable(executor):
            raise TypeError("legacy executor must be callable")
        self._executor = executor
        self.internal_tool_id = internal_tool_id

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        legacy_context = LegacyToolExecutionContext.from_canonical(context)
        result = await asyncio.to_thread(
            self._executor,
            arguments=dict(arguments),
            run_context=legacy_context.run_context,
        )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolExecutionResult):
            return result
        return normalize_legacy_result(
            result,
            tool_call_id=context.tool_call_id,
            name=context.internal_tool_id,
        ).result


@dataclass(frozen=True)
class AdaptedLegacyTool:
    """Canonical registration plus migration metadata for one legacy spec."""

    definition: ToolDefinition
    registered_tool: RegisteredTool
    selectable_by_model: bool
    migration_debt: LegacyAdapterDebt = field(default_factory=LegacyAdapterDebt)

    @property
    def debt(self) -> LegacyAdapterDebt:
        return self.migration_debt

    @property
    def executor(self) -> LegacyExecutorAdapter:
        return self.registered_tool.executor  # type: ignore[return-value]


@dataclass(frozen=True)
class NormalizedLegacyResult:
    """Canonical result together with its original provider-facing envelope."""

    result: ToolExecutionResult
    tool_call_id: str
    name: str
    legacy_message: ToolResultMessage | None = None
    migration_debt: LegacyAdapterDebt = field(default_factory=LegacyAdapterDebt)

    @property
    def debt(self) -> LegacyAdapterDebt:
        return self.migration_debt

    def to_message(self) -> ToolResultMessage:
        return self.result.to_message(tool_call_id=self.tool_call_id, name=self.name)


def adapt_legacy_tool_spec(
    spec: Any,
    *,
    internal_id: str | None = None,
    version: str = "0.0.0",
    provenance: ToolProvenance | None = None,
    executor: Callable[..., Any] | None = None,
    output_schema: Mapping[str, object] | None = None,
) -> AdaptedLegacyTool:
    """Convert an execution-capable legacy ``tools.ToolSpec`` to canonical values."""

    name = _required_text(getattr(spec, "name", None), "legacy tool name")
    description = _required_text(getattr(spec, "description", "") or name, "description")
    model_alias = _safe_alias(name)
    stable_id = internal_id or f"legacy.{_safe_id(name)}.v1"
    stable_id = _safe_internal_id(stable_id)
    source_provenance = provenance or ToolProvenance(
        source="legacy",
        reference=_spec_reference(spec),
    )
    input_value = getattr(spec, "input_schema", {}) or {}
    if not isinstance(input_value, Mapping):
        raise TypeError("legacy input_schema must be a mapping")
    raw_output = output_schema if output_schema is not None else getattr(spec, "output_schema", {})
    raw_output = raw_output or {}
    if not isinstance(raw_output, Mapping):
        raise TypeError("legacy output_schema must be a mapping")

    debt_fields: list[str] = []
    debt_details: dict[str, str] = {}
    if not raw_output:
        debt_fields.append("empty_output_schema")
        debt_details["empty_output_schema"] = "legacy tool did not declare an output schema"

    executor_value = executor if executor is not None else getattr(spec, "executor", None)
    if executor_value is None:
        raise ValueError("legacy tool adaptation requires an explicit executor")

    state_effects, state_debt = _state_effects(getattr(spec, "state_effects", ()))
    debt_fields.extend(state_debt)
    requires_verification = bool(getattr(spec, "requires_verification", False))
    if requires_verification:
        debt_fields.append("legacy_requires_verification")
        debt_details["legacy_requires_verification"] = (
            "legacy boolean retained as migration debt; no verifier is inferred"
        )
    if getattr(spec, "executor_mode", ""):
        debt_fields.append("executor_mode")
        debt_details["executor_mode"] = str(spec.executor_mode)
    selectable = bool(getattr(spec, "selectable_by_model", True))
    if not selectable:
        debt_fields.append("not_selectable_by_model")
        debt_details["not_selectable_by_model"] = "canonical view selection must enforce this flag"

    definition = ToolDefinition(
        internal_id=stable_id,
        model_alias=model_alias,
        description=description,
        input_schema=dict(input_value),
        output_schema=dict(raw_output),
        verification_policy=VerificationPolicy(),
        provenance=source_provenance,
        version=version,
        execution_backend=ExecutionBackend.IN_PROCESS,
        state_effects=state_effects,
    )
    wrapped = LegacyExecutorAdapter(executor_value, internal_tool_id=stable_id)
    registered = RegisteredTool(definition=definition, executor=wrapped)
    return AdaptedLegacyTool(
        definition=definition,
        registered_tool=registered,
        selectable_by_model=selectable,
        migration_debt=LegacyAdapterDebt(tuple(debt_fields), debt_details),
    )


def adapt_tool_spec(*args: Any, **kwargs: Any) -> AdaptedLegacyTool:
    """Short alias used by callers that do not need to name the old API."""

    return adapt_legacy_tool_spec(*args, **kwargs)


def normalize_legacy_result(
    value: Any,
    *,
    tool_call_id: str,
    name: str,
) -> NormalizedLegacyResult:
    """Normalize a legacy result while preserving its tool-call identity."""

    if not isinstance(tool_call_id, str) or not tool_call_id.strip():
        raise ValueError("tool_call_id is required")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name is required")

    if isinstance(value, ToolExecutionResult):
        return NormalizedLegacyResult(
            result=value,
            tool_call_id=tool_call_id,
            name=name,
        )
    if isinstance(value, ToolResultMessage):
        return _normalize_message(value, tool_call_id=tool_call_id, name=name)
    if isinstance(value, ToolResult):
        return _normalize_tool_result(value, tool_call_id=tool_call_id, name=name)
    if isinstance(value, Mapping):
        return _normalize_mapping(value, tool_call_id=tool_call_id, name=name)
    return _failure_normalized(
        tool_call_id=tool_call_id,
        name=name,
        code="invalid_legacy_result",
        message=f"unsupported legacy result type: {type(value).__name__}",
        debt=("unsupported_result_type",),
    )


def normalize_legacy_tool_result(
    value: Any,
    *,
    tool_call_id: str,
    name: str,
) -> NormalizedLegacyResult:
    return normalize_legacy_result(value, tool_call_id=tool_call_id, name=name)


class LegacyObserverAdapter:
    """Transparent observer proxy preserving dispatcher callback ordering."""

    def __init__(self, observer: Any) -> None:
        self._observer = observer

    def on_call(self, tool_call: ToolCall) -> None:
        self._observer.on_call(tool_call)

    def terminal_result(self, tool_call: ToolCall) -> ToolResultMessage | None:
        return self._observer.terminal_result(tool_call)

    def on_result(self, tool_call: ToolCall, result: Any) -> None:
        self._observer.on_result(tool_call, result)

    def on_exception(self, tool_call: ToolCall, error: Exception) -> ToolResultMessage:
        return self._observer.on_exception(tool_call, error)


def adapt_legacy_observer(observer: Any) -> LegacyObserverAdapter:
    return LegacyObserverAdapter(observer)


def _normalize_tool_result(
    value: ToolResult,
    *,
    tool_call_id: str,
    name: str,
) -> NormalizedLegacyResult:
    debt: list[str] = []
    data = dict(value.data)
    text = value.summary or ""
    if value.tool_name and value.tool_name != name:
        debt.append("legacy_tool_name_mismatch")
    if value.executor_mode:
        debt.append("legacy_executor_mode")
    if value.evidence_refs:
        evidence = tuple(value.evidence_refs)
    else:
        evidence = ()
    if value.success:
        result = ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=text,
            data=data,
            evidence_refs=evidence,
            retryable=False,
            backend_attempted=True,
        )
    else:
        reason = value.failure_reason or "legacy tool reported failure"
        result = ToolExecutionResult(
            status=ToolExecutionStatus.FAILURE,
            text=text,
            data=data,
            evidence_refs=evidence,
            error=ToolExecutionError("legacy_failure", reason),
            retryable=value.retryable,
            backend_attempted=True,
        )
    return NormalizedLegacyResult(
        result=result,
        tool_call_id=tool_call_id,
        name=name,
        migration_debt=LegacyAdapterDebt(tuple(debt)),
    )


def _normalize_mapping(
    value: Mapping[str, Any],
    *,
    tool_call_id: str,
    name: str,
) -> NormalizedLegacyResult:
    data = dict(value)
    success_value = data.get("success")
    success = success_value if isinstance(success_value, bool) else not bool(data.get("error"))
    text = _first_text(data)
    evidence = _string_tuple(data.get("evidence_refs", ()))
    images, image_debt = _images_from_data(data)
    terminal = _terminal_from_data(data)
    debt = list(image_debt)
    if "success" not in data:
        debt.append("implicit_success")
    if success:
        result = ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=text,
            data=data,
            images=images,
            evidence_refs=evidence,
            terminal=terminal,
            backend_attempted=True,
        )
    else:
        reason = str(
            data.get("failure_reason") or data.get("error") or "legacy tool reported failure"
        )
        result = ToolExecutionResult(
            status=ToolExecutionStatus.FAILURE,
            text=text,
            data=data,
            images=images,
            evidence_refs=evidence,
            error=ToolExecutionError("legacy_failure", reason),
            retryable=bool(data.get("retryable", False)),
            terminal=terminal,
            backend_attempted=True,
        )
    return NormalizedLegacyResult(
        result=result,
        tool_call_id=tool_call_id,
        name=name,
        migration_debt=LegacyAdapterDebt(tuple(debt)),
    )


def _normalize_message(
    value: ToolResultMessage,
    *,
    tool_call_id: str,
    name: str,
) -> NormalizedLegacyResult:
    debt: list[str] = []
    effective_id = value.tool_call_id or tool_call_id
    if value.tool_call_id and value.tool_call_id != tool_call_id:
        debt.append("legacy_tool_call_id_replaced")
    effective_name = value.name or name
    payload = dict(value.data or {})
    if value.data is None:
        recovered = _object_from_text_content(value.content)
        if recovered is not None:
            payload = recovered
            debt.append("message_data_recovered_from_content")
    if value.provider_metadata:
        debt.append("provider_metadata")
    if value.is_error and payload.get("success") is not False:
        payload["success"] = False
        payload.setdefault("error", "legacy tool message marked as error")
        debt.append("message_error_flag")
    text_parts: list[str] = []
    images: list[ResultImage] = []
    for block in value.content:
        if block.type == "text" and block.text:
            text_parts.append(block.text)
        elif block.type == "image":
            image, image_debt = _image_from_block(block)
            debt.extend(image_debt)
            if image is not None:
                images.append(image)
    normalized = _normalize_mapping(
        payload,
        tool_call_id=effective_id,
        name=effective_name,
    )
    if text_parts or images:
        normalized = NormalizedLegacyResult(
            result=ToolExecutionResult(
                status=normalized.result.status,
                text="\n".join(text_parts) if text_parts else normalized.result.text,
                data=normalized.result.data,
                images=tuple(images) or normalized.result.images,
                attachments=normalized.result.attachments,
                observations=normalized.result.observations,
                evidence_refs=normalized.result.evidence_refs,
                error=normalized.result.error,
                retryable=normalized.result.retryable,
                outcome_certainty=normalized.result.outcome_certainty,
                verification=normalized.result.verification,
                terminal=normalized.result.terminal,
                backend_attempted=normalized.result.backend_attempted,
            ),
            tool_call_id=effective_id,
            name=effective_name,
            legacy_message=value,
            migration_debt=LegacyAdapterDebt(
                tuple(dict.fromkeys((*normalized.debt.fields, *debt))),
                {**normalized.debt.details},
            ),
        )
    else:
        normalized = NormalizedLegacyResult(
            result=normalized.result,
            tool_call_id=effective_id,
            name=effective_name,
            legacy_message=value,
            migration_debt=LegacyAdapterDebt(
                tuple(dict.fromkeys((*normalized.debt.fields, *debt))),
                {**normalized.debt.details},
            ),
        )
    return normalized


def _failure_normalized(
    *,
    tool_call_id: str,
    name: str,
    code: str,
    message: str,
    debt: tuple[str, ...],
) -> NormalizedLegacyResult:
    return NormalizedLegacyResult(
        result=ToolExecutionResult(
            status=ToolExecutionStatus.INVALID,
            error=ToolExecutionError(code, message),
            backend_attempted=False,
        ),
        tool_call_id=tool_call_id,
        name=name,
        migration_debt=LegacyAdapterDebt(debt),
    )


def _image_from_block(block: ContentBlock) -> tuple[ResultImage | None, tuple[str, ...]]:
    source = block.source or {}
    if source.get("type") != "base64" or not isinstance(source.get("data"), str):
        return None, ("image_content_unavailable",)
    encoded = source["data"]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        return None, ("invalid_image_base64",)
    media_type = str(source.get("media_type") or "image/png")
    metadata = block.metadata or {}
    computed = hashlib.sha256(raw).hexdigest()
    declared = metadata.get("content_sha256")
    debt = ("image_content_hash_recomputed",) if declared and declared != computed else ()
    return (
        ResultImage(
            media_type=media_type,
            data_base64=encoded,
            content_sha256=computed,
            pixel_sha256=metadata.get("pixel_sha256"),
            observation_id=metadata.get("observation_id"),
        ),
        debt,
    )


def _images_from_data(data: Mapping[str, Any]) -> tuple[tuple[ResultImage, ...], tuple[str, ...]]:
    raw_images = data.get("images", ())
    if not isinstance(raw_images, Sequence) or isinstance(raw_images, (str, bytes, bytearray)):
        return (), ()
    images: list[ResultImage] = []
    debt: list[str] = []
    for raw in raw_images:
        if not isinstance(raw, Mapping):
            debt.append("unsupported_image_shape")
            continue
        encoded = raw.get("data_base64") or raw.get("data")
        if not isinstance(encoded, str):
            debt.append("image_content_unavailable")
            continue
        try:
            decoded = base64.b64decode(encoded, validate=True)
            images.append(
                ResultImage(
                    media_type=str(raw.get("media_type") or "image/png"),
                    data_base64=encoded,
                    content_sha256=hashlib.sha256(decoded).hexdigest(),
                    pixel_sha256=raw.get("pixel_sha256"),
                    observation_id=raw.get("observation_id"),
                )
            )
        except Exception:
            debt.append("invalid_image_payload")
    return tuple(images), tuple(debt)


def _terminal_from_data(data: Mapping[str, Any]) -> TerminalInfo | None:
    if data.get("terminal") is not True:
        return None
    classification = str(data.get("classification") or "legacy_terminal")
    return TerminalInfo(
        classification=_safe_id(classification).replace("-", "_") or "legacy_terminal",
        score_eligible=bool(data.get("score_eligible", False)),
        evidence_ref=(
            data.get("evidence_ref") if isinstance(data.get("evidence_ref"), str) else None
        ),
    )


def _first_text(data: Mapping[str, Any]) -> str:
    for key in ("text", "summary", "detail", "message"):
        value = data.get(key)
        if isinstance(value, str):
            return value
    return ""


def _object_from_text_content(content: Sequence[ContentBlock]) -> dict[str, Any] | None:
    text_blocks = [block.text for block in content if block.type == "text" and block.text]
    if len(text_blocks) != 1:
        return None
    try:
        value = json.loads(text_blocks[0])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _state_effects(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return (), ("invalid_state_effects",)
    effects: list[str] = []
    debt: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            debt.append("invalid_state_effects")
            continue
        normalized = _safe_effect(item)
        if normalized != item:
            debt.append("state_effects_normalized")
        if normalized and normalized not in effects:
            effects.append(normalized)
    return tuple(effects), tuple(dict.fromkeys(debt))


def _spec_reference(spec: Any) -> str:
    executor = getattr(spec, "executor", None)
    if executor is not None:
        module = getattr(executor, "__module__", "legacy")
        qualname = getattr(executor, "__qualname__", getattr(executor, "__name__", "tool"))
        return f"{module}.{qualname}"
    return f"{type(spec).__module__}.{type(spec).__qualname__}"


def _safe_alias(value: str) -> str:
    normalized = _SAFE_ALIAS_RE.sub("_", value.lower()).strip("_")
    if not normalized or not normalized[0].isalpha():
        normalized = f"tool_{normalized}" if normalized else "tool"
    return normalized[:64]


def _safe_id(value: str) -> str:
    normalized = _SAFE_ID_RE.sub("_", value.lower()).strip("_")
    if not normalized:
        return "tool"
    return normalized if normalized[0].isalpha() else f"tool_{normalized}"


def _safe_effect(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_.:/@+-]+", "_", value.lower()).strip("_")
    return normalized or "legacy.effect"


def _safe_internal_id(value: str) -> str:
    parts = [_safe_id(part) for part in value.split(".") if part]
    if len(parts) < 2:
        parts = ["legacy", *parts, "v1"]
    return ".".join(parts)


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    return value


__all__ = [
    "AdaptedLegacyTool",
    "LegacyAdapterDebt",
    "LegacyExecutorAdapter",
    "LegacyObserverAdapter",
    "LegacyToolExecutionContext",
    "NormalizedLegacyResult",
    "adapt_legacy_observer",
    "adapt_legacy_tool_spec",
    "adapt_tool_spec",
    "normalize_legacy_result",
    "normalize_legacy_tool_result",
]
