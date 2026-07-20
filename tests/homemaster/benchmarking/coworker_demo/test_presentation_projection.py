from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from homemaster.benchmarking.coworker_demo import presentation as presentation_projection
from homemaster.benchmarking.coworker_demo.correlation import action_id_for
from homemaster.benchmarking.coworker_demo.presentation import (
    ProjectionError,
    project_runtime_event,
    reject_secret_text,
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
        "schema_version": 2,
        "runtime_event_type": "tool.call_started",
        "tool_call_id": "call-1",
        "action_id": action_id_for("run-a", "call-1"),
        "tool_name": "browser_click",
        "tool_label_zh": "执行页面操作",
        "tool_kind": "mutation",
        "status": "running",
        "arguments": {"bid": "monitor-query-alarm"},
        "timestamp": "2026-07-17T08:00:00Z",
    }
    assert "secret" not in json.dumps(projected)


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    [
        (
            "task_planner",
            {"goal": "g", "current_subtask": "c", "next_focus": "n", "subtasks": [{}]},
            {"subtask_count": 1, "has_current_subtask": True, "has_next_focus": True},
        ),
        (
            "task_progress_check",
            {"current_subtask": "c", "next_focus": "n", "updates": [{}]},
            {"update_count": 1, "has_current_subtask": True, "has_next_focus": True},
        ),
        (
            "skill_view",
            {"skill_name": "change_execution", "token": "no"},
            {"skill_name": "change_execution"},
        ),
        ("browser_navigate", {"route": "ticket", "url": "no"}, {"route": "ticket"}),
        ("browser_observe", {"raw": "no"}, {}),
        (
            "browser_click",
            {"bid": "monitor-query-alarm", "api_key": "no"},
            {"bid": "monitor-query-alarm"},
        ),
        (
            "browser_fill",
            {"bid": "automation-tenant-id", "value": "v", "token": "no"},
            {"bid": "automation-tenant-id", "value_class": "free_text", "value_present": True},
        ),
        (
            "browser_select",
            {"bid": "automation-script", "value": "svc_cfg_cli_runner", "token": "no"},
            {"bid": "automation-script", "value": "svc_cfg_cli_runner"},
        ),
        (
            "browser_wait",
            {"job_id": "job-add-abcdef1234", "target_status": "terminal", "operation": "no"},
            {"job_id": "job-add-abcdef1234", "target_status": "terminal"},
        ),
        (
            "sop_decide",
            {"stage": "check_before_change", "decision": "proceed", "reason": "no"},
            {"stage": "check_before_change", "decision": "proceed"},
        ),
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
                    "action_id": action_id_for("run-a", "call-1"),
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
                            "evidence_refs": ["ev-00002-abcdef12"],
                        }
                    },
                    "evidence_refs": ["ev-00001-abcdef12", "ev-00002-abcdef12"],
                }
            },
        )
    )

    assert projected == {
        "schema_version": 2,
        "runtime_event_type": "tool.call_completed",
        "tool_call_id": "call-1",
        "action_id": action_id_for("run-a", "call-1"),
        "tool_name": "browser_click",
        "tool_label_zh": "执行页面操作",
        "tool_kind": "mutation",
        "status": "succeeded",
        "result": {"query": "alarm", "status": "ready"},
        "evidence_refs": ["ev-00001-abcdef12", "ev-00002-abcdef12"],
        "timestamp": "2026-07-17T08:00:00Z",
    }
    serialized = json.dumps(projected)
    assert "forbidden" not in serialized
    assert "active_alarms" not in serialized


def test_private_and_assistant_events_are_not_projected() -> None:
    thinking = event("assistant.thinking", payload={"thinking": "private"})
    assert project_runtime_event(thinking) is None


