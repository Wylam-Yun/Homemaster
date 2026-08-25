from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from homemaster.adapters.profiles import build_tool_registry
from scripts.coworker_demo.scripted_shell_gate import ScriptedConversation
from scripts.coworker_demo.verify_run_bundle import (
    verify_presentation_bundle,
    verify_provider_identity,
)


@pytest.mark.parametrize("case_id", ["normal", "post_change_anomaly"])
@pytest.mark.parametrize("profile", ["clean", "observable_failures"])
def test_scripted_conversation_only_calls_registered_coworker_tools(
    case_id: str,
    profile: str,
) -> None:
    registry = build_tool_registry(environment="coworker")
    registered = set(registry.all_names())
    steps = ScriptedConversation(case_id, profile).steps

    assert {tool_name for tool_name, _arguments in steps} <= registered
    assert [arguments for tool_name, arguments in steps if tool_name == "load_skill"] == [
        {"name": "change_execution"},
        {"name": "evidence_discipline"},
    ]


def test_independent_verifier_rejects_missing_tool_completion(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    path = run_root / "presentation/events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "sequence": 1,
                "event_id": "presentation-1",
                "tool_call_id": "call-1",
                "action_id": "action-1",
                "status": "running",
                "task": {"source_text": "locked SOP", "source_sha256": "bad"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_root / "presentation/snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "last_sequence": 1,
                "plan": {},
                "current_action": None,
                "last_result": None,
                "public_model_output": None,
                "decision_summary": {},
                "incidents": [],
                "critical_history": [],
            }
        ),
        encoding="utf-8",
    )

    failures = verify_presentation_bundle(run_root)

    assert "missing_terminal_event:call-1" in failures
    assert "sop_source_hash_mismatch:presentation-1" in failures


def test_independent_verifier_accepts_correlated_locked_sop_events(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    path = run_root / "presentation/events.jsonl"
    path.parent.mkdir(parents=True)
    source_text = "locked SOP"
    source_sha256 = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    events = [
        {
            "schema_version": 2,
            "sequence": 1,
            "event_id": "presentation-1",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "event_type": "tool.call_started",
            "tool_name": "task_planner",
            "tool_label_zh": "任务规划",
            "tool_kind": "planner",
            "status": "running",
            "task": {"source_text": source_text, "source_sha256": source_sha256},
        },
        {
            "schema_version": 2,
            "sequence": 2,
            "event_id": "presentation-2",
            "tool_call_id": "call-1",
            "action_id": "action-1",
            "event_type": "tool.call_completed",
            "tool_name": "task_planner",
            "tool_label_zh": "任务规划",
            "tool_kind": "planner",
            "status": "succeeded",
            "task": {"source_text": source_text, "source_sha256": source_sha256},
            "plan": {
                "items": [{"id": "precheck", "title": "Run checks", "status": "pending"}],
                "current_id": "precheck",
                "next_focus": "Run checks",
            },
        },
    ]
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    (run_root / "presentation/snapshot.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "last_sequence": 2,
                "plan": {},
                "current_action": None,
                "last_result": None,
                "public_model_output": None,
                "decision_summary": {},
                "incidents": [],
                "critical_history": [],
            }
        ),
        encoding="utf-8",
    )

    assert verify_presentation_bundle(run_root) == []

    events[1].pop("plan")
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    assert "presentation_plan_missing:presentation-2" in verify_presentation_bundle(run_root)


