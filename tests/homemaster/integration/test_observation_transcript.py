from __future__ import annotations

import base64
import hashlib

import pytest

from homemaster.adapters import build_alfworld_profile
from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.observations import (
    ObservationCapture,
    ObservationLedger,
    ObservationService,
    ObservationState,
)
from homemaster.providers.llm_client import _attempt_record
from homemaster.tools.contracts import (
    PermissionSubject,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolExecutionStatus,
)


class Backend:
    backend_id = "alfworld-backend"
    generation = 1

    def __init__(self) -> None:
        self.state_sequence = 0
        self.event_sequence = 0
        self.capture_count = 0

    def capture(self) -> ObservationCapture:
        self.capture_count += 1
        self.event_sequence += 1
        return ObservationCapture(
            backend_id=self.backend_id,
            run_id="run-1",
            generation=self.generation,
            state_sequence=self.state_sequence,
            capture_event_sequence=self.event_sequence,
            media_type="image/png",
            content=f"frame-{self.state_sequence}".encode(),
            pixel_bytes=f"pixels-{self.state_sequence}".encode(),
            evidence_ref=f"frame/{self.capture_count}",
        )

    def advance(self) -> None:
        self.state_sequence += 1
        self.event_sequence += 1


def _context(profile, backend, ledger, internal_id, call_id):
    return ToolExecutionContext(
        session_id="session-1",
        run_id="run-1",
        turn_index=0,
        tool_call_id=call_id,
        internal_tool_id=internal_id,
        tool_view=profile.view,
        permission_subject=PermissionSubject(subject_id="user", channel="test"),
        backend=backend,
        deadline=None,
        cancellation=None,
        observation=ledger,
        domain_observer=None,
    )


@pytest.mark.asyncio
async def test_explicit_observation_binding_and_action_debt_transcript() -> None:
    service = ObservationService(id_factory=iter(("obs-1", "obs-2")).__next__)
    profile = build_alfworld_profile(observation_service=service)
    backend = Backend()
    ledger = ObservationLedger("run-1", backend.backend_id, backend.generation)
    observe = profile.view.lookup("observe").tool
    action = profile.view.lookup("robot_go_to").tool
    assert observe is not None and action is not None
    observe_context = _context(profile, backend, ledger, "alfworld.observe.v1", "observe-1")
    action_context = _context(profile, backend, ledger, "alfworld.robot_go_to.v1", "action-1")

    initial_request = [ContentBlock(text="task")]
    assert all(block.type != "image" for block in initial_request)
    first = await observe.executor.execute({}, observe_context)
    assert first.images[0].observation_id == "obs-1"
    assert ledger.state is ObservationState.OBSERVED_UNBOUND

    # observe+action in one assistant response is rejected until the next
    # successful frozen provider request binds the exact observation bytes.
    assert await service.before_action(action.definition, action_context) is False
    request_hash = hashlib.sha256(b"request-with-obs-1").hexdigest()
    record = ledger.current_record
    assert record is not None
    block = record.to_content_block()
    attempt = _attempt_record(
        messages=[UserMessage(content=[block])],
        request_body={
            "messages": [
                {
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "data": base64.b64encode(record.content_bytes).decode("ascii"),
                            },
                        }
                    ]
                }
            ]
        },
        model_attempt_id="attempt-bind",
        request_sha256=request_hash,
        stripped_images=False,
        response_completed=True,
        error=None,
    )
    binding = ledger.bind_provider_request(record, attempt)
    assert binding.request_sha256 == request_hash
    assert await service.before_action(action.definition, action_context) is True

    backend.advance()
    receipt = ToolExecutionResult(
        status=ToolExecutionStatus.SUCCESS,
        data={"ok": True},
        backend_attempted=True,
    )
    await service.after_action(action.definition, receipt, action_context)
    assert ledger.state is ObservationState.NEEDS_OBSERVE
    assert await service.before_action(action.definition, action_context) is False

    fresh = await observe.executor.execute({}, observe_context)
    assert fresh.images[0].observation_id == "obs-2"
    assert backend.capture_count == 2
    assert ledger.current_record is not None
    assert ledger.current_record.state_sequence == 1