def test_successful_task_snapshot_projects_bounded_plan() -> None:
    projected = project_runtime_event(
        event(
            "tool.call_completed",
            name="task_planner",
            tool_call_id="call-plan",
            payload={
                "data": {
                    "subtasks": [
                        {
                            "id": "precheck",
                            "description": "Complete checks",
                            "status": "blocked",
                            "evidence": ["must-not-project"],
                        },
                        {
                            "id": "verify",
                            "description": "Verify uncertain result",
                            "status": "uncertain",
                        },
                    ],
                    "current_subtask": "precheck",
                    "next_focus": "Collect current evidence",
                    "constraints": ["must-not-project"],
                    "open_questions": ["must-not-project"],
                    "completion_summary": "must-not-project",
                }
            },
        )
    )

    assert projected["schema_version"] == 2
    assert projected["plan"] == {
        "items": [
            {"id": "precheck", "title": "Complete checks", "status": "blocked"},
            {"id": "verify", "title": "Verify uncertain result", "status": "uncertain"},
        ],
        "current_id": "precheck",
        "next_focus": "Collect current evidence",
    }
    serialized_plan = json.dumps(projected["plan"])
    assert "must-not-project" not in serialized_plan
    assert all("evidence" not in item for item in projected["plan"]["items"])
    assert "constraints" not in serialized_plan
    assert "open_questions" not in serialized_plan
    assert "completion_summary" not in serialized_plan


@pytest.mark.parametrize(
    "mutation",
    [
        {"subtasks": [{"id": f"step-{index}", "description": "Step"} for index in range(25)]},
        {"subtasks": [{"id": "x" * 65, "description": "Step"}]},
        {"subtasks": [{"id": "step", "description": "x" * 161}]},
        {"subtasks": [{"id": "step", "description": "Step", "status": "failed"}]},
        {
            "subtasks": [{"id": "step", "description": "Step"}],
            "next_focus": "x" * 241,
        },
        {"subtasks": [{"id": "step", "description": "unsafe\x00title"}]},
        {"subtasks": [{"id": "step", "description": "sk-abcdefghijklmnopqrstuvwxyz"}]},
    ],
)
def test_unsafe_or_oversized_plan_field_rejects_plan_projection(mutation: dict) -> None:
    with pytest.raises(ProjectionError, match="unsafe planner snapshot"):
        project_runtime_event(
            event(
                "tool.call_completed",
                name="task_progress_check",
                tool_call_id="call-plan-limits",
                payload={"data": mutation},
            )
        )


def test_exact_plan_and_reply_limits_are_accepted() -> None:
    plan = project_runtime_event(
        event(
            "tool.call_completed",
            name="task_planner",
            tool_call_id="call-plan-exact-limits",
            payload={
                "data": {
                    "subtasks": [
                        {"id": f"step-{index}", "description": "x" * 160} for index in range(24)
                    ],
                    "current_subtask": "step-0",
                    "next_focus": "x" * 240,
                }
            },
        )
    )
    reply = project_runtime_event(event("assistant.reply", payload={"reply": "x" * 1_200}))
    assert len(plan["plan"]["items"]) == 24
    assert len(reply["public_model_output"]["text"]) == 1_200


@pytest.mark.parametrize("reply", ["x" * 1_201, "unsafe\x00reply"])
def test_unsafe_or_oversized_reply_is_rejected(reply: str) -> None:
    assert project_runtime_event(event("assistant.reply", payload={"reply": reply})) is None


@pytest.mark.parametrize(
    "failure_code",
    [
        "plan_required",
        "missing_precheck_evidence",
        "progress_required",
        "wait_required",
        "postchecks_required",
        "rollback_verification_required",
        "rollback_decision_required",
        "missing_anomaly_evidence",
        "missing_implementation_evidence",
        "missing_postcheck_evidence",
        "missing_rollback_evidence",
        "external_state_mismatch",
        "parameter_mismatch",
        "command_not_allowed",
        "invalid_decision_for_stage",
        "stale_state_version",
        "action_replay",
        "terminal_outcome",
    ],
)
def test_every_safety_failure_code_is_normalized_without_raw_exception(
    failure_code: str,
) -> None:
    raw = f"EnvironmentClientError: {failure_code}: raw internal detail"
    projected = project_runtime_event(
        event(
            "tool.call_failed",
            name="browser_click",
            tool_call_id=f"call-{failure_code}",
            payload={"data": {"failure_reason": raw, "success": False}},
        )
    )
    assert projected["failure_code"] == failure_code
    assert "raw internal detail" not in json.dumps(projected)


