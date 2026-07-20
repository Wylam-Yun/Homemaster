from __future__ import annotations

import base64
import hashlib

import pytest

from homemaster.agent.messages import UserMessage
from homemaster.observations import (
    ObservationCapture,
    ObservationCaptureContext,
    ObservationLedger,
    ObservationService,
)
from homemaster.providers.llm_client import _attempt_record


class CountingBackend:
    backend_id = "backend-a"
    generation = 1
    state_sequence = 2
    event_sequence = 3

    def __init__(self) -> None:
        self.capture_count = 0

    def capture(self) -> ObservationCapture:
        self.capture_count += 1
        return ObservationCapture(
            backend_id=self.backend_id,
            run_id="run-1",
            generation=self.generation,
            state_sequence=self.state_sequence,
            capture_event_sequence=self.event_sequence,
            media_type="image/png",
            content=b"same-frozen-frame",
            pixel_bytes=b"same-frozen-pixels",
            evidence_ref="frame/1",
        )


@pytest.mark.asyncio
async def test_provider_retry_reuses_frozen_observation_bytes_and_hashes() -> None:
    backend = CountingBackend()
    ledger = ObservationLedger("run-1", backend.backend_id, backend.generation)
    service = ObservationService(id_factory=lambda: "obs-1")
    context = ObservationCaptureContext("run-1", "alfworld.observe.v1", backend, ledger)

    record = await service.capture_for_model(context)
    request_sha256 = hashlib.sha256(b"canonical-request").hexdigest()
    binding = service.bind_provider_request(ledger, record, request_sha256)
    first_payload = (binding.content_bytes, binding.content_sha256, binding.pixel_sha256)

    retry_payload = (binding.content_bytes, binding.content_sha256, binding.pixel_sha256)
    assert retry_payload == first_payload
    assert backend.capture_count == 1
    assert request_sha256 == binding.request_sha256


@pytest.mark.asyncio
async def test_attempt_record_preserves_optional_observation_metadata() -> None:
    backend = CountingBackend()
    ledger = ObservationLedger("run-1", backend.backend_id, backend.generation)
    service = ObservationService(id_factory=lambda: "obs-1")
    record = await service.capture_for_model(
        ObservationCaptureContext("run-1", "alfworld.observe.v1", backend, ledger)
    )
    block = record.to_content_block()
    encoded = base64.b64encode(record.content_bytes).decode("ascii")
    attempt = _attempt_record(
        messages=[UserMessage(content=[block])],
        request_body={
            "messages": [
                {
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "data": encoded},
                        }
                    ]
                }
            ]
        },
        model_attempt_id="attempt-1",
        request_sha256=hashlib.sha256(b"request").hexdigest(),
        stripped_images=False,
        response_completed=True,
        error=None,
    )

    image = attempt.outbound_images[0]
    assert image.content_sha256 == record.content_sha256
    assert image.observation_id == record.observation_id
    assert image.observation_content_sha256 == record.content_sha256
    assert image.observation_pixel_sha256 == record.pixel_sha256
    assert image.observation_backend_id == record.backend_id
    assert image.observation_generation == record.generation
    assert image.observation_capture_event_sequence == record.capture_event_sequence
