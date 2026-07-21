"""Canonical immutable contracts for tool definition and execution."""

from __future__ import annotations

import base64
import binascii
import hashlib
import inspect
import json
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal, Protocol, runtime_checkable

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel

from homemaster.agent.messages import ContentBlock, ToolResultMessage

_INTERNAL_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_MODEL_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_.:/@+-]*$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ExecutionProof(StrEnum):
    NONE = "none"
    STRUCTURED_RECEIPT = "structured_receipt"
    EXTERNAL_STATE = "external_state"


class PostActionObservation(StrEnum):
    NONE = "none"
    FRESH_AFTER_BACKEND_ADVANCE = "fresh_after_backend_advance"


class TerminalRule(StrEnum):
    NORMAL = "normal"
    EXTERNAL_TERMINAL_OWNER = "external_terminal_owner"


class ExecutionBackend(StrEnum):
    IN_PROCESS = "in_process"
    ENVIRONMENT = "environment"
    MCP = "mcp"
    PLUGIN = "plugin"


class ConcurrencyPolicy(StrEnum):
    PARALLEL = "parallel"
    SERIALIZED = "serialized"
    RESOURCE_KEY = "resource_key"


class ToolExecutionStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    INVALID = "invalid"
    DENIED = "denied"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"
    OBSERVATION_REQUIRED = "observation_required"
    VERIFICATION_PENDING = "verification_pending"


class OutcomeCertainty(StrEnum):
    CONFIRMED = "confirmed"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class VerificationStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True)
class VerificationPolicy:
    execution_proof: ExecutionProof = ExecutionProof.NONE
    requires_pre_observation: Literal[False, "current_bound"] = False
    post_action_observation: PostActionObservation = PostActionObservation.NONE
    terminal_rule: TerminalRule = TerminalRule.NORMAL

    def __post_init__(self) -> None:
        if not isinstance(self.execution_proof, ExecutionProof):
            raise TypeError("execution_proof must be ExecutionProof")
        if not isinstance(self.post_action_observation, PostActionObservation):
            raise TypeError("post_action_observation must be PostActionObservation")
        if not isinstance(self.terminal_rule, TerminalRule):
            raise TypeError("terminal_rule must be TerminalRule")
        if self.requires_pre_observation not in {False, "current_bound"}:
            raise ValueError("requires_pre_observation must be false or current_bound")

    def to_dict(self) -> dict[str, object]:
        return {
            "execution_proof": self.execution_proof.value,
            "requires_pre_observation": self.requires_pre_observation,
            "post_action_observation": self.post_action_observation.value,
            "terminal_rule": self.terminal_rule.value,
        }


@dataclass(frozen=True)
class ToolProvenance:
    source: str
    reference: str

    def __post_init__(self) -> None:
        _require_token(self.source, label="provenance source")
        _require_nonempty(self.reference, label="provenance reference")

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "reference": self.reference}