def test_every_episode_error_code_has_an_explicit_safe_mapping() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (repo_root / "apps/case02_openenv/src/case02_openenv").rglob("*.py")
    )
    episode_codes = set(re.findall(r'EpisodeError\(\s*"([a-z0-9_]+)"', source))

    assert episode_codes
    assert episode_codes <= set(presentation_projection._FAILURE_CODE_ALIASES)
    assert set(presentation_projection._FAILURE_CODE_ALIASES.values()) <= (
        presentation_projection._SAFE_FAILURE_CODES
    )


def test_message_only_rollback_failure_maps_to_safe_code() -> None:
    projected = project_runtime_event(
        event(
            "tool.call_failed",
            name="browser_click",
            tool_call_id="call-remove",
            payload={
                "data": {
                    "failure_reason": "RuntimeError: remove requires a rollback decision",
                    "success": False,
                }
            },
        )
    )
    assert projected["failure_code"] == "rollback_decision_required"
    assert "RuntimeError" not in json.dumps(projected)


def test_assistant_reply_projects_bounded_public_text_but_thinking_does_not() -> None:
    reply = project_runtime_event(event("assistant.reply", payload={"reply": "Run blocked."}))
    assert reply == {
        "schema_version": 2,
        "runtime_event_type": "model.public_reply",
        "status": "succeeded",
        "public_model_output": {
            "kind": "assistant_reply",
            "text": "Run blocked.",
            "outcome": "intermediate",
        },
        "timestamp": "2026-07-17T08:00:00Z",
    }
    assert (
        project_runtime_event(
            event("assistant.thinking", payload={"thinking": "private reasoning"})
        )
        is None
    )


@pytest.mark.parametrize(
    "secret",
    [
        "actual-configured-provider-secret",
        "Bearer abcdefghijklmnopqrstuvwxyz123456",
        "api_key=abcdefghijklmnopqrstuvwxyz123456",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue",
        "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
        "https://example.test/file?X-Amz-Signature=abcdef1234567890",
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "aZ8Pq3Lw9Xk2Vm7Nc4Rt6Ys1Hd5Jf0Bu",
    ],
    ids=[
        "configured-value",
        "bearer-header",
        "api-key-assignment",
        "jwt",
        "private-key",
        "signed-url",
        "openai-key",
        "high-entropy-value",
    ],
)
def test_free_text_secret_patterns_are_rejected_without_logging_source(secret: str) -> None:
    configured = ("actual-configured-provider-secret",)
    assert reject_secret_text(secret, sensitive_values=configured) is True
    assert (
        project_runtime_event(
            event("assistant.reply", payload={"reply": f"Result {secret}"}),
            sensitive_values=configured,
        )
        is None
    )


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
                "action_id": action_id_for("run-a", "call-secret-test"),
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
                payload={
                    "arguments": {
                        "job_id": "job-add-abcdef1234",
                        "target_status": "terminal",
                    }
                },
            )
        )
        for boundary in ("tool.call_started", "tool.call_completed", "tool.call_failed")
    ]
    assert {item["action_id"] for item in projections if item is not None} == {
        action_id_for("run-a", "call-1")
    }
    assert action_id_for("a:b", "c") != action_id_for("a", "b:c")


