from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.benchmarking.coworker_demo.correlation import action_id_for
from homemaster.benchmarking.coworker_demo.presentation import (
    project_runtime_event,
    summarize_tool_result,
)
from homemaster.benchmarking.coworker_demo.tracing import CoworkerTraceSink
from homemaster.events.runtime_events import RuntimeEvent


def event(
    event_type: str,
    *,
    name: str | None = None,
    payload: dict | None = None,
    run_id: str = "run-a",
    tool_call_id: str | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        type=event_type,
        session_id="session-a",
        run_id=run_id,
        turn_index=0,
        payload=payload or {},
        tool_call_id=tool_call_id,
        name=name,
        timestamp="2026-07-17T08:00:00+00:00",
    )


def test_started_tool_projects_only_allowlisted_arguments() -> None:
    projected = project_runtime_event(
        event(
            "tool.call_started",
            name="browser_click",
            tool_call_id="call-1",
            payload={"arguments": {"bid": "monitor-query-alarm", "api_key": "secret"}},
        )
    )

    assert projected == {
        "runtime_event_type": "tool.call_started",
        "tool_call_id": "call-1",
        "action_id": action_id_for("run-a", "call-1"),
        "tool_name": "browser_click",
        "status": "running",
        "arguments": {"bid": "monitor-query-alarm"},
        "timestamp": "2026-07-17T08:00:00+00:00",
    }
    assert "secret" not in json.dumps(projected)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        (
            "task_planner",
            {"goal": "g", "current_subtask": "c", "next_focus": "n", "subtasks": "no"},
            {"goal": "g", "current_subtask": "c", "next_focus": "n"},
        ),
        (
            "task_progress_check",
            {"current_subtask": "c", "next_focus": "n", "updates": "no"},
            {"current_subtask": "c", "next_focus": "n"},
        ),
        (
            "skill_view",
            {"skill_name": "change_execution", "token": "no"},
            {"skill_name": "change_execution"},
        ),
        ("browser_navigate", {"route": "ticket", "url": "no"}, {"route": "ticket"}),
        ("browser_observe", {"raw": "no"}, {}),
        ("browser_click", {"bid": "b", "api_key": "no"}, {"bid": "b"}),
        ("browser_fill", {"bid": "b", "value": "v", "token": "no"}, {"bid": "b", "value": "v"}),
        ("browser_select", {"bid": "b", "value": "v", "token": "no"}, {"bid": "b", "value": "v"}),
        (
            "browser_wait",
            {"job_id": "j", "target_status": "terminal", "operation": "no"},
            {"job_id": "j", "target_status": "terminal"},
        ),
        ("terminal_execute", {"command": "grep x", "env": "no"}, {"command": "grep x"}),
        (
            "sop_decide",
            {"stage": "check", "decision": "proceed", "reason": "no"},
            {"stage": "check", "decision": "proceed"},
        ),
        ("unknown_tool", {"anything": "no"}, {}),
    ],
)
def test_started_argument_allowlist_is_explicit_for_every_tool(
    tool_name: str, arguments: dict, expected: dict
) -> None:
    projected = project_runtime_event(
        event(
            "tool.call_started",
            name=tool_name,
            tool_call_id=f"call-{tool_name}",
            payload={"arguments": arguments},
        )
    )

    assert projected is not None
    assert projected["arguments"] == expected


def test_completed_click_uses_trusted_identity_and_safe_receipt_fields() -> None:
    projected = project_runtime_event(
        event(
            "tool.call_completed",
            name="browser_click",
            tool_call_id="call-1",
            payload={
                "data": {
                    "action_id": "action-trusted",
                    "backend_status": "succeeded",
                    "raw_prompt": "forbidden",
                    "visible_observation": {
                        "receipt": {
                            "payload": {
                                "query": "alarm",
                                "status": "ready",
                                "active_alarms": ["must-not-leak"],
                                "raw_prompt": "forbidden",
                            },
                            "evidence_refs": ["receipt-1"],
                        }
                    },
                    "evidence_refs": ["data-1", "receipt-1"],
                }
            },
        )
    )

    assert projected == {
        "runtime_event_type": "tool.call_completed",
        "tool_call_id": "call-1",
        "action_id": "action-trusted",
        "tool_name": "browser_click",
        "status": "succeeded",
        "result": {"query": "alarm", "status": "ready"},
        "evidence_refs": ["data-1", "receipt-1"],
        "timestamp": "2026-07-17T08:00:00+00:00",
    }
    serialized = json.dumps(projected)
    assert "forbidden" not in serialized
    assert "active_alarms" not in serialized


def test_private_and_assistant_events_are_not_projected() -> None:
    thinking = event("assistant.thinking", payload={"thinking": "private"})
    assert project_runtime_event(thinking) is None
    assert project_runtime_event(event("assistant.reply", payload={"reply": "private"})) is None


def test_unallowlisted_payload_regions_never_reach_projection() -> None:
    forbidden = "FORBIDDEN-SENTINEL-9f2d"
    source = event(
        "tool.call_completed",
        name="browser_click",
        tool_call_id="call-secret-test",
        payload={
            "arguments": {"bid": "safe", "api_key": forbidden, "nested": {"x": forbidden}},
            "args": {"password": forbidden},
            "result": forbidden,
            "thinking": forbidden,
            "reply": forbidden,
            "provider_data": forbidden,
            "data": {
                "action_id": "action-safe",
                "backend_status": "succeeded",
                "raw_prompt": forbidden,
                "credentials": {"token": forbidden},
                "arbitrary": {"nested": forbidden},
                "evidence_refs": [{"nested": forbidden}, "safe-evidence"],
                "visible_observation": {
                    "url": forbidden,
                    "visible_text": forbidden,
                    "controls": [{"text": forbidden}],
                    "receipt": {
                        "private": forbidden,
                        "payload": {
                            "query": "safe-query",
                            "status": "ready",
                            "active_alarms": forbidden,
                            "nested": {"secret": forbidden},
                        },
                    },
                },
            },
        },
    )
    source.session_id = forbidden
    source.event_id = forbidden

    projected = project_runtime_event(source)

    assert projected is not None
    assert forbidden not in json.dumps(projected)


