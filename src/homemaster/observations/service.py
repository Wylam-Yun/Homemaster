"""Media-independent, explicit model observation capture.

The service deliberately does not know how a backend stores frames or DOM
state.  Backends expose one ``capture`` operation; serialization and provider
binding are separate immutable steps.  Audit captures use a distinct record
type and can never be turned into model content or an observation binding.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from homemaster.providers.attempts import ProviderAttemptRecord
from homemaster.tools.contracts import (
    PostActionObservation,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionResult,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RASTER_MEDIA_TYPES = frozenset(
    {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "image/x-ms-bmp",
    }
)


class ObservationFreshnessError(ValueError):
    """Raised when a record cannot authorize the current backend state."""


class ObservationState(StrEnum):
    NEEDS_OBSERVE = "needs_observe"
    OBSERVED_UNBOUND = "observed_unbound"
    BOUND_READY = "bound_ready"
    TERMINAL = "terminal"


class ObservationBackend(Protocol):
    """Minimal borrowed-backend protocol used by explicit observation."""

    def capture(self) -> ObservationCapture | Mapping[str, object] | object: ...


@dataclass(frozen=True)
class ObservationCapture:
    """Raw backend capture before canonical serialization."""

    backend_id: str
    run_id: str
    generation: int
    state_sequence: int
    capture_event_sequence: int
    media_type: str
    content: object
    evidence_ref: str
    pixel_bytes: bytes | None = None
    pixel_sha256: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.backend_id, "backend_id")
        _require_text(self.run_id, "run_id")
        _require_text(self.media_type, "media_type")
        _require_text(self.evidence_ref, "evidence_ref")
        _require_sequence(self.generation, "generation")
        _require_sequence(self.state_sequence, "state_sequence")
        _require_sequence(self.capture_event_sequence, "capture_event_sequence")
        if isinstance(self.content, (bytearray, memoryview)):
            object.__setattr__(self, "content", bytes(self.content))
        if self.pixel_bytes is not None:
            object.__setattr__(self, "pixel_bytes", bytes(self.pixel_bytes))
        if self.pixel_sha256 is not None:
            _require_sha256(self.pixel_sha256, "pixel_sha256")


@dataclass(frozen=True)
class ObservationCaptureContext:
    """Explicit capture request passed to :class:`ObservationService`."""

    run_id: str
    internal_tool_id: str
    backend: ObservationBackend
    ledger: ObservationLedger

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        _require_text(self.internal_tool_id, "internal_tool_id")
        if not isinstance(self.ledger, ObservationLedger):
            raise TypeError("ledger must be an ObservationLedger")


@dataclass(frozen=True)
class SerializedObservation:
    """Frozen bytes and hashes produced without provider-specific concerns."""

    media_type: str
    content_bytes: bytes
    content_sha256: str
    pixel_sha256: str | None

    def __post_init__(self) -> None:
        _require_text(self.media_type, "media_type")
        object.__setattr__(self, "content_bytes", bytes(self.content_bytes))
        computed = hashlib.sha256(self.content_bytes).hexdigest()
        if self.content_sha256 != computed:
            raise ValueError("serialized content hash mismatch")
        if self.pixel_sha256 is not None:
            _require_sha256(self.pixel_sha256, "pixel_sha256")
        if _is_raster(self.media_type) and self.pixel_sha256 is None:
            raise ValueError("raster observation requires pixel_sha256")
        if not _is_raster(self.media_type) and self.pixel_sha256 is not None:
            raise ValueError("structured observation must not contain pixel_sha256")


@dataclass(frozen=True)
class ObservationRecord:
    observation_id: str
    internal_tool_id: str
    backend_id: str
    run_id: str
    generation: int
    state_sequence: int
    capture_event_sequence: int
    media_type: str
    content_bytes: bytes
    content_sha256: str
    pixel_sha256: str | None
    evidence_ref: str

    def __post_init__(self) -> None:
        for label, value in (
            ("observation_id", self.observation_id),
            ("internal_tool_id", self.internal_tool_id),
            ("backend_id", self.backend_id),
            ("run_id", self.run_id),
            ("media_type", self.media_type),
            ("evidence_ref", self.evidence_ref),
        ):
            _require_text(value, label)
        _require_sequence(self.generation, "generation")
        _require_sequence(self.state_sequence, "state_sequence")
        _require_sequence(self.capture_event_sequence, "capture_event_sequence")
        serialized = SerializedObservation(
            media_type=self.media_type,
            content_bytes=self.content_bytes,
            content_sha256=self.content_sha256,
            pixel_sha256=self.pixel_sha256,
        )
        object.__setattr__(self, "content_bytes", serialized.content_bytes)

    @property
    def is_raster(self) -> bool:
        return _is_raster(self.media_type)

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "internal_tool_id": self.internal_tool_id,
            "backend_id": self.backend_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "state_sequence": self.state_sequence,
            "capture_event_sequence": self.capture_event_sequence,
            "media_type": self.media_type,
            "content_sha256": self.content_sha256,
            "pixel_sha256": self.pixel_sha256,
            "evidence_ref": self.evidence_ref,
        }

    def to_content_block(self) -> Any:
        """Create model content only for an explicit model record."""
        from homemaster.agent.messages import ContentBlock

        metadata = {
            "observation_id": self.observation_id,
            "internal_tool_id": self.internal_tool_id,
            "backend_id": self.backend_id,
            "run_id": self.run_id,
            "generation": self.generation,
            "state_sequence": self.state_sequence,
            "capture_event_sequence": self.capture_event_sequence,
            "content_sha256": self.content_sha256,
            "pixel_sha256": self.pixel_sha256,
            "evidence_ref": self.evidence_ref,
        }
        if self.is_raster:
            return ContentBlock(
                type="image",
                source={
                    "type": "base64",
                    "media_type": self.media_type,
                    "data": base64.b64encode(self.content_bytes).decode("ascii"),
                },
                metadata=metadata,
            )
        return ContentBlock(
            type="text",
            text=self.content_bytes.decode("utf-8"),
            metadata={**metadata, "media_type": self.media_type},
        )


@dataclass(frozen=True)
class AuditCaptureRecord:
    """Internal evidence; intentionally has no model-content conversion."""

    backend_id: str
    run_id: str
    generation: int
    state_sequence: int
    capture_event_sequence: int
    media_type: str
    content_bytes: bytes
    content_sha256: str
    pixel_sha256: str | None
    evidence_ref: str

    def __post_init__(self) -> None:
        serialized = SerializedObservation(
            media_type=self.media_type,
            content_bytes=self.content_bytes,
            content_sha256=self.content_sha256,
            pixel_sha256=self.pixel_sha256,
        )
        object.__setattr__(self, "content_bytes", serialized.content_bytes)


@dataclass(frozen=True)
class ObservationRequestBinding:
    """Frozen provider binding for one observation and one canonical request."""

    observation_id: str
    request_sha256: str
    content_sha256: str
    pixel_sha256: str | None
    content_bytes: bytes
    backend_id: str
    run_id: str
    generation: int
    state_sequence: int
    capture_event_sequence: int

    def __post_init__(self) -> None:
        _require_sha256(self.request_sha256, "request_sha256")
        _require_sha256(self.content_sha256, "content_sha256")
        object.__setattr__(self, "content_bytes", bytes(self.content_bytes))
        if hashlib.sha256(self.content_bytes).hexdigest() != self.content_sha256:
            raise ValueError("provider binding content hash mismatch")
        if self.pixel_sha256 is not None:
            _require_sha256(self.pixel_sha256, "pixel_sha256")

    def to_metadata(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "observation_content_sha256": self.content_sha256,
            "observation_pixel_sha256": self.pixel_sha256,
            "observation_backend_id": self.backend_id,
            "observation_run_id": self.run_id,
            "observation_generation": self.generation,
            "observation_state_sequence": self.state_sequence,
            "observation_capture_event_sequence": self.capture_event_sequence,
        }


class ObservationSerializer:
    """Canonicalize backend content while remaining independent of providers."""

    def serialize(self, capture: ObservationCapture) -> SerializedObservation:
        if not isinstance(capture, ObservationCapture):
            capture = _coerce_capture(capture)
        content_bytes = _serialize_content(capture.content)
        pixel_sha256 = capture.pixel_sha256
        if _is_raster(capture.media_type):
            if capture.pixel_bytes is not None:
                pixel_sha256 = hashlib.sha256(capture.pixel_bytes).hexdigest()
            elif pixel_sha256 is None:
                pixel_sha256 = _decode_pixel_sha256(content_bytes, capture.media_type)
        else:
            pixel_sha256 = None
        return SerializedObservation(
            media_type=capture.media_type,
            content_bytes=content_bytes,
            content_sha256=hashlib.sha256(content_bytes).hexdigest(),
            pixel_sha256=pixel_sha256,
        )


@dataclass
class ObservationLedger:
    """Mutable per-run authority for observation freshness and binding."""

    run_id: str
    backend_id: str
    generation: int
    current_record: ObservationRecord | None = None
    current_binding: ObservationRequestBinding | None = None
    state: ObservationState = ObservationState.NEEDS_OBSERVE
    debt_action_event_sequence: int | None = None
    debt_post_state_sequence: int | None = None
    last_capture_event_sequence: int = -1

    def __post_init__(self) -> None:
        _require_text(self.run_id, "run_id")
        _require_text(self.backend_id, "backend_id")
        _require_sequence(self.generation, "generation")
        if self.last_capture_event_sequence < -1:
            raise ValueError("last_capture_event_sequence must be >= -1")

    @property
    def observation_debt(self) -> bool:
        return self.debt_action_event_sequence is not None

    def assert_context(self, *, run_id: str, backend_id: str, generation: int) -> None:
        if run_id != self.run_id:
            raise ObservationFreshnessError("foreign observation run")
        if backend_id != self.backend_id:
            raise ObservationFreshnessError("foreign observation backend")
        if generation != self.generation:
            raise ObservationFreshnessError("wrong observation generation")

    def commit_model_record(self, record: ObservationRecord) -> None:
        self.assert_context(
            run_id=record.run_id,
            backend_id=record.backend_id,
            generation=record.generation,
        )
        if record.capture_event_sequence <= self.last_capture_event_sequence:
            raise ObservationFreshnessError("observation capture event is stale or out of order")
        if self.observation_debt:
            assert self.debt_action_event_sequence is not None
            if record.capture_event_sequence < self.debt_action_event_sequence:
                raise ObservationFreshnessError("observation must follow action completion")
            if (
                self.debt_post_state_sequence is not None
                and record.state_sequence < self.debt_post_state_sequence
            ):
                raise ObservationFreshnessError("observation state predates backend action")
        self.current_record = record
        self.current_binding = None
        self.state = ObservationState.OBSERVED_UNBOUND
        self.last_capture_event_sequence = record.capture_event_sequence
        self.debt_action_event_sequence = None
        self.debt_post_state_sequence = None

    def bind_provider_request(
        self,
        record: ObservationRecord,
        attempt: ProviderAttemptRecord,
    ) -> ObservationRequestBinding:
        if self.state is not ObservationState.OBSERVED_UNBOUND or self.current_record != record:
            raise ObservationFreshnessError("observation is not the current unbound record")
        _validate_provider_attempt(record, attempt)
        binding = ObservationRequestBinding(
            observation_id=record.observation_id,
            request_sha256=attempt.request_sha256,
            content_sha256=record.content_sha256,
            pixel_sha256=record.pixel_sha256,
            content_bytes=record.content_bytes,
            backend_id=record.backend_id,
            run_id=record.run_id,
            generation=record.generation,
            state_sequence=record.state_sequence,
            capture_event_sequence=record.capture_event_sequence,
        )
        self.current_binding = binding
        self.state = ObservationState.BOUND_READY
        return binding

    def before_action(self, definition: ToolDefinition, context: ToolExecutionContext) -> bool:
        policy = definition.verification_policy
        if policy.requires_pre_observation != "current_bound":
            return True
        if self.state is not ObservationState.BOUND_READY or self.current_binding is None:
            return False
        backend_state = _backend_sequence(context.backend, "state_sequence")
        if backend_state is not None and backend_state != self.current_binding.state_sequence:
            self.invalidate("backend state differs from observation binding")
            return False
        backend_event = _backend_sequence(context.backend, "event_sequence")
        if backend_event is None:
            backend_event = _backend_sequence(context.backend, "capture_event_sequence")
        if (
            backend_event is not None
            and backend_event != self.current_binding.capture_event_sequence
        ):
            self.invalidate("backend event differs from observation binding")
            return False
        return True

    def after_action(
        self,
        definition: ToolDefinition,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> None:
        if (
            definition.verification_policy.post_action_observation
            is not PostActionObservation.FRESH_AFTER_BACKEND_ADVANCE
            or not result.backend_attempted
        ):
            return
        action_event = _backend_sequence(context.backend, "event_sequence")
        if action_event is None:
            action_event = _backend_sequence(context.backend, "capture_event_sequence")
        if action_event is None:
            raise ObservationFreshnessError("backend action lacks an event sequence")
        post_state = _backend_sequence(context.backend, "state_sequence")
        self.mark_observation_debt(
            action_completion_event_sequence=action_event,
            post_state_sequence=post_state,
        )

    def mark_observation_debt(
        self,
        *,
        action_completion_event_sequence: int,
        post_state_sequence: int | None,
    ) -> None:
        _require_sequence(action_completion_event_sequence, "action_completion_event_sequence")
        if post_state_sequence is not None:
            _require_sequence(post_state_sequence, "post_state_sequence")
        self.current_record = None
        self.current_binding = None
        self.state = ObservationState.NEEDS_OBSERVE
        self.debt_action_event_sequence = action_completion_event_sequence
        self.debt_post_state_sequence = post_state_sequence

    def invalidate(self, reason: str = "") -> None:
        del reason
        self.current_record = None
        self.current_binding = None
        if self.state is not ObservationState.TERMINAL:
            self.state = ObservationState.NEEDS_OBSERVE

    def close_terminal(self) -> None:
        self.current_record = None
        self.current_binding = None
        self.debt_action_event_sequence = None
        self.debt_post_state_sequence = None
        self.state = ObservationState.TERMINAL


class ObservationService:
    """Shared serializer/capture service with no provider or domain ownership."""

    def __init__(self, *, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: f"obs-{uuid.uuid4().hex}")
        self._serializer = ObservationSerializer()

    @property
    def serializer(self) -> ObservationSerializer:
        return self._serializer

    async def capture_for_model(
        self,
        context: ObservationCaptureContext | ToolExecutionContext,
    ) -> ObservationRecord:
        request = _capture_context(context)
        _assert_backend_context(request)
        raw = await _maybe_await(request.backend.capture())
        capture = _coerce_capture(raw)
        request.ledger.assert_context(
            run_id=capture.run_id,
            backend_id=capture.backend_id,
            generation=capture.generation,
        )
        serialized = self._serializer.serialize(capture)
        record = ObservationRecord(
            observation_id=self._id_factory(),
            internal_tool_id=request.internal_tool_id,
            backend_id=capture.backend_id,
            run_id=capture.run_id,
            generation=capture.generation,
            state_sequence=capture.state_sequence,
            capture_event_sequence=capture.capture_event_sequence,
            media_type=serialized.media_type,
            content_bytes=serialized.content_bytes,
            content_sha256=serialized.content_sha256,
            pixel_sha256=serialized.pixel_sha256,
            evidence_ref=capture.evidence_ref,
        )
        request.ledger.commit_model_record(record)
        return record

    async def capture_for_audit(
        self,
        context: ObservationCaptureContext | ToolExecutionContext,
    ) -> AuditCaptureRecord:
        request = _capture_context(context)
        _assert_backend_context(request)
        raw = await _maybe_await(request.backend.capture())
        capture = _coerce_capture(raw)
        request.ledger.assert_context(
            run_id=capture.run_id,
            backend_id=capture.backend_id,
            generation=capture.generation,
        )
        serialized = self._serializer.serialize(capture)
        return AuditCaptureRecord(
            backend_id=capture.backend_id,
            run_id=capture.run_id,
            generation=capture.generation,
            state_sequence=capture.state_sequence,
            capture_event_sequence=capture.capture_event_sequence,
            media_type=serialized.media_type,
            content_bytes=serialized.content_bytes,
            content_sha256=serialized.content_sha256,
            pixel_sha256=serialized.pixel_sha256,
            evidence_ref=capture.evidence_ref,
        )

    def bind_provider_request(
        self,
        ledger: ObservationLedger,
        record: ObservationRecord,
        attempt: ProviderAttemptRecord,
    ) -> ObservationRequestBinding:
        return ledger.bind_provider_request(record, attempt)

    def commit_provider_attempt(
        self,
        ledger: ObservationLedger,
        attempt: ProviderAttemptRecord,
    ) -> ObservationRequestBinding | None:
        """Bind the current observation only at a successful provider boundary."""

        if ledger.state is ObservationState.BOUND_READY:
            return ledger.current_binding
        record = ledger.current_record
        if ledger.state is not ObservationState.OBSERVED_UNBOUND or record is None:
            return None
        return ledger.bind_provider_request(record, attempt)

    async def before_action(
        self,
        definition: ToolDefinition,
        context: ToolExecutionContext,
    ) -> bool:
        ledger = context.observation
        if not isinstance(ledger, ObservationLedger):
            return definition.verification_policy.requires_pre_observation != "current_bound"
        backend_id = _backend_identity(context.backend, "backend_id") or ledger.backend_id
        generation = _backend_sequence(context.backend, "generation")
        ledger.assert_context(
            run_id=context.run_id,
            backend_id=backend_id,
            generation=ledger.generation if generation is None else generation,
        )
        return ledger.before_action(definition, context)

    async def after_action(
        self,
        definition: ToolDefinition,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> None:
        ledger = context.observation
        if isinstance(ledger, ObservationLedger):
            ledger.after_action(definition, result, context)


@dataclass
class ObservationProviderCommitter:
    """Run-scoped bridge from AgentRuntime provider commits to a ledger."""

    service: ObservationService
    ledger: ObservationLedger

    def commit_successful_response(
        self,
        *,
        attempt: ProviderAttemptRecord,
    ) -> ObservationRequestBinding | None:
        return self.service.commit_provider_attempt(self.ledger, attempt)

    def invalidate(self, reason: str) -> None:
        self.ledger.invalidate(reason)


def _validate_provider_attempt(
    record: ObservationRecord,
    attempt: ProviderAttemptRecord,
) -> None:
    if not isinstance(attempt, ProviderAttemptRecord):
        raise TypeError("attempt must be ProviderAttemptRecord")
    if (
        not attempt.response_completed
        or attempt.stripped_images
        or attempt.error_type is not None
        or attempt.cause_code is not None
    ):
        raise ObservationFreshnessError(
            "only a complete unmodified provider attempt can bind an observation"
        )
    _require_sha256(attempt.request_sha256, "request_sha256")
    matching = [
        binding
        for binding in attempt.outbound_observations
        if (
            binding.observation_id == record.observation_id
            and binding.content_sha256 == record.content_sha256
            and binding.observation_content_sha256 == record.content_sha256
            and binding.observation_pixel_sha256 == record.pixel_sha256
            and binding.observation_backend_id == record.backend_id
            and binding.observation_run_id == record.run_id
            and binding.observation_generation == record.generation
            and binding.observation_state_sequence == record.state_sequence
            and binding.observation_capture_event_sequence
            == record.capture_event_sequence
        )
    ]
    if not matching:
        raise ObservationFreshnessError(
            "provider attempt does not contain the exact current observation"
        )


def _capture_context(
    context: ObservationCaptureContext | ToolExecutionContext,
) -> ObservationCaptureContext:
    if isinstance(context, ObservationCaptureContext):
        return context
    ledger = context.observation
    if not isinstance(ledger, ObservationLedger):
        raise TypeError("ToolExecutionContext.observation must be an ObservationLedger")
    if context.backend is None or not callable(getattr(context.backend, "capture", None)):
        raise TypeError("observation context backend must implement capture()")
    return ObservationCaptureContext(
        run_id=context.run_id,
        internal_tool_id=context.internal_tool_id,
        backend=context.backend,
        ledger=ledger,
    )


def _assert_backend_context(context: ObservationCaptureContext) -> None:
    backend_id = _backend_identity(context.backend, "backend_id")
    generation = _backend_sequence(context.backend, "generation")
    if backend_id is not None and backend_id != context.ledger.backend_id:
        raise ObservationFreshnessError("foreign observation backend")
    if generation is not None and generation != context.ledger.generation:
        raise ObservationFreshnessError("wrong observation generation")
    context.ledger.assert_context(
        run_id=context.run_id,
        backend_id=context.ledger.backend_id,
        generation=context.ledger.generation,
    )


def _coerce_capture(value: object) -> ObservationCapture:
    if isinstance(value, ObservationCapture):
        return value
    if isinstance(value, Mapping):
        payload = dict(value)
        aliases = {
            "generation": payload.get("run_generation", payload.get("generation")),
            "state_sequence": payload.get("backend_state_sequence", payload.get("state_sequence")),
            "capture_event_sequence": payload.get(
                "capture_event_sequence", payload.get("event_sequence")
            ),
            "content": payload.get("content", payload.get("bytes", payload.get("data"))),
        }
        return ObservationCapture(
            backend_id=str(payload["backend_id"]),
            run_id=str(payload["run_id"]),
            generation=int(aliases["generation"]),
            state_sequence=int(aliases["state_sequence"]),
            capture_event_sequence=int(aliases["capture_event_sequence"]),
            media_type=str(payload["media_type"]),
            content=aliases["content"],
            evidence_ref=str(payload.get("evidence_ref", "backend-capture")),
            pixel_bytes=payload.get("pixel_bytes"),
            pixel_sha256=payload.get("pixel_sha256"),
        )
    raise TypeError("backend capture must return ObservationCapture or a mapping")


def _serialize_content(content: object) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, (bytearray, memoryview)):
        return bytes(content)
    try:
        return json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TypeError("structured observation content is not JSON serializable") from exc


def _decode_pixel_sha256(content: bytes, media_type: str) -> str:
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(content)) as image:
            return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()
    except Exception as exc:
        raise ValueError(
            f"cannot derive pixel hash for {media_type}; backend must provide "
            "pixel_bytes or pixel_sha256"
        ) from exc


def _is_raster(media_type: str) -> bool:
    return media_type.lower() in _RASTER_MEDIA_TYPES


def _backend_identity(backend: object | None, name: str) -> object | None:
    if backend is None:
        return None
    value = getattr(backend, name, None)
    if callable(value):
        value = value()
    return value


def _backend_sequence(backend: object | None, name: str) -> int | None:
    value = _backend_identity(backend, name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ObservationFreshnessError(f"backend {name} must be a non-negative integer")
    return value


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _require_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty")


def _require_sequence(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _require_sha256(value: object, label: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


__all__ = [
    "AuditCaptureRecord",
    "ObservationBackend",
    "ObservationCapture",
    "ObservationCaptureContext",
    "ObservationFreshnessError",
    "ObservationLedger",
    "ObservationRecord",
    "ObservationRequestBinding",
    "ObservationSerializer",
    "ObservationService",
    "ObservationState",
    "SerializedObservation",
]