def test_status_mapping_accepts_only_trusted_backend_lifecycle_status() -> None:
    cases: list[tuple[str, str | None, str]] = [
        ("tool.call_completed", "accepted", "accepted"),
        ("tool.call_completed", "succeeded", "succeeded"),
        ("tool.call_completed", None, "succeeded"),
        ("tool.call_failed", "rejected", "rejected"),
        ("tool.call_failed", "failed", "failed"),
        ("tool.call_failed", None, "failed"),
    ]
    for event_type, backend_status, expected in cases:
        projected = project_runtime_event(
            event(
                event_type,
                name="browser_observe",
                tool_call_id=f"call-{backend_status}-{event_type}",
                payload={
                    "data": (
                        {"backend_status": backend_status} if backend_status is not None else {}
                    )
                },
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
            "schema_version": 2,
            "runtime_event_type": event_type,
            "status": expected_status,
            "timestamp": "2026-07-17T08:00:00Z",
        }


def test_invalid_tool_identity_fails_closed() -> None:
    for invalid in (
        event("tool.call_started", name="browser_click", tool_call_id=None),
        event("tool.call_started", name="", tool_call_id="call-1"),
        event("tool.call_started", name="browser_click", tool_call_id="call-1", run_id=""),
    ):
        with pytest.raises(ProjectionError):
            project_runtime_event(invalid)


def test_tool_result_summaries_are_tool_specific_and_clipped() -> None:
    assert summarize_tool_result(
        "browser_wait",
        {
            "visible_observation": {
                "job_id": "job-add-abcdef1234",
                "operation": "add",
                "status": "succeeded",
                "x": "no",
            }
        },
    ) == {
        "job_id": "job-add-abcdef1234",
        "operation": "add",
        "status": "succeeded",
    }
    terminal = summarize_tool_result(
        "terminal_execute", {"exit_code": 0, "stdout": "x" * 400, "stderr": "", "token": "no"}
    )
    assert terminal["exit_code"] == 0
    assert terminal["stdout_present"] is True
    assert terminal["stderr_present"] is False
    assert "x" * 20 not in json.dumps(terminal)
    with pytest.raises(ProjectionError):
        summarize_tool_result("unknown_tool", {"success": True})


class RecordingClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.presented: list[tuple[str, dict]] = []

    def presentation_event(self, run_id: str, payload: dict) -> dict:
        if self.fail:
            raise RuntimeError("mirror unavailable")
        self.presented.append((run_id, payload))
        return {"success": True}


def test_trace_sink_redacts_secrets_locally_and_posts_only_safe_projection(
    tmp_path: Path,
) -> None:
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
    assert "full secret" not in local_trace
    assert "MODEL: working through the change procedure" in local_transcript
    assert "MODEL: full assistant reply" in local_transcript
    assert len(client.presented) == 2
    assert client.presented[0][1]["public_model_output"]["text"] == "full assistant reply"
    assert client.presented[1][1]["arguments"] == {"bid": "monitor-query-alarm"}
    assert "secret" not in json.dumps(client.presented)


def test_trace_sink_never_logs_or_mirrors_configured_secret_in_reply(tmp_path: Path) -> None:
    secret = "actual-configured-provider-secret"
    client = RecordingClient()
    trace = tmp_path / "trace.jsonl"
    transcript = tmp_path / "transcript.txt"
    sink = CoworkerTraceSink(
        trace,
        client,
        "run-a",
        transcript_path=transcript,
        sensitive_values=(secret,),
    )

    sink.emit(event("assistant.reply", payload={"reply": f"Result: {secret}"}))

    assert secret not in trace.read_text(encoding="utf-8")
    assert secret not in transcript.read_text(encoding="utf-8")
    assert client.presented == []
    assert sink.mirror_failure_total == 1


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

    assert list(sink.mirror_failures) == ["RuntimeError: presentation mirror failed"]
    assert sink.mirror_failure_total == 1
    assert (tmp_path / "trace.jsonl").exists()


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "task_planner",
            {
                "goal": "FORBIDDEN-CREDENTIAL https://signed.invalid/x",
                "current_subtask": "FORBIDDEN-CREDENTIAL",
                "next_focus": "FORBIDDEN-CREDENTIAL",
                "subtasks": [{"description": "FORBIDDEN-CREDENTIAL"}],
            },
        ),
        (
            "task_progress_check",
            {
                "current_subtask": "FORBIDDEN-CREDENTIAL",
                "next_focus": "FORBIDDEN-CREDENTIAL",
                "updates": [{"evidence": "FORBIDDEN-CREDENTIAL"}],
            },
        ),
        ("browser_fill", {"bid": "automation-tenant-id", "value": "FORBIDDEN-CREDENTIAL"}),
        ("browser_select", {"bid": "monitor-cluster", "value": "FORBIDDEN-CREDENTIAL"}),
        ("browser_select", {"bid": "monitor-region", "value": "FORBIDDEN-CREDENTIAL"}),
        ("browser_select", {"bid": "automation-script", "value": "FORBIDDEN-CREDENTIAL"}),
        ("terminal_execute", {"command": "grep FORBIDDEN-CREDENTIAL https://signed.invalid"}),
    ],
)
def test_free_text_allowlisted_arguments_are_summarized_without_values(
    tool_name: str, arguments: dict
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
    assert "FORBIDDEN-CREDENTIAL" not in json.dumps(projected)
    assert "signed.invalid" not in json.dumps(projected)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("skill_view", {"skill_name": "FORBIDDEN-CREDENTIAL"}),
        ("browser_navigate", {"route": "https://signed.invalid/FORBIDDEN-CREDENTIAL"}),
        ("browser_click", {"bid": "FORBIDDEN-CREDENTIAL"}),
        ("browser_wait", {"job_id": "FORBIDDEN-CREDENTIAL", "target_status": "terminal"}),
        ("sop_decide", {"stage": "FORBIDDEN-CREDENTIAL", "decision": "proceed"}),
    ],
)
def test_invalid_closed_argument_values_fail_projection(tool_name: str, arguments: dict) -> None:
    with pytest.raises(ProjectionError):
        project_runtime_event(
            event(
                "tool.call_started",
                name=tool_name,
                tool_call_id=f"call-{tool_name}",
                payload={"arguments": arguments},
            )
        )


