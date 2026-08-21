from __future__ import annotations

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.schemas import WebEvent


def _event(event_type: str, *, payload: dict[str, object], **values: object) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        session_id="session-01",
        run_id="run-01",
        turn_index=0,
        payload=payload,
        **values,
    )


def test_transport_delta_projects_reasoning_and_answer_as_separate_web_events() -> None:
    projected = WebEventProjection().project(
        _event(
            "transport.delta",
            payload={
                "reasoning_delta": "think",
                "text_delta": "answer",
                "provider_metadata": {"secret": "must-not-cross"},
            },
        ),
        request_id="request-01",
    )

    assert projected == (
        WebEvent(
            type="thinking.delta",
            session_id="session-01",
            run_id="run-01",
            request_id="request-01",
            payload={"text": "think"},
        ),
        WebEvent(
            type="answer.delta",
            session_id="session-01",
            run_id="run-01",
            request_id="request-01",
            payload={"text": "answer"},
        ),
    )


def test_projection_maps_snapshots_and_lifecycle_and_rejects_unknown_events() -> None:
    projection = WebEventProjection()

    cases = (
        (
            _event("runtime.turn_started", payload={"user_text": "private prompt"}),
            "run.started",
            {},
        ),
        (
            _event("assistant.thinking", payload={"thinking": "full reasoning"}),
            "thinking.snapshot",
            {"text": "full reasoning"},
        ),
        (
            _event("assistant.reply", payload={"reply": "final answer"}),
            "answer.snapshot",
            {"text": "final answer"},
        ),
        (
            _event("runtime.turn_completed", payload={"final_reply": "do not duplicate"}),
            "run.completed",
            {},
        ),
        (
            _event(
                "runtime.turn_failed",
                payload={
                    "error_code": "provider_failed",
                    "error": "Provider failed safely",
                    "traceback": "must-not-cross",
                },
            ),
            "run.failed",
            {
                "code": "provider_failed",
                "message": "Provider failed safely",
                "retryable": False,
            },
        ),
        (
            _event("runtime.budget_exhausted", payload={"error_code": "budget_exhausted"}),
            "run.failed",
            {
                "code": "budget_exhausted",
                "message": "budget_exhausted",
                "retryable": False,
            },
        ),
        (
            _event("runtime.cancelled", payload={"phase": "provider_stream"}),
            "run.cancelled",
            {},
        ),
    )

    for event, event_type, payload in cases:
        assert projection.project(event, request_id="request-01") == (
            WebEvent(
                type=event_type,
                session_id="session-01",
                run_id="run-01",
                request_id="request-01",
                payload=payload,
            ),
        )

    assert projection.project(
        _event("provider.private_payload", payload={"secret": "private"}),
        request_id="request-01",
    ) == ()


def test_turn_completed_projects_question_before_completion() -> None:
    projected = WebEventProjection().project(
        _event(
            "runtime.turn_completed",
            payload={"question": "Please confirm the requested action."},
        ),
        request_id="request-01",
    )

    assert projected == (
        WebEvent(
            type="answer.snapshot",
            session_id="session-01",
            run_id="run-01",
            request_id="request-01",
            payload={"text": "Please confirm the requested action."},
        ),
        WebEvent(
            type="run.completed",
            session_id="session-01",
            run_id="run-01",
            request_id="request-01",
            payload={},
        ),
    )
    assert WebEventProjection(include_thinking=False).project(
        _event("assistant.thinking", payload={"thinking": "private"}),
        request_id="request-01",
    ) == ()


def test_projection_maps_structured_events_with_explicit_field_allowlists() -> None:
    projection = WebEventProjection()
    artifact = {
        "artifact_handle": f"hm-artifact:{'a' * 32}",
        "run_id": "run-01",
        "filename": "result.png",
        "media_type": "image/png",
        "content_sha256": "b" * 64,
    }

    cases = (
        (
            _event(
                "tool.call_started",
                payload={"arguments": {"query": "token=exact"}, "private": "drop"},
                tool_call_id="call-01",
                name="search_files",
            ),
            "tool.started",
            {
                "tool_call_id": "call-01",
                "name": "search_files",
                "arguments": {"query": "token=exact"},
            },
        ),
        (
            _event(
                "tool.call_completed",
                payload={
                    "result": "complete output",
                    "data": {
                        "artifacts": [
                            artifact,
                            {**artifact, "artifact_handle": "/tmp/private.png"},
                        ],
                        "host_path": "/tmp/private.png",
                    },
                },
                tool_call_id="call-01",
                name="search_files",
            ),
            "tool.completed",
            {
                "tool_call_id": "call-01",
                "name": "search_files",
                "status": "completed",
                "output": "complete output",
                "artifacts": [artifact],
            },
        ),
        (
            _event(
                "tool.call_failed",
                payload={"result": "safe failure", "traceback": "must-not-cross"},
                tool_call_id="call-02",
                name="terminal",
            ),
            "tool.failed",
            {
                "tool_call_id": "call-02",
                "name": "terminal",
                "status": "failed",
                "output": "safe failure",
                "artifacts": [],
            },
        ),
        (
            _event(
                "permission.confirmation_requested",
                payload={
                    "approval_id": "approval-01",
                    "arguments": {"path": "permission-test.txt"},
                    "cwd": "/workspace",
                    "reason": "confirmation_required",
                    "subject_id": "must-not-cross",
                },
                tool_call_id="call-03",
                name="write_file",
            ),
            "approval.requested",
            {
                "approval_id": "approval-01",
                "tool_call_id": "call-03",
                "name": "write_file",
                "arguments": {"path": "permission-test.txt"},
                "cwd": "/workspace",
                "reason": "confirmation_required",
            },
        ),
        (
            _event(
                "permission.confirmation_completed",
                payload={
                    "approval_id": "approval-01",
                    "approved": True,
                    "outcome": "approved",
                    "subject_id": "must-not-cross",
                },
                tool_call_id="call-03",
                name="write_file",
            ),
            "approval.resolved",
            {
                "approval_id": "approval-01",
                "tool_call_id": "call-03",
                "name": "write_file",
                "approved": True,
                "outcome": "approved",
            },
        ),
        (
            _event(
                "usage.update",
                payload={"input_tokens": 12, "output_tokens": 7, "private": "drop"},
            ),
            "usage.updated",
            {"input_tokens": 12, "output_tokens": 7},
        ),
        (
            _event(
                "context.compaction",
                payload={
                    "trigger": "auto",
                    "before_tokens": 100,
                    "after_tokens": 40,
                    "private": "drop",
                },
            ),
            "context.compacted",
            {"trigger": "auto", "before_tokens": 100, "after_tokens": 40},
        ),
    )

    for event, event_type, payload in cases:
        assert projection.project(event, request_id="request-01") == (
            WebEvent(
                type=event_type,
                session_id="session-01",
                run_id="run-01",
                request_id="request-01",
                payload=payload,
            ),
        )