def _write_provider_fixture(run_root: Path) -> None:
    identity = {
        "schema_version": 1,
        "created_at_utc": "2026-07-19T00:00:00+00:00",
        "provider": "Mimo",
        "model": "mimo-v2.5",
        "api_format": "anthropic",
        "transport": "raw_http",
        "scheme": "https",
        "host": "token-plan-cn.xiaomimimo.com",
        "api_key_count": 1,
        "provider_config_override": False,
    }
    source = json.dumps(
        {
            key: identity[key]
            for key in (
                "provider",
                "model",
                "api_format",
                "transport",
                "scheme",
                "host",
                "api_key_count",
                "provider_config_override",
            )
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    identity["config_fingerprint_sha256"] = hashlib.sha256(source).hexdigest()
    path = run_root / "agent/provider_identity.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(identity), encoding="utf-8")
    runtime = [
        {
            "type": "transport.request_started",
            "payload": {"model": "mimo-v2.5", "iteration": 0},
            "timestamp": "2026-07-19T00:00:01+00:00",
        },
        {
            "type": "transport.response_completed",
            "payload": {"model": "mimo-v2.5", "status": "ok", "iteration": 0},
            "timestamp": "2026-07-19T00:00:02+00:00",
        },
    ]
    (run_root / "agent/runtime_events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in runtime), encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("loopback", "provider_identity_endpoint"),
        ("override", "provider_identity_override"),
        ("wrong_model", "provider_identity_model"),
        ("no_response", "provider_success_response_missing"),
        ("missing_iteration", "provider_transport_iteration"),
        ("mismatched_iteration", "provider_transport_pairing"),
        ("duplicate_response", "provider_transport_duplicate_iteration"),
        ("response_before_request", "provider_transport_order"),
        ("tool_before_response", "provider_tool_without_response"),
        ("late_identity", "provider_identity_after_request"),
    ],
)
def test_provider_identity_rejects_spoofable_or_incomplete_artifact(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    _write_provider_fixture(tmp_path)
    identity_path = tmp_path / "agent/provider_identity.json"
    identity = json.loads(identity_path.read_text())
    if mutation == "loopback":
        identity["host"] = "127.0.0.1"
    elif mutation == "override":
        identity["provider_config_override"] = True
    elif mutation == "wrong_model":
        identity["model"] = "scripted-coworker"
    elif mutation == "no_response":
        runtime_path = tmp_path / "agent/runtime_events.jsonl"
        runtime_path.write_text(runtime_path.read_text().splitlines()[0] + "\n")
    elif mutation == "missing_iteration":
        runtime_path = tmp_path / "agent/runtime_events.jsonl"
        runtime = [json.loads(line) for line in runtime_path.read_text().splitlines()]
        runtime[1]["payload"].pop("iteration")
        runtime_path.write_text("".join(json.dumps(event) + "\n" for event in runtime))
    elif mutation == "mismatched_iteration":
        runtime_path = tmp_path / "agent/runtime_events.jsonl"
        runtime = [json.loads(line) for line in runtime_path.read_text().splitlines()]
        runtime[1]["payload"]["iteration"] = 1
        runtime_path.write_text("".join(json.dumps(event) + "\n" for event in runtime))
    elif mutation == "duplicate_response":
        runtime_path = tmp_path / "agent/runtime_events.jsonl"
        runtime = [json.loads(line) for line in runtime_path.read_text().splitlines()]
        runtime.append(runtime[1])
        runtime_path.write_text("".join(json.dumps(event) + "\n" for event in runtime))
    elif mutation == "response_before_request":
        runtime_path = tmp_path / "agent/runtime_events.jsonl"
        runtime = [json.loads(line) for line in runtime_path.read_text().splitlines()]
        runtime.reverse()
        runtime_path.write_text("".join(json.dumps(event) + "\n" for event in runtime))
    elif mutation == "tool_before_response":
        runtime_path = tmp_path / "agent/runtime_events.jsonl"
        runtime = [json.loads(line) for line in runtime_path.read_text().splitlines()]
        runtime.insert(1, {"type": "tool.call_started", "payload": {}})
        runtime_path.write_text("".join(json.dumps(event) + "\n" for event in runtime))
    elif mutation == "late_identity":
        identity["created_at_utc"] = "2026-07-19T00:00:03+00:00"
    if mutation != "no_response":
        fingerprint_source = {
            key: identity.get(key)
            for key in (
                "provider",
                "model",
                "api_format",
                "transport",
                "scheme",
                "host",
                "api_key_count",
                "provider_config_override",
            )
        }
        identity["config_fingerprint_sha256"] = hashlib.sha256(
            json.dumps(
                fingerprint_source,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        identity_path.write_text(json.dumps(identity))

    assert expected in verify_provider_identity(tmp_path, "mimo-v2.5")


def test_provider_identity_valid_fixture_requires_successful_response(tmp_path: Path) -> None:
    _write_provider_fixture(tmp_path)

    assert verify_provider_identity(tmp_path, "mimo-v2.5") == []


def _step_index(
    steps: list[tuple[str, dict]],
    name: str,
    *,
    start: int = 0,
    **arguments: str,
) -> int:
    return next(
        index
        for index, (tool_name, payload) in enumerate(steps[start:], start=start)
        if tool_name == name and all(payload.get(key) == value for key, value in arguments.items())
    )


def test_observable_failure_normal_profile_has_two_narrative_recovery_points() -> None:
    steps = ScriptedConversation("normal", "observable_failures").steps
    premature_progress = _step_index(steps, "task_progress_check")
    rejected_precheck = _step_index(
        steps,
        "sop_decide",
        reason="Attempted before all checks were observed",
    )
    monitor = _step_index(steps, "browser_navigate", route="monitor", start=rejected_precheck)
    success_precheck = _step_index(
        steps,
        "sop_decide",
        reason="All visible prechecks passed",
        start=monitor,
    )
    rejected_navigation = _step_index(
        steps,
        "browser_navigate",
        route="automation",
        start=success_precheck,
    )
    recovery_progress = _step_index(
        steps,
        "task_progress_check",
        start=rejected_navigation,
    )

    assert premature_progress < rejected_precheck < monitor < success_precheck
    assert success_precheck < rejected_navigation < recovery_progress


def test_observable_failure_anomaly_profile_has_four_narrative_recovery_points() -> None:
    steps = ScriptedConversation("post_change_anomaly", "observable_failures").steps
    rejected_precheck = _step_index(
        steps,
        "sop_decide",
        reason="Attempted before all checks were observed",
    )
    add_wait = _step_index(steps, "browser_wait", job_id="$job_add")
    rejected_grep = _step_index(steps, "terminal_execute")
    recovered_grep = _step_index(steps, "terminal_execute", start=add_wait)
    alarm_indices = [
        index
        for index, (name, payload) in enumerate(steps)
        if name == "browser_click" and payload.get("bid") == "monitor-query-alarm"
    ]
    causal_alarm = alarm_indices[-1]
    expected_reads = [
        "monitor-query-probe",
        "monitor-query-capacity",
        "monitor-query-runtime-metrics",
        "monitor-query-traffic",
    ]
    observed_reads = [steps[causal_alarm + offset][1].get("bid") for offset in range(1, 5)]
    rejected_remove = _step_index(
        steps,
        "browser_click",
        start=causal_alarm + 5,
        bid="automation-submit",
    )
    wrong_stage = _step_index(
        steps,
        "sop_decide",
        start=rejected_remove,
        reason="Attempted rollback before authorization",
    )
    valid_rollback = _step_index(
        steps,
        "sop_decide",
        start=wrong_stage,
        decision="rollback",
    )

    assert rejected_precheck < rejected_grep < add_wait < recovered_grep < causal_alarm
    assert observed_reads == expected_reads
    assert causal_alarm < rejected_remove < wrong_stage < valid_rollback