@pytest.mark.parametrize(
    ("tool_name", "data"),
    [
        (
            "browser_click",
            {
                "visible_observation": {
                    "receipt": {
                        "payload": {
                            key: "FORBIDDEN-CREDENTIAL"
                            for key in (
                                "check",
                                "ready",
                                "query",
                                "stage",
                                "status",
                                "alarm_code",
                                "job_id",
                                "operation",
                            )
                        }
                    }
                }
            },
        ),
        (
            "browser_wait",
            {
                "visible_observation": {
                    "job_id": "FORBIDDEN-CREDENTIAL",
                    "operation": "FORBIDDEN-CREDENTIAL",
                    "status": "FORBIDDEN-CREDENTIAL",
                }
            },
        ),
        (
            "browser_fill",
            {
                "visible_observation": {
                    "bid": "FORBIDDEN-CREDENTIAL",
                    "value": "FORBIDDEN-CREDENTIAL",
                    "readback": "FORBIDDEN-CREDENTIAL",
                }
            },
        ),
        (
            "terminal_execute",
            {
                "stdout": "FORBIDDEN-CREDENTIAL",
                "stderr": "https://signed.invalid/FORBIDDEN-CREDENTIAL",
            },
        ),
        (
            "sop_decide",
            {
                "backend_status": "FORBIDDEN-CREDENTIAL",
                "terminal": "FORBIDDEN-CREDENTIAL",
                "classification": "FORBIDDEN-CREDENTIAL",
            },
        ),
        (
            "task_planner",
            {
                "goal": "FORBIDDEN-CREDENTIAL",
                "current_subtask": "FORBIDDEN-CREDENTIAL",
                "next_focus": "FORBIDDEN-CREDENTIAL",
                "completion_summary": "FORBIDDEN-CREDENTIAL",
            },
        ),
        (
            "skill_view",
            {"name": "FORBIDDEN-CREDENTIAL", "description": "FORBIDDEN-CREDENTIAL"},
        ),
    ],
)
def test_all_result_string_values_are_omitted_or_safely_summarized(
    tool_name: str, data: dict
) -> None:
    summary = summarize_tool_result(tool_name, data)
    serialized = json.dumps(summary)
    assert "FORBIDDEN-CREDENTIAL" not in serialized
    assert "signed.invalid" not in serialized