@dataclass(frozen=True)
class ToolDefinition:
    internal_id: str
    model_alias: str
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    verification_policy: VerificationPolicy
    provenance: ToolProvenance
    version: str
    execution_backend: ExecutionBackend = ExecutionBackend.IN_PROCESS
    timeout_s: float | None = None
    concurrency_policy: ConcurrencyPolicy = ConcurrencyPolicy.PARALLEL
    resource_key: str | None = None
    state_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.verification_policy, VerificationPolicy):
            raise TypeError("verification_policy must be VerificationPolicy")
        if not isinstance(self.provenance, ToolProvenance):
            raise TypeError("provenance must be ToolProvenance")
        if not isinstance(self.execution_backend, ExecutionBackend):
            raise TypeError("execution_backend must be ExecutionBackend")
        if not isinstance(self.concurrency_policy, ConcurrencyPolicy):
            raise TypeError("concurrency_policy must be ConcurrencyPolicy")
        if _INTERNAL_ID_RE.fullmatch(self.internal_id) is None:
            raise ValueError("internal_id must be a stable namespaced lowercase id")
        if _MODEL_ALIAS_RE.fullmatch(self.model_alias) is None:
            raise ValueError("model_alias must be a lowercase provider-safe name")
        _require_nonempty(self.description, label="description")
        if _VERSION_RE.fullmatch(self.version) is None:
            raise ValueError("version must be a semantic version")
        if self.timeout_s is not None and (
            isinstance(self.timeout_s, bool)
            or not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a finite positive number")
        if self.concurrency_policy is ConcurrencyPolicy.RESOURCE_KEY:
            _require_token(self.resource_key, label="resource_key")
        elif self.resource_key is not None:
            raise ValueError("resource_key requires concurrency_policy=resource_key")
        if isinstance(self.state_effects, str):
            raise TypeError("state_effects must be a sequence of tokens")
        effects = tuple(self.state_effects)
        if len(effects) != len(set(effects)):
            raise ValueError("state_effects must be unique")
        for effect in effects:
            _require_token(effect, label="state effect")
        object.__setattr__(self, "state_effects", effects)
        input_schema = _freeze_json_object(self.input_schema, "input")
        output_schema = _freeze_json_object(self.output_schema, "output")
        try:
            Draft202012Validator.check_schema(_thaw_json(input_schema))
            Draft202012Validator.check_schema(_thaw_json(output_schema))
        except SchemaError as exc:
            raise ValueError(f"invalid JSON Schema: {exc.message}") from exc
        object.__setattr__(self, "input_schema", input_schema)
        object.__setattr__(
            self,
            "output_schema",
            output_schema,
        )

    def to_model_manifest(self) -> dict[str, object]:
        """Return the same provider projection seeded by OpenHarness BaseTool."""

        return {
            "name": self.model_alias,
            "description": self.description,
            "input_schema": _thaw_json(self.input_schema),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "internal_id": self.internal_id,
            "model_alias": self.model_alias,
            "description": self.description,
            "input_schema": _thaw_json(self.input_schema),
            "output_schema": _thaw_json(self.output_schema),
            "verification_policy": self.verification_policy.to_dict(),
            "provenance": self.provenance.to_dict(),
            "version": self.version,
            "execution_backend": self.execution_backend.value,
            "timeout_s": self.timeout_s,
            "concurrency_policy": self.concurrency_policy.value,
            "resource_key": self.resource_key,
            "state_effects": list(self.state_effects),
        }

    @property
    def snapshot_sha256(self) -> str:
        encoded = json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PermissionSubject:
    subject_id: str
    channel: str
    roles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty(self.subject_id, label="permission subject id")
        _require_token(self.channel, label="permission channel")
        if isinstance(self.roles, str):
            raise TypeError("permission roles must be a sequence of tokens")
        roles = tuple(self.roles)
        if len(roles) != len(set(roles)):
            raise ValueError("permission roles must be unique")
        for role in roles:
            _require_token(role, label="permission role")
        object.__setattr__(self, "roles", roles)


@runtime_checkable
class ToolViewHandle(Protocol):
    @property
    def view_id(self) -> str: ...

    def is_enabled(self, internal_id: str) -> bool: ...


@runtime_checkable
class CancellationHandle(Protocol):
    @property
    def cancelled(self) -> bool: ...


@runtime_checkable
class DeadlineHandle(Protocol):
    def remaining_s(self) -> float | None: ...


@dataclass(frozen=True)
class ToolExecutionContext:
    session_id: str
    run_id: str
    turn_index: int
    tool_call_id: str
    internal_tool_id: str
    tool_view: ToolViewHandle
    permission_subject: PermissionSubject
    backend: object | None
    deadline: DeadlineHandle | None
    cancellation: CancellationHandle | None
    observation: object | None
    domain_observer: object | None

    def __post_init__(self) -> None:
        for label, value in (
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("tool_call_id", self.tool_call_id),
        ):
            _require_nonempty(value, label=label)
        if not isinstance(self.turn_index, int) or isinstance(self.turn_index, bool):
            raise TypeError("turn_index must be an integer")
        if self.turn_index < 0:
            raise ValueError("turn_index must be non-negative")
        if _INTERNAL_ID_RE.fullmatch(self.internal_tool_id) is None:
            raise ValueError("internal_tool_id must be a stable namespaced lowercase id")
        if not isinstance(self.tool_view, ToolViewHandle):
            raise TypeError("tool_view must implement ToolViewHandle")
        if not isinstance(self.permission_subject, PermissionSubject):
            raise TypeError("permission_subject must be PermissionSubject")
        if self.deadline is not None and not isinstance(self.deadline, DeadlineHandle):
            raise TypeError("deadline must implement DeadlineHandle")
        if self.cancellation is not None and not isinstance(
            self.cancellation, CancellationHandle
        ):
            raise TypeError("cancellation must implement CancellationHandle")


@dataclass(frozen=True)
class ToolExecutionError:
    code: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_token(self.code, label="error code")
        _require_nonempty(self.message, label="error message")
        object.__setattr__(self, "details", _freeze_json_object(self.details, "error details"))

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "details": _thaw_json(self.details),
        }


