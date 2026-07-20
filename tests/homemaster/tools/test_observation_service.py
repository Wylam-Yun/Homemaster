from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.observations import (
    AuditCaptureRecord,
    ObservationCapture,
    ObservationCaptureContext,
    ObservationFreshnessError,
    ObservationLedger,
    ObservationService,
    ObservationState,
)
from homemaster.providers.llm_client import _attempt_record


class Backend:
    def __init__(self, *, backend_id: str = "backend-a", generation: int = 3) -> None:
        self.backend_id = backend_id
        self.generation = generation
        self.state_sequence = 7
        self.event_sequence = 11
        self.captures = 0
        self.next_capture: ObservationCapture | None = None

    def capture(self) -> ObservationCapture:
        self.captures += 1
        if self.next_capture is None:
            return ObservationCapture(
                backend_id=self.backend_id,
                run_id="run-1",
                generation=self.generation,
                state_sequence=self.state_sequence,
                capture_event_sequence=self.event_sequence,
                media_type="image/png",
                content=b"encoded-image",
                pixel_bytes=b"decoded-pixels",
                evidence_ref=f"capture/{self.captures}",
            )
        return self.next_capture


def _service_and_context(backend: Backend):
    ledger = ObservationLedger("run-1", "backend-a", 3)
    service = ObservationService(id_factory=iter(("obs-1", "obs-2", "obs-3")).__next__)
    context = ObservationCaptureContext("run-1", "alfworld.observe.v1", backend, ledger)
    return service, context, ledger


def _attempt_for(record, request_sha256: str):
    block = record.to_content_block()
    encoded = block.source["data"] if block.type == "image" else None
    body_block = (
        {"type": "image", "source": {"type": "base64", "data": encoded}}
        if encoded is not None
        else {"type": "text", "text": block.text}
    )
    return _attempt_record(
        messages=[UserMessage(content=[block])],
        request_body={"messages": [{"content": [body_block]}]},
        model_attempt_id="attempt-1",
        request_sha256=request_sha256,
        stripped_images=False,
        response_completed=True,
        error=None,
    )


@pytest.mark.asyncio
async def test_raster_capture_serialization_and_binding_are_frozen() -> None:
    backend = Backend()
    service, context, ledger = _service_and_context(backend)

    record = await service.capture_for_model(context)
    assert record.observation_id == "obs-1"
    assert record.content_sha256 == hashlib.sha256(b"encoded-image").hexdigest()
    assert record.pixel_sha256 == hashlib.sha256(b"decoded-pixels").hexdigest()
    assert record.to_content_block().type == "image"
    assert backend.captures == 1

    request_hash = hashlib.sha256(b"frozen-request").hexdigest()
    binding = service.bind_provider_request(ledger, record, _attempt_for(record, request_hash))
    assert binding.content_bytes == b"encoded-image"
    assert binding.to_metadata()["observation_id"] == "obs-1"
    assert ledger.state is ObservationState.BOUND_READY
    assert backend.captures == 1


@pytest.mark.asyncio
async def test_structured_capture_has_content_hash_but_no_pixel_hash() -> None:
    backend = Backend()
    backend.next_capture = ObservationCapture(
        backend_id="backend-a",
        run_id="run-1",
        generation=3,
        state_sequence=7,
        capture_event_sequence=12,
        media_type="application/json",
        content={"z": 2, "a": [1, True]},
        evidence_ref="dom/1",
    )
    service, context, _ledger = _service_and_context(backend)

    record = await service.capture_for_model(context)
    assert record.content_bytes == b'{"a":[1,true],"z":2}'
    assert record.pixel_sha256 is None
    assert record.to_content_block().type == "text"


@pytest.mark.asyncio
async def test_audit_capture_never_enters_model_ledger() -> None:
    backend = Backend()
    service, context, ledger = _service_and_context(backend)

    audit = await service.capture_for_audit(context)
    assert isinstance(audit, AuditCaptureRecord)
    assert not hasattr(audit, "to_content_block")
    assert ledger.state is ObservationState.NEEDS_OBSERVE
    assert ledger.current_record is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("backend_id", "generation", "message"),
    [
        ("foreign", 3, "foreign observation backend"),
        ("backend-a", 4, "wrong observation generation"),
    ],
)
async def test_foreign_or_wrong_generation_fails_before_backend_capture(
    backend_id: str, generation: int, message: str
) -> None:
    backend = Backend(backend_id=backend_id, generation=generation)
    service, context, _ledger = _service_and_context(backend)

    with pytest.raises(ObservationFreshnessError, match=message):
        await service.capture_for_model(context)
    assert backend.captures == 0


@pytest.mark.asyncio
async def test_stale_capture_order_and_post_action_freshness_are_rejected() -> None:
    backend = Backend()
    service, context, ledger = _service_and_context(backend)
    first = await service.capture_for_model(context)
    request_hash = hashlib.sha256(b"req").hexdigest()
    service.bind_provider_request(ledger, first, _attempt_for(first, request_hash))

    ledger.mark_observation_debt(action_completion_event_sequence=20, post_state_sequence=8)
    backend.next_capture = ObservationCapture(
        backend_id="backend-a",
        run_id="run-1",
        generation=3,
        state_sequence=7,
        capture_event_sequence=20,
        media_type="application/json",
        content={"stale": True},
        evidence_ref="stale/1",
    )
    with pytest.raises(ObservationFreshnessError, match="follow action completion"):
        await service.capture_for_model(context)

    backend.next_capture = ObservationCapture(
        backend_id="backend-a",
        run_id="run-1",
        generation=3,
        state_sequence=8,
        capture_event_sequence=21,
        media_type="application/json",
        content={"fresh": True},
        evidence_ref="fresh/1",
    )
    record = await service.capture_for_model(context)
    assert record.state_sequence == 8
    assert ledger.observation_debt is False


@pytest.mark.asyncio
async def test_lower_backend_sequence_invalidates_previous_binding() -> None:
    backend = Backend()
    service, context, ledger = _service_and_context(backend)
    record = await service.capture_for_model(context)
    request_hash = hashlib.sha256(b"req-lower").hexdigest()
    service.bind_provider_request(ledger, record, _attempt_for(record, request_hash))
    backend.state_sequence = record.state_sequence - 1
    assert await service.before_action(
        SimpleNamespace(
            verification_policy=SimpleNamespace(requires_pre_observation="current_bound")
        ),
        SimpleNamespace(observation=ledger, run_id="run-1", backend=backend),
    ) is False