def test_terminal_projection_emits_only_closed_classification_and_presence() -> None:
    command = (
        'grep -A 3 "tenant-a:item-1" '
        "/opt/app/service_layer/component/config/extension_item_mapping.json"
    )
    projected = project_runtime_event(
        event(
            "tool.call_started",
            name="terminal_execute",
            tool_call_id="call-terminal",
            payload={"arguments": {"command": command}},
        )
    )
    assert projected is not None
    assert projected["arguments"]["command_kind"] == "sop_grep"
    assert set(projected["arguments"]) == {"command_kind"}
    assert command not in json.dumps(projected)
    result = summarize_tool_result(
        "terminal_execute",
        {"exit_code": 0, "stdout": "secret stdout", "stderr": "signed URL"},
    )
    assert result["stdout_present"] is True
    assert result["stderr_present"] is True
    assert set(result) == {"exit_code", "stdout_present", "stderr_present"}
    assert "secret stdout" not in json.dumps(result)


@pytest.mark.parametrize(
    "command",
    [
        (
            'grep -A 3 "tenant-a:item-1" '
            "/opt/app/service_layer/component/config/extension_item_mapping.json; exfil"
        ),
        (
            'grep -A 3 "tenant-a:item-1" '
            "/opt/app/service_layer/component/config/extension_item_mapping.json && exfil"
        ),
        (
            'grep -A 3 "tenant-a:item-1" '
            "/opt/app/service_layer/component/config/extension_item_mapping.json || exfil"
        ),
        (
            'grep -A 3 "tenant-a:item-1" '
            "/opt/app/service_layer/component/config/extension_item_mapping.json > /tmp/x"
        ),
        (
            'grep -A 3 "$(exfil):item-1" '
            "/opt/app/service_layer/component/config/extension_item_mapping.json"
        ),
        (
            'grep -A 3 "tenant-a:item-1"\n'
            "/opt/app/service_layer/component/config/extension_item_mapping.json"
        ),
        "x" * 10_000,
    ],
)
def test_shell_tainted_or_huge_commands_are_never_classified_safe(command: str) -> None:
    projected = project_runtime_event(
        event(
            "tool.call_started",
            name="terminal_execute",
            tool_call_id="call-terminal-tainted",
            payload={"arguments": {"command": command}},
        )
    )
    assert projected is not None
    assert projected["arguments"] == {"command_kind": "other"}
    assert command not in json.dumps(projected)


def test_low_entropy_terminal_output_has_no_digest_or_length_or_content() -> None:
    result = summarize_tool_result(
        "terminal_execute",
        {"exit_code": 1, "stdout": "password", "stderr": "token"},
    )
    assert result == {"exit_code": 1, "stdout_present": True, "stderr_present": True}
    serialized = json.dumps(result)
    assert "password" not in serialized
    assert "token" not in serialized
    assert "sha" not in serialized
    assert "length" not in serialized


def test_spoofed_result_action_id_raises_projection_error() -> None:
    with pytest.raises(ProjectionError, match="action identity mismatch"):
        project_runtime_event(
            event(
                "tool.call_completed",
                name="browser_observe",
                tool_call_id="call-spoof",
                payload={"data": {"action_id": "action-spoofed"}},
            )
        )