def test_lifecycle_reuses_fallback_action_id_and_preserves_boundaries() -> None:
    projections = [
        project_runtime_event(
            event(
                boundary,
                name="browser_wait",
                tool_call_id="call-1",
                payload={"arguments": {"job_id": "job-1", "target_status": "terminal"}},
            )
        )
        for boundary in ("tool.call_started", "tool.call_completed", "tool.call_failed")
    ]
    assert {item["action_id"] for item in projections if item is not None} == {
        action_id_for("run-a", "call-1")
    }
    assert action_id_for("a:b", "c") != action_id_for("a", "b:c")


def test_status_mapping_accepts_only_trusted_backend_lifecycle_status() -> None:
    cases = [
        ("tool.call_completed", "accepted", "accepted"),
        ("tool.call_completed", "ACCEPTED", "succeeded"),
        ("tool.call_completed", "rejected", "succeeded"),
        ("tool.call_failed", "rejected", "rejected"),
        ("tool.call_failed", "REJECTED", "failed"),
        ("tool.call_failed", "accepted", "failed"),
    ]
    for event_type, backend_status, expected in cases:
        projected = project_runtime_event(
            event(
                event_type,
                name="browser_observe",
                tool_call_id=f"call-{backend_status}-{event_type}",
                payload={"data": {"backend_status": backend_status}},
            )
        )
        assert projected is not None
        assert projected["status"] == expected


def test_runtime_turn_projection_has_no_prompt_reply_or_thinking() -> None:
    for event_type, expected_status in (
        ("runtime.turn_completed", "succeeded"),
        ("runtime.turn_failed", "failed"),
    ):
        projected = project_runtime_event(
            event(
                event_type,
                payload={"final_reply": "reply", "thinking": "thought", "prompt": "prompt"},
            )
        )
        assert projected == {
            "runtime_event_type": event_type,
            "status": expected_status,
            "timestamp": "2026-07-17T08:00:00+00:00",
        }


def test_invalid_tool_identity_fails_closed() -> None:
    for invalid in (
        event("tool.call_started", name="browser_click", tool_call_id=None),
        event("tool.call_started", name="", tool_call_id="call-1"),
        event("tool.call_started", name="browser_click", tool_call_id="call-1", run_id=""),
    ):
        assert project_runtime_event(invalid) is None


def test_tool_result_summaries_are_tool_specific_and_clipped() -> None:
    assert summarize_tool_result(
        "browser_wait",
        {
            "visible_observation": {
                "job_id": "j",
                "operation": "add",
                "status": "succeeded",
                "x": "no",
            }
        },
    ) == {"job_id": "j", "operation": "add", "status": "succeeded"}
    assert summarize_tool_result(
        "terminal_execute", {"exit_code": 0, "stdout": "x" * 400, "stderr": "", "token": "no"}
    ) == {"exit_code": 0, "stdout": "x" * 320, "stderr": ""}
    assert summarize_tool_result("unknown_tool", {"success": True, "secret": "no"}) == {
        "success": True
    }


class RecordingClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.presented: list[tuple[str, dict]] = []

    def presentation_event(self, run_id: str, payload: dict) -> dict:
        if self.fail:
            raise RuntimeError("mirror unavailable")
        self.presented.append((run_id, payload))
        return {"success": True}


def test_trace_sink_keeps_full_local_records_but_posts_only_projection(tmp_path: Path) -> None:
    client = RecordingClient()
    trace = tmp_path / "trace.jsonl"
    transcript = tmp_path / "transcript.txt"
    sink = CoworkerTraceSink(trace, client, "run-a", transcript_path=transcript)
    events = [
        event("assistant.thinking", payload={"thinking": "full private thought"}),
        event("assistant.reply", payload={"reply": "full assistant reply"}),
        event(
            "tool.call_started",
            name="browser_click",
            tool_call_id="call-1",
            payload={"arguments": {"bid": "monitor-query-alarm", "api_key": "full secret"}},
        ),
    ]

    for item in events:
        sink.emit(item)

    local_trace = trace.read_text(encoding="utf-8")
    local_transcript = transcript.read_text(encoding="utf-8")
    assert "full private thought" in local_trace
    assert "full assistant reply" in local_trace
    assert "full secret" in local_trace
    assert "MODEL: working through the change procedure" in local_transcript
    assert "MODEL: full assistant reply" in local_transcript
    assert len(client.presented) == 1
    assert client.presented[0][1]["arguments"] == {"bid": "monitor-query-alarm"}
    assert "secret" not in json.dumps(client.presented)


def test_trace_sink_records_projection_mirror_failures_without_raising(tmp_path: Path) -> None:
    sink = CoworkerTraceSink(tmp_path / "trace.jsonl", RecordingClient(fail=True), "run-a")

    sink.emit(
        event(
            "tool.call_started",
            name="browser_click",
            tool_call_id="call-1",
            payload={"arguments": {"bid": "monitor-query-alarm"}},
        )
    )

    assert sink.mirror_failures == ["RuntimeError: mirror unavailable"]
    assert (tmp_path / "trace.jsonl").exists()