@dataclass(frozen=True)
class ResultImage:
    media_type: str
    data_base64: str
    content_sha256: str
    pixel_sha256: str | None = None
    observation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.media_type.startswith("image/"):
            raise ValueError("result image media_type must be image/*")
        content = _decode_base64(self.data_base64, label="result image")
        if hashlib.sha256(content).hexdigest() != _require_sha256(
            self.content_sha256, label="result image content hash"
        ):
            raise ValueError("result image content hash mismatch")
        if self.pixel_sha256 is not None:
            _require_sha256(self.pixel_sha256, label="result image pixel hash")
        if self.observation_id is not None:
            _require_nonempty(self.observation_id, label="result image observation id")

    def to_dict(self) -> dict[str, object]:
        return {
            "media_type": self.media_type,
            "data_base64": self.data_base64,
            "content_sha256": self.content_sha256,
            "pixel_sha256": self.pixel_sha256,
            "observation_id": self.observation_id,
        }


@dataclass(frozen=True)
class ResultAttachment:
    filename: str
    media_type: str
    data_base64: str
    content_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty(self.filename, label="attachment filename")
        if "/" in self.filename or "\\" in self.filename:
            raise ValueError("attachment filename must not contain a path")
        _require_nonempty(self.media_type, label="attachment media type")
        content = _decode_base64(self.data_base64, label="result attachment")
        if hashlib.sha256(content).hexdigest() != _require_sha256(
            self.content_sha256, label="attachment content hash"
        ):
            raise ValueError("attachment content hash mismatch")

    def to_dict(self) -> dict[str, str]:
        return {
            "filename": self.filename,
            "media_type": self.media_type,
            "data_base64": self.data_base64,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class ObservationReference:
    observation_id: str
    evidence_ref: str
    content_sha256: str

    def __post_init__(self) -> None:
        _require_nonempty(self.observation_id, label="observation id")
        _require_nonempty(self.evidence_ref, label="observation evidence ref")
        _require_sha256(self.content_sha256, label="observation content hash")

    def to_dict(self) -> dict[str, str]:
        return {
            "observation_id": self.observation_id,
            "evidence_ref": self.evidence_ref,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class VerificationRecord:
    status: VerificationStatus = VerificationStatus.NOT_REQUESTED
    detail: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, VerificationStatus):
            raise TypeError("verification status must be VerificationStatus")
        refs = _validated_refs(self.evidence_refs, label="verification evidence")
        object.__setattr__(self, "evidence_refs", refs)
        if self.status is VerificationStatus.NOT_REQUESTED:
            if self.detail is not None or refs:
                raise ValueError("not_requested verification cannot contain evidence")
        elif self.detail is not None:
            _require_nonempty(self.detail, label="verification detail")
        if self.status in {VerificationStatus.PASSED, VerificationStatus.FAILED} and not refs:
            raise ValueError("completed verification requires evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "detail": self.detail,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class TerminalInfo:
    classification: str
    score_eligible: bool
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        _require_token(self.classification, label="terminal classification")
        if not isinstance(self.score_eligible, bool):
            raise TypeError("terminal score_eligible must be a boolean")
        if self.evidence_ref is not None:
            _require_nonempty(self.evidence_ref, label="terminal evidence ref")

    def to_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "score_eligible": self.score_eligible,
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class ToolExecutionResult:
    status: ToolExecutionStatus
    text: str = ""
    data: Mapping[str, object] = field(default_factory=dict)
    images: tuple[ResultImage, ...] = ()
    attachments: tuple[ResultAttachment, ...] = ()
    observations: tuple[ObservationReference, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error: ToolExecutionError | None = None
    retryable: bool = False
    outcome_certainty: OutcomeCertainty = OutcomeCertainty.CONFIRMED
    verification: VerificationRecord = field(default_factory=VerificationRecord)
    terminal: TerminalInfo | None = None
    backend_attempted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, ToolExecutionStatus):
            raise TypeError("status must be ToolExecutionStatus")
        if not isinstance(self.outcome_certainty, OutcomeCertainty):
            raise TypeError("outcome_certainty must be OutcomeCertainty")
        if not isinstance(self.verification, VerificationRecord):
            raise TypeError("verification must be VerificationRecord")
        if self.error is not None and not isinstance(self.error, ToolExecutionError):
            raise TypeError("error must be ToolExecutionError")
        if self.terminal is not None and not isinstance(self.terminal, TerminalInfo):
            raise TypeError("terminal must be TerminalInfo")
        if not isinstance(self.text, str):
            raise TypeError("text must be a string")
        if not isinstance(self.retryable, bool):
            raise TypeError("retryable must be a boolean")
        if not isinstance(self.backend_attempted, bool):
            raise TypeError("backend_attempted must be a boolean")
        object.__setattr__(self, "data", _freeze_json_object(self.data, "result data"))
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(self, "attachments", tuple(self.attachments))
        object.__setattr__(self, "observations", tuple(self.observations))
        if any(not isinstance(image, ResultImage) for image in self.images):
            raise TypeError("images must contain ResultImage values")
        if any(not isinstance(item, ResultAttachment) for item in self.attachments):
            raise TypeError("attachments must contain ResultAttachment values")
        if any(not isinstance(item, ObservationReference) for item in self.observations):
            raise TypeError("observations must contain ObservationReference values")
        object.__setattr__(
            self,
            "evidence_refs",
            _validated_refs(self.evidence_refs, label="result evidence"),
        )
        self._validate_status()

    @property
    def success(self) -> bool:
        return self.status is ToolExecutionStatus.SUCCESS

    @property
    def is_error(self) -> bool:
        return not self.success

    @property
    def failure_reason(self) -> str | None:
        return self.error.message if self.error is not None else None

    def _validate_status(self) -> None:
        if self.status is ToolExecutionStatus.SUCCESS:
            if self.error is not None or self.retryable:
                raise ValueError("successful result cannot contain an error or be retryable")
        elif self.error is None:
            raise ValueError("non-success result requires a typed error")
        if self.retryable and self.status is not ToolExecutionStatus.FAILURE:
            raise ValueError("only a confirmed failure may be retryable")
        if self.status is ToolExecutionStatus.OUTCOME_UNKNOWN:
            if not self.backend_attempted or self.outcome_certainty is not OutcomeCertainty.UNKNOWN:
                raise ValueError(
                    "outcome_unknown requires an attempted backend and unknown certainty"
                )
            if self.retryable:
                raise ValueError("outcome_unknown must not be automatically retryable")
        elif self.outcome_certainty is OutcomeCertainty.UNKNOWN:
            raise ValueError("unknown certainty requires status=outcome_unknown")
        pre_backend_statuses = {
            ToolExecutionStatus.INVALID,
            ToolExecutionStatus.DENIED,
            ToolExecutionStatus.CANCELLED,
            ToolExecutionStatus.OBSERVATION_REQUIRED,
        }
        if self.status in pre_backend_statuses and self.backend_attempted:
            raise ValueError(f"{self.status.value} cannot claim a backend attempt")
        if self.verification.status is VerificationStatus.PASSED and not self.success:
            raise ValueError("passed verification requires a successful result")
        if self.verification.status is VerificationStatus.FAILED and self.success:
            raise ValueError("failed verification cannot accompany a successful result")
        if self.verification.status is VerificationStatus.PENDING:
            if self.status is not ToolExecutionStatus.VERIFICATION_PENDING:
                raise ValueError("pending verification requires verification_pending status")
        elif self.status is ToolExecutionStatus.VERIFICATION_PENDING:
            raise ValueError("verification_pending status requires pending verification")
        if self.terminal is not None and self.status not in {
            ToolExecutionStatus.SUCCESS,
            ToolExecutionStatus.FAILURE,
            ToolExecutionStatus.OUTCOME_UNKNOWN,
        }:
            raise ValueError("terminal information is invalid for a pre-execution result")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "success": self.success,
            "text": self.text,
            "data": _thaw_json(self.data),
            "images": [image.to_dict() for image in self.images],
            "attachments": [attachment.to_dict() for attachment in self.attachments],
            "observations": [observation.to_dict() for observation in self.observations],
            "evidence_refs": list(self.evidence_refs),
            "error": self.error.to_dict() if self.error is not None else None,
            "failure_reason": self.failure_reason,
            "retryable": self.retryable,
            "outcome_certainty": self.outcome_certainty.value,
            "verification": self.verification.to_dict(),
            "terminal": self.terminal.to_dict() if self.terminal is not None else None,
            "backend_attempted": self.backend_attempted,
        }

    def to_message(self, *, tool_call_id: str, name: str) -> ToolResultMessage:
        _require_nonempty(tool_call_id, label="tool call id")
        _require_nonempty(name, label="tool result name")
        payload = self.to_dict()
        content = [
            ContentBlock(
                text=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )
        ]
        if self.text and self.observations:
            metadata = _observation_content_metadata(self.observations[0], self.data)
            if _has_complete_observation_identity(metadata):
                content.append(
                    ContentBlock(
                        text=self.text,
                        metadata={**metadata, "media_type": self.data.get("media_type")},
                    )
                )
        for image in self.images:
            reference = next(
                (
                    observation
                    for observation in self.observations
                    if observation.observation_id == image.observation_id
                ),
                None,
            )
            metadata = (
                _observation_content_metadata(reference, self.data)
                if reference is not None
                else {}
            )
            content.append(
                ContentBlock(
                    type="image",
                    source={
                        "type": "base64",
                        "media_type": image.media_type,
                        "data": image.data_base64,
                    },
                    metadata={
                        "content_sha256": image.content_sha256,
                        "pixel_sha256": image.pixel_sha256,
                        "observation_id": image.observation_id,
                        **metadata,
                    },
                )
            )
        return ToolResultMessage(
            tool_call_id=tool_call_id,
            name=name,
            content=content,
            is_error=self.is_error,
            data=payload,
        )


def _observation_content_metadata(
    reference: ObservationReference,
    data: Mapping[str, object],
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "observation_id": reference.observation_id,
        "observation_content_sha256": reference.content_sha256,
    }
    if data.get("observation_id") != reference.observation_id:
        return metadata
    names = {
        "backend_id": "observation_backend_id",
        "run_id": "observation_run_id",
        "generation": "observation_generation",
        "state_sequence": "observation_state_sequence",
        "capture_event_sequence": "observation_capture_event_sequence",
        "pixel_sha256": "observation_pixel_sha256",
    }
    for source, target in names.items():
        value = data.get(source)
        if value is not None:
            metadata[target] = value
    return metadata


def _has_complete_observation_identity(metadata: Mapping[str, object]) -> bool:
    return all(
        name in metadata
        for name in (
            "observation_id",
            "observation_content_sha256",
            "observation_backend_id",
            "observation_run_id",
            "observation_generation",
            "observation_state_sequence",
            "observation_capture_event_sequence",
        )
    )


class ToolExecutor(Protocol):
    def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> Awaitable[ToolExecutionResult]: ...


class ToolVerifier(Protocol):
    def verify(
        self,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> Awaitable[VerificationRecord]: ...


@dataclass(frozen=True)
class RegisteredTool:
    definition: ToolDefinition
    executor: ToolExecutor
    verifier: ToolVerifier | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ToolDefinition):
            raise TypeError("definition must be ToolDefinition")
        if not inspect.iscoroutinefunction(getattr(self.executor, "execute", None)):
            raise TypeError("executor must implement async execute")
        if self.verifier is not None and not inspect.iscoroutinefunction(
            getattr(self.verifier, "verify", None)
        ):
            raise TypeError("verifier must implement async verify")
        if (
            self.definition.verification_policy.execution_proof is ExecutionProof.EXTERNAL_STATE
            and self.verifier is None
        ):
            raise ValueError("external_state verification requires a verifier")

    def to_definition_snapshot(self) -> dict[str, object]:
        return self.definition.to_dict()


class BaseTool(ABC):
    """Adapted from OpenHarness BaseTool at the locked upstream commit."""

    name: str
    description: str
    input_model: type[BaseModel]

    @abstractmethod
    async def execute(
        self,
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        """Execute the tool."""

    def is_read_only(self, arguments: BaseModel) -> bool:
        """Return whether the invocation is read-only."""

        del arguments
        return False

    def to_api_schema(self) -> dict[str, Any]:
        """Return the tool schema expected by the Anthropic Messages API."""

        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


def _freeze_json_object(value: Mapping[str, object], label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    frozen = _freeze_json(value, label=label)
    assert isinstance(frozen, Mapping)
    return frozen


def _freeze_json(value: object, *, label: str) -> object:
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{label} keys must be strings")
            frozen[key] = _freeze_json(item, label=label)
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return tuple(_freeze_json(item, label=label) for item in value)
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise TypeError(f"{label} must contain only finite JSON values")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validated_refs(values: Sequence[str], *, label: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError(f"{label} references must be a sequence of strings")
    refs = tuple(values)
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} references must be unique")
    for ref in refs:
        _require_nonempty(ref, label=f"{label} reference")
    return refs


def _require_nonempty(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_token(value: object, *, label: str) -> str:
    text = _require_nonempty(value, label=label)
    if _TOKEN_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a normalized token")
    return text


def _require_sha256(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _decode_base64(value: object, *, label: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} data must be non-empty base64")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} data must be valid base64") from exc