def test_sink_rejects_cross_run_projection_without_posting(tmp_path: Path) -> None:
    client = RecordingClient()
    sink = CoworkerTraceSink(tmp_path / "trace.jsonl", client, "run-a")
    sink.emit(
        event(
            "runtime.turn_completed",
            run_id="run-b",
            payload={"final_reply": "must remain local"},
        )
    )
    assert client.presented == []
    assert sink.mirror_failure_total == 1
    assert "must remain local" in (tmp_path / "trace.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("run_id", "call_id", "tool_name"),
    [
        ("bad/run", "call-1", "browser_click"),
        ("run-a", "call\nsecret", "browser_click"),
        ("run-a", "call-1", "unknown_tool"),
        ("r" * 129, "call-1", "browser_click"),
    ],
)
def test_unsafe_or_unknown_tool_identity_fails_closed(
    run_id: str, call_id: str, tool_name: str
) -> None:
    with pytest.raises(ProjectionError):
        project_runtime_event(
            event(
                "tool.call_started",
                run_id=run_id,
                tool_call_id=call_id,
                name=tool_name,
            )
        )


def test_untrusted_timestamp_fails_closed() -> None:
    source = event(
        "tool.call_started",
        name="browser_click",
        tool_call_id="call-timestamp",
        payload={"arguments": {"bid": "monitor-query-alarm"}},
    )
    source.timestamp = "https://signed.invalid/FORBIDDEN-CREDENTIAL"
    with pytest.raises(ProjectionError, match="invalid presentation timestamp"):
        project_runtime_event(source)


def test_timestamp_is_canonicalized_to_utc_whole_seconds() -> None:
    source = event(
        "runtime.turn_completed",
        payload={"final_reply": "local only"},
    )
    source.timestamp = "2026-07-17T16:00:00.987654+08:00"
    assert project_runtime_event(source) == {
        "schema_version": 2,
        "runtime_event_type": "runtime.turn_completed",
        "status": "succeeded",
        "timestamp": "2026-07-17T08:00:00Z",
    }


def test_evidence_refs_accept_only_bounded_repository_ids() -> None:
    valid = [
        "ev-00001-abcdef12",
        "terminal-cmd-abcdef123456",
        "job-job-add-abcdef1234-accepted",
    ]
    hostile = [
        "https://signed.invalid/token",
        "secret\nheader",
        "x" * 10_000,
        {"token": "FORBIDDEN-CREDENTIAL"},
    ]
    projected = project_runtime_event(
        event(
            "tool.call_completed",
            name="browser_observe",
            tool_call_id="call-evidence",
            payload={"data": {"evidence_refs": [*valid, *hostile, *valid] * 20}},
        )
    )
    assert projected is not None
    assert projected["evidence_refs"] == valid
    assert "FORBIDDEN" not in json.dumps(projected)


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        ("tool.call_completed", {"backend_status": "failed"}),
        ("tool.call_completed", {"backend_status": "succeeded", "success": False}),
        (
            "tool.call_completed",
            {"visible_observation": {"status": "rejected"}},
        ),
        ("tool.call_failed", {"backend_status": "succeeded"}),
        ("tool.call_failed", {"backend_status": "failed", "success": True}),
        ("tool.call_failed", {"visible_observation": {"status": "succeeded"}}),
        ("tool.call_completed", {"success": "true"}),
        ("tool.call_failed", {"success": 0}),
        (
            "tool.call_completed",
            {
                "visible_observation": {
                    "status": "failed",
                    "receipt": {"payload": {"status": "unknown"}},
                }
            },
        ),
        (
            "tool.call_failed",
            {
                "visible_observation": {
                    "status": "succeeded",
                    "receipt": {"payload": {"status": "normal"}},
                }
            },
        ),
    ],
)
def test_contradictory_result_status_fails_closed(event_type: str, data: dict) -> None:
    with pytest.raises(ProjectionError, match="inconsistent tool lifecycle"):
        project_runtime_event(
            event(
                event_type,
                name="browser_observe",
                tool_call_id="call-status",
                payload={"data": data},
            )
        )


def test_mirror_failure_history_is_bounded_and_sanitized(tmp_path: Path) -> None:
    sink = CoworkerTraceSink(tmp_path / "trace.jsonl", RecordingClient(fail=True), "run-a")
    for index in range(100):
        sink.emit(
            event(
                "tool.call_started",
                name="browser_click",
                tool_call_id=f"call-{index}",
                payload={"arguments": {"bid": "monitor-query-alarm"}},
            )
        )
    assert sink.mirror_failure_total == 100
    assert len(sink.mirror_failures) <= 32
    assert "mirror unavailable" not in json.dumps(list(sink.mirror_failures))


def test_trace_sink_serializes_concurrent_emits(tmp_path: Path) -> None:
    client = RecordingClient()
    trace = tmp_path / "trace.jsonl"
    sink = CoworkerTraceSink(trace, client, "run-a")

    threads = [
        threading.Thread(
            target=sink.emit,
            args=(
                event(
                    "tool.call_started",
                    name="browser_click",
                    tool_call_id=f"call-{index}",
                    payload={"arguments": {"bid": "monitor-query-alarm"}},
                ),
            ),
        )
        for index in range(40)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = trace.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(client.presented) == 40
    assert all(json.loads(line)["type"] == "tool.call_started" for line in lines)


class BlockingFirstClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self._calls = 0
        self._calls_lock = threading.Lock()

    def presentation_event(self, run_id: str, payload: dict) -> dict:
        with self._calls_lock:
            self._calls += 1
            call_number = self._calls
        if call_number == 1:
            self.first_entered.set()
            assert self.release_first.wait(timeout=5)
        return super().presentation_event(run_id, payload)


def test_slow_mirror_does_not_block_another_local_trace_append(tmp_path: Path) -> None:
    client = BlockingFirstClient()
    trace = tmp_path / "trace.jsonl"
    sink = CoworkerTraceSink(trace, client, "run-a")
    first = threading.Thread(
        target=sink.emit,
        args=(
            event(
                "tool.call_started",
                name="browser_click",
                tool_call_id="call-first",
                payload={"arguments": {"bid": "monitor-query-alarm"}},
            ),
        ),
    )
    second = threading.Thread(
        target=sink.emit,
        args=(
            event(
                "tool.call_started",
                name="browser_click",
                tool_call_id="call-second",
                payload={"arguments": {"bid": "monitor-query-alarm"}},
            ),
        ),
    )
    first.start()
    assert client.first_entered.wait(timeout=2)
    second.start()
    second.join(timeout=1)
    try:
        assert len(trace.read_text(encoding="utf-8").splitlines()) == 2
    finally:
        client.release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()


class OrderedBlockingClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.first_entered = threading.Event()
        self.release_first = threading.Event()
        self.entered_types: list[str] = []
        self._entered_lock = threading.Lock()

    def presentation_event(self, run_id: str, payload: dict) -> dict:
        with self._entered_lock:
            self.entered_types.append(payload["runtime_event_type"])
            first = len(self.entered_types) == 1
        if first:
            self.first_entered.set()
            assert self.release_first.wait(timeout=5)
        return super().presentation_event(run_id, payload)


def test_concurrent_lifecycle_mirroring_preserves_local_ticket_order(tmp_path: Path) -> None:
    client = OrderedBlockingClient()
    trace = tmp_path / "trace.jsonl"
    sink = CoworkerTraceSink(trace, client, "run-a")
    call_id = "call-ordered"
    started = event(
        "tool.call_started",
        name="browser_observe",
        tool_call_id=call_id,
        payload={"arguments": {}},
    )
    completed = event(
        "tool.call_completed",
        name="browser_observe",
        tool_call_id=call_id,
        payload={"data": {"action_id": action_id_for("run-a", call_id)}},
    )
    first = threading.Thread(target=sink.emit, args=(started,))
    second = threading.Thread(target=sink.emit, args=(completed,))

    first.start()
    assert client.first_entered.wait(timeout=2)
    second.start()
    second.join(timeout=0.5)
    try:
        assert len(trace.read_text(encoding="utf-8").splitlines()) == 2
        assert client.entered_types == ["tool.call_started"]
        assert second.is_alive()
    finally:
        client.release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)

    assert client.entered_types == ["tool.call_started", "tool.call_completed"]
    assert [item[1]["runtime_event_type"] for item in client.presented] == [
        "tool.call_started",
        "tool.call_completed",
    ]
