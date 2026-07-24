"""Product-independent verification of one completed coworker run bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

REQUIRED_MANIFEST_ARTIFACTS = (
    "input/item_change_ticket.json",
    "input/scenario.json",
    "input/dataset_manifest.json",
    "input/ground_truth_hashes.json",
    "environment/audit_events.jsonl",
    "environment/state_snapshots.jsonl",
    "environment/evaluator_inputs.json",
    "trajectory/raw_actions.jsonl",
    "trajectory/effective_trajectory.jsonl",
    "trajectory/trajectory_match.json",
    "scores/trajectory_score.json",
    "scores/result_score.json",
    "scores/summary.json",
    "presentation/events.jsonl",
    "presentation/snapshot.json",
    "presentation/verification.json",
    "video/demo.mp4",
    "video/poster.png",
    "video/video_manifest.json",
)
PRESENTATION_FAILURE_CODES = {
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
    "unclassified_failure",
}
PRESENTATION_REQUIRED_SNAPSHOT_FIELDS = {
    "plan",
    "current_action",
    "last_result",
    "public_model_output",
    "decision_summary",
    "incidents",
    "critical_history",
}
EXPECTED_PROVIDER_HOST = "token-plan-cn.xiaomimimo.com"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _extract_frame(video: Path, timestamp: float) -> bytes:
    extraction = subprocess.run(
        [
            "/usr/bin/ffmpeg",
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if extraction.returncode != 0:
        raise RuntimeError(f"frame extraction failed: {extraction.stderr.decode().strip()}")
    return extraction.stdout


def _frame_stats(frame: bytes, width: int, height: int) -> tuple[dict[str, float], bytes]:
    expected = width * height * 3
    if len(frame) != expected:
        raise ValueError(f"raw frame size {len(frame)} != {expected}")
    grayscale = bytearray(width * height)
    nonblack = 0
    dark = 0
    total = 0
    total_squared = 0
    for pixel, offset in enumerate(range(0, len(frame), 3)):
        value = (frame[offset] + frame[offset + 1] + frame[offset + 2]) // 3
        grayscale[pixel] = value
        nonblack += value >= 17
        dark += value <= 63
        total += value
        total_squared += value * value
    count = width * height
    mean = total / count
    return (
        {
            "nonblack_ratio": nonblack / count,
            "dark_ratio": dark / count,
            "variance": total_squared / count - mean * mean,
        },
        bytes(grayscale),
    )


def _crop_rgb(
    frame: bytes,
    width: int,
    height: int,
    box: tuple[int, int, int, int],
) -> bytes:
    left, top, right, bottom = box
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError("invalid RGB crop")
    row_size = width * 3
    cropped = bytearray()
    for row in range(top, bottom):
        start = row * row_size + left * 3
        cropped.extend(frame[start : start + (right - left) * 3])
    return bytes(cropped)


def _changed_pixels(left: bytes, right: bytes) -> int:
    if len(left) != len(right):
        raise ValueError("frame sizes differ")
    return sum(
        left_pixel != right_pixel for left_pixel, right_pixel in zip(left, right, strict=True)
    )


def verify_presentation_bundle(run_root: Path) -> list[str]:
    events_path = run_root / "presentation/events.jsonl"
    if not events_path.is_file():
        return ["missing:presentation/events.jsonl"]
    try:
        events = _read_jsonl(events_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid:presentation/events.jsonl:{type(exc).__name__}"]

    snapshot_path = run_root / "presentation/snapshot.json"
    if not snapshot_path.is_file():
        return ["missing:presentation/snapshot.json"]
    try:
        snapshot = _read_json(snapshot_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid:presentation/snapshot.json:{type(exc).__name__}"]

    starts = {
        event.get("tool_call_id"): event
        for event in events
        if event.get("status") == "running" and event.get("tool_call_id")
    }
    terminal = {
        event.get("tool_call_id"): event
        for event in events
        if event.get("status") in {"accepted", "succeeded", "failed", "rejected"}
        and event.get("tool_call_id")
    }
    failures: list[str] = []
    if snapshot.get("schema_version") != 2:
        failures.append("presentation_schema_version")
    for field in sorted(PRESENTATION_REQUIRED_SNAPSHOT_FIELDS - set(snapshot)):
        failures.append(f"presentation_snapshot_missing:{field}")
    for event in events:
        if event.get("schema_version") != 2:
            failures.append(f"presentation_event_schema_version:{event.get('event_id')}")
        if event.get("event_type", "").startswith("tool."):
            if not event.get("tool_label_zh") or not event.get("tool_kind"):
                failures.append(f"presentation_tool_metadata:{event.get('event_id')}")
        if event.get("status") in {"failed", "rejected"}:
            if event.get("failure_code") not in PRESENTATION_FAILURE_CODES:
                failures.append(f"presentation_failure_code:{event.get('event_id')}")
        if event.get("plan") is not None and (
            event.get("tool_name") not in {"task_planner", "task_progress_check"}
            or event.get("status") != "succeeded"
        ):
            failures.append(f"presentation_plan_owner:{event.get('event_id')}")
        if (
            event.get("event_type") == "tool.call_completed"
            and event.get("tool_name") in {"task_planner", "task_progress_check"}
            and event.get("status") == "succeeded"
            and event.get("plan") is None
        ):
            failures.append(f"presentation_plan_missing:{event.get('event_id')}")
        if event.get("failure"):
            failures.append(str(event["failure"]))
        task = event.get("task") or {}
        source_text = task.get("source_text")
        source_hash = task.get("source_sha256")
        if source_text and hashlib.sha256(source_text.encode("utf-8")).hexdigest() != source_hash:
            failures.append(f"sop_source_hash_mismatch:{event.get('event_id')}")
    for tool_call_id in sorted(starts):
        started = starts[tool_call_id]
        completed = terminal.get(tool_call_id)
        if completed is None:
            failures.append(f"missing_terminal_event:{tool_call_id}")
        elif completed.get("action_id") != started.get("action_id"):
            failures.append(f"action_id_mismatch:{tool_call_id}")
    if not any((event.get("task") or {}).get("source_text") for event in events):
        failures.append("missing_sop_source_text")
    failed_by_action = {
        event.get("action_id"): event
        for event in events
        if event.get("status") in {"failed", "rejected"} and event.get("action_id")
    }
    incidents = snapshot.get("incidents") or []
    incident_actions = {incident.get("failed_action_id") for incident in incidents}
    for action_id in sorted(set(failed_by_action) - incident_actions):
        failures.append(f"presentation_incident_missing:{action_id}")
    for incident in incidents:
        action_id = incident.get("failed_action_id")
        source = failed_by_action.get(action_id)
        if source is None:
            failures.append(f"presentation_incident_orphan:{incident.get('incident_id')}")
            continue
        if incident.get("failure_code") != source.get("failure_code"):
            failures.append(f"presentation_incident_code:{incident.get('incident_id')}")
        recovery = incident.get("recovery")
        if recovery and recovery.get("resolved_sequence", 0) <= incident.get("opened_sequence", 0):
            failures.append(f"presentation_recovery_order:{incident.get('incident_id')}")
    event_ids = {event.get("event_id") for event in events}
    incident_ids = {incident.get("incident_id") for incident in incidents}
    for entry in snapshot.get("critical_history") or []:
        history_id = entry.get("history_id")
        if not history_id or entry.get("sequence", 0) > snapshot.get("last_sequence", 0):
            failures.append(f"presentation_history_event:{history_id}")
        if entry.get("kind") in {"incident", "recovery"} and not incident_ids:
            failures.append(f"presentation_history_incident:{history_id}")
    serialized = json.dumps({"events": events, "snapshot": snapshot}, ensure_ascii=False).lower()
    for forbidden in (
        "assistant.thinking",
        '"prompt"',
        '"headers"',
        '"authorization"',
        '"api_key"',
        '"constraints"',
        '"open_questions"',
    ):
        if forbidden in serialized:
            failures.append(f"presentation_forbidden_field:{forbidden}")
    if not event_ids and events:
        failures.append("presentation_event_identity")
    return list(dict.fromkeys(failures))


def verify_provider_identity(run_root: Path, expected_model: str) -> list[str]:
    failures: list[str] = []
    identity_path = run_root / "agent/provider_identity.json"
    runtime_path = run_root / "agent/runtime_events.jsonl"
    if not identity_path.is_file():
        return ["provider_identity_missing"]
    identity = _read_json(identity_path)
    created_at = identity.get("created_at_utc")
    if not isinstance(created_at, str):
        failures.append("provider_identity_timestamp")
    if identity.get("provider") != "Mimo":
        failures.append("provider_identity_provider")
    if identity.get("model") != expected_model:
        failures.append("provider_identity_model")
    if identity.get("scheme") != "https":
        failures.append("provider_identity_scheme")
    host = str(identity.get("host") or "").lower()
    if host != EXPECTED_PROVIDER_HOST:
        failures.append("provider_identity_endpoint")
    if host in {"localhost", "127.0.0.1", "::1"}:
        failures.append("provider_identity_loopback")
    if identity.get("provider_config_override") is not False:
        failures.append("provider_identity_override")
    fingerprint = identity.get("config_fingerprint_sha256")
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
    expected_fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_source,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if fingerprint != expected_fingerprint:
        failures.append("provider_identity_fingerprint")
    serialized_identity = json.dumps(identity, ensure_ascii=False).lower()
    if any(
        forbidden in serialized_identity
        for forbidden in ("authorization", "bearer ", '"api_keys"', '"base_url"')
    ):
        failures.append("provider_identity_secret_surface")
    if not runtime_path.is_file():
        return [*failures, "runtime_events_missing"]
    runtime = _read_jsonl(runtime_path)
    transport = [
        event
        for event in runtime
        if event.get("type") in {"transport.request_started", "transport.response_completed"}
    ]
    requests = [event for event in transport if event.get("type") == "transport.request_started"]
    responses = [
        event for event in transport if event.get("type") == "transport.response_completed"
    ]
    if not requests:
        failures.append("provider_request_missing")
    if not responses:
        failures.append("provider_success_response_missing")
    transport_positions: dict[str, dict[int, list[int]]] = {
        "transport.request_started": {},
        "transport.response_completed": {},
    }
    invalid_iteration = False
    for index, event in enumerate(runtime):
        event_type = event.get("type")
        if event_type not in transport_positions:
            continue
        iteration = (event.get("payload") or {}).get("iteration")
        if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 0:
            invalid_iteration = True
            continue
        transport_positions[event_type].setdefault(iteration, []).append(index)
    if invalid_iteration:
        failures.append("provider_transport_iteration")
    request_positions = transport_positions["transport.request_started"]
    response_positions = transport_positions["transport.response_completed"]
    if any(
        len(positions) != 1
        for positions in (*request_positions.values(), *response_positions.values())
    ):
        failures.append("provider_transport_duplicate_iteration")
    request_iterations = set(request_positions)
    response_iterations = set(response_positions)
    if request_iterations != response_iterations:
        failures.append("provider_transport_pairing")
    paired_iterations = request_iterations & response_iterations
    if paired_iterations and paired_iterations != set(range(len(paired_iterations))):
        failures.append("provider_transport_iteration_sequence")
    if any(
        request_positions[iteration][0] >= response_positions[iteration][0]
        for iteration in paired_iterations
        if len(request_positions[iteration]) == len(response_positions[iteration]) == 1
    ):
        failures.append("provider_transport_order")
    transport_state = "idle"
    for event in runtime:
        if event.get("type") == "transport.request_started":
            transport_state = "pending"
        elif event.get("type") == "transport.response_completed":
            transport_state = "completed"
        elif event.get("type") == "tool.call_started" and transport_state != "completed":
            failures.append("provider_tool_without_response")
            break
    for event in transport:
        if (event.get("payload") or {}).get("model") != expected_model:
            failures.append("runtime_model_identity")
        if "scripted-coworker" in json.dumps(event, ensure_ascii=False):
            failures.append("runtime_scripted_provider")
    if requests and isinstance(created_at, str):
        try:
            identity_time = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            request_time = datetime.fromisoformat(
                str(requests[0].get("timestamp", "")).replace("Z", "+00:00")
            )
            if identity_time > request_time:
                failures.append("provider_identity_after_request")
        except ValueError:
            failures.append("provider_identity_timestamp")
    if any((event.get("payload") or {}).get("status") != "ok" for event in responses):
        failures.append("provider_response_status")
    return list(dict.fromkeys(failures))


def _expected_config_record(state: dict[str, Any]) -> tuple[str, dict[str, str], str]:
    variables = state["variables"]
    expected = {
        key: variables[key] for key in ("ExtensionName", "ItemCode", "SpecCode", "TenantId")
    }
    key = f"{variables['TenantId']}:{variables['ItemCode']}"
    stdout = json.dumps({key: expected}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return key, expected, stdout + "\n"


def _successful_job(
    raw: list[dict[str, Any]], operation: str, run_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    submitted = next(
        (
            event
            for event in raw
            if event.get("kind") == "automation_job_submitted"
            and (event.get("arguments") or {}).get("operation") == operation
            and (event.get("arguments") or {}).get("run_id") == run_id
        ),
        None,
    )
    if submitted is None:
        return None, None
    job_id = (submitted.get("arguments") or {}).get("job_id")
    succeeded = next(
        (
            event
            for event in raw
            if event.get("kind") == "automation_job_status"
            and event.get("status") == "succeeded"
            and (event.get("arguments") or {}).get("job_id") == job_id
        ),
        None,
    )
    return submitted, succeeded


def verify_external_end_state(run_root: Path, scenario: str) -> list[str]:
    failures: list[str] = []
    state = _read_json(run_root / "environment/state.json")
    raw = _read_jsonl(run_root / "trajectory/raw_actions.jsonl")
    config = _read_json(
        run_root
        / "environment/episode_root/service_layer/component/config/extension_item_mapping.json"
    )
    commands_path = run_root / "terminal/commands.jsonl"
    commands = _read_jsonl(commands_path) if commands_path.is_file() else []
    key, expected_record, expected_stdout = _expected_config_record(state)
    run_id = str(state.get("run_id") or "")

    add_submit, add_success = _successful_job(raw, "add", run_id)
    if add_submit is None:
        failures.append("add_job_missing")
    if add_success is None or (add_success.get("arguments") or {}).get("return_code") != 0:
        failures.append("add_job_return_code")
    if not commands or commands[0].get("exit_code") != 0:
        failures.append("add_grep_exit")
    else:
        stdout_path = run_root / str(commands[0].get("stdout_path"))
        stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.is_file() else None
        if stdout != expected_stdout:
            failures.append("add_grep_stdout")

    if scenario == "normal":
        if config.get(key) != expected_record or len(config.get(key, {})) != 4:
            failures.append("normal_external_config")
        _, business_success = _successful_job(raw, "business_verify", run_id)
        if (
            business_success is None
            or (business_success.get("arguments") or {}).get("return_code") != 0
        ):
            failures.append("normal_business_job_return_code")
        if state.get("terminal_outcome") != "complete":
            failures.append("normal_terminal_outcome")
        return failures

    if key in config:
        failures.append("anomaly_external_config")
    add_job_id = (add_submit.get("arguments") or {}).get("job_id") if add_submit else None
    alarm = next(
        (
            event
            for event in raw
            if event.get("node_id") == "ANOMALY_FOUND"
            and (event.get("arguments") or {}).get("caused_by_current_change") is True
        ),
        None,
    )
    if alarm is None or (alarm.get("arguments") or {}).get("causal_add_job_id") != add_job_id:
        failures.append("anomaly_causal_alarm_job")
    rollback = next((event for event in raw if event.get("node_id") == "ROLLBACK_DECISION"), None)
    remove_submit, remove_success = _successful_job(raw, "remove", run_id)
    if (
        rollback is None
        or remove_submit is None
        or rollback.get("sequence", 0) >= remove_submit.get("sequence", 0)
    ):
        failures.append("anomaly_rollback_order")
    if remove_success is None or (remove_success.get("arguments") or {}).get("return_code") != 0:
        failures.append("anomaly_remove_return_code")
    if len(commands) < 2 or commands[1].get("exit_code") != 1:
        failures.append("anomaly_rollback_grep_exit")
    else:
        stdout_path = run_root / str(commands[1].get("stdout_path"))
        stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.is_file() else None
        if stdout != "":
            failures.append("anomaly_rollback_grep_stdout")
    if state.get("terminal_outcome") != "rolled_back":
        failures.append("anomaly_terminal_outcome")
    return failures


def verify(
    run_root: Path,
    data_root: Path,
    *,
    expected_model: str | None = None,
) -> dict[str, Any]:
    run_root = run_root.resolve()
    failures: list[str] = []
    summary = _read_json(run_root / "scores/summary.json")
    scenario = summary["scenario_id"]
    attempt_path = run_root / "attempt_manifest.json"
    if not attempt_path.is_file():
        failures.append("attempt_manifest_missing")
    else:
        attempt = _read_json(attempt_path)
        if attempt.get("run_id") != summary.get("run_id"):
            failures.append("attempt_manifest_run_id")
        if attempt.get("status") != "completed" or attempt.get("formal_success") is not True:
            failures.append("attempt_manifest_status")
    if expected_model is not None:
        failures.extend(verify_provider_identity(run_root, expected_model))
    dag = yaml.safe_load((data_root / "agent_trajectory_ground_truth.yaml").read_text())
    required = {
        node["node_id"] for node in dag["nodes"] if scenario in node["required_in_scenarios"]
    }
    raw = _read_jsonl(run_root / "trajectory/raw_actions.jsonl")
    known_evidence: set[str] = set()
    for event in raw:
        if event.get("source") == "decision":
            unknown = sorted(set(event.get("evidence_refs", [])).difference(known_evidence))
            if unknown:
                failures.append(f"unknown_evidence_ref:{event.get('event_id')}:{','.join(unknown)}")
        if event.get("status") in {"accepted", "succeeded"}:
            if event.get("event_id"):
                known_evidence.add(event["event_id"])
            if event.get("source") in {"backend", "browser", "terminal", "state"}:
                known_evidence.update(event.get("evidence_refs", []))
            if event.get("kind") == "automation_job_submitted":
                job_id = event.get("arguments", {}).get("job_id")
                if job_id:
                    known_evidence.add(f"job-{job_id}-accepted")
    observed = {
        event.get("node_id") for event in raw if event.get("status") in {"accepted", "succeeded"}
    }
    config_variants = {
        event.get("arguments", {}).get("check")
        for event in raw
        if event.get("node_id") == "PRE_CONFIG"
    }
    if not {"extension_config", "upstream_ready"}.issubset(config_variants):
        observed.discard("PRE_CONFIG")
    missing_nodes = sorted(required - observed)
    if missing_nodes:
        failures.append(f"missing_nodes:{','.join(missing_nodes)}")

    expected_nodes = 24 if scenario == "normal" else 22
    expected_results = 14 if scenario == "normal" else 11
    for key, expected in (
        ("required_nodes", expected_nodes),
        ("matched_nodes", expected_nodes),
        ("required_checkpoints", expected_results),
        ("passed_checkpoints", expected_results),
    ):
        if summary.get(key) != expected:
            failures.append(f"summary_{key}:{summary.get(key)}")
    for key in ("trajectory_score", "result_score", "overall_score"):
        if summary.get(key) != 100.0:
            failures.append(f"summary_{key}:{summary.get(key)}")
    if summary.get("formal_success") is not True:
        failures.append("formal_success_not_true")
    if summary.get("video_verification") != "passed":
        failures.append("video_not_passed")

    presentation_failures = verify_presentation_bundle(run_root)
    verification_path = run_root / "presentation/verification.json"
    if verification_path.is_file():
        presentation_report = _read_json(verification_path)
        if not presentation_report.get("observer_was_alive", False):
            presentation_failures.append("observer_exited_before_recording_stop")
        presentation_failures = list(dict.fromkeys(presentation_failures))
        if presentation_report.get("schema_version") != 2:
            failures.append("presentation_report_schema_version")
        reported_failures = presentation_report.get("failures") or []
        if reported_failures:
            failures.append("presentation_product_failures")
        if presentation_report.get("passed") is not True:
            failures.append("presentation_pass_flag_mismatch")
    else:
        failures.append("missing:presentation/verification.json")
    failures.extend(presentation_failures)
    failures.extend(verify_external_end_state(run_root, scenario))

    if expected_model is not None:
        runtime = _read_jsonl(run_root / "agent/runtime_events.jsonl")
        runtime_starts = [event for event in runtime if event.get("type") == "tool.call_started"]
        presentation_events = _read_jsonl(run_root / "presentation/events.jsonl")
        presentation_starts = [
            event for event in presentation_events if event.get("event_type") == "tool.call_started"
        ]
        if len(runtime_starts) != len(presentation_starts):
            failures.append("runtime_presentation_tool_count")
        presentation_failures_by_call = {
            event.get("tool_call_id")
            for event in presentation_events
            if event.get("status") in {"failed", "rejected"} and event.get("failure_code")
        }
        runtime_failed_calls = {
            event.get("tool_call_id")
            for event in runtime
            if event.get("type") == "tool.call_failed"
        }
        if not runtime_failed_calls <= presentation_failures_by_call:
            failures.append("runtime_presentation_failure_correlation")

    commands_path = run_root / "terminal/commands.jsonl"
    commands = _read_jsonl(commands_path) if commands_path.is_file() else []
    exits = [command.get("exit_code") for command in commands]
    if scenario == "normal" and exits != [0]:
        failures.append(f"normal_terminal_exits:{exits}")
    if scenario == "post_change_anomaly" and exits != [0, 1]:
        failures.append(f"anomaly_terminal_exits:{exits}")
    terminal_ids = [command.get("evidence_id") for command in commands]
    if len(terminal_ids) != len(set(terminal_ids)):
        failures.append("terminal_evidence_reused")

    state = _read_json(run_root / "environment/state.json")
    episode_file = (
        run_root
        / "environment/episode_root/service_layer/component/config/extension_item_mapping.json"
    )
    config = _read_json(episode_file)
    key = f"{state['variables']['TenantId']}:{state['variables']['ItemCode']}"
    if scenario == "normal" and key not in config:
        failures.append("normal_config_absent")
    if scenario == "post_change_anomaly" and key in config:
        failures.append("anomaly_config_present")

    manifest = _read_json(run_root / "run_manifest.json")
    manifest_artifacts = manifest.get("artifacts", {})
    for relative in REQUIRED_MANIFEST_ARTIFACTS:
        if relative not in manifest_artifacts:
            failures.append(f"missing_manifest_entry:{relative}")
    for relative, entry in manifest_artifacts.items():
        path = (run_root / relative).resolve()
        if run_root not in path.parents or not path.is_file():
            failures.append(f"manifest_missing:{relative}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            failures.append(f"manifest_hash:{relative}")
        elif entry.get("complete") is not True:
            failures.append(f"manifest_incomplete:{relative}")

    video = run_root / "video/demo.mp4"
    probe = subprocess.run(
        [
            "/usr/bin/ffprobe",
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height,pix_fmt,duration,nb_read_frames",
            "-of",
            "json",
            str(video),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        failures.append(f"ffprobe_return:{probe.returncode}")
        stream = {}
    else:
        streams = json.loads(probe.stdout).get("streams", [])
        stream = streams[0] if len(streams) == 1 else {}
    expected_video = {
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
    }
    for key_name, expected in expected_video.items():
        if stream.get(key_name) != expected:
            failures.append(f"video_{key_name}:{stream.get(key_name)}")
    if float(stream.get("duration", 0)) < 4 or int(stream.get("nb_read_frames", 0)) < 1:
        failures.append("video_duration_or_frames")
    video_manifest = _read_json(run_root / "video/video_manifest.json")
    if not video_manifest.get("verified"):
        failures.append("video_manifest_unverified")
    if video_manifest.get("ffmpeg_return_code") != 0:
        failures.append(f"ffmpeg_return:{video_manifest.get('ffmpeg_return_code')}")
    video_sha256 = hashlib.sha256(video.read_bytes()).hexdigest() if video.is_file() else None
    if video_manifest.get("sha256") != video_sha256:
        failures.append("video_manifest_sha256")
    if summary.get("video_manifest_sha256") != video_sha256:
        failures.append("summary_video_sha256")
    first_packet = video_manifest.get("first_packet") or {}
    progress = first_packet.get("progress") or {}
    positive_sizes = [size for size in first_packet.get("size_samples", []) if size > 0]
    if (
        progress.get("frame", 0) < 1
        or progress.get("total_size", 0) <= 28
        or len(set(positive_sizes)) < 2
    ):
        failures.append("first_packet_not_proven")
    timebase = video_manifest.get("recording_timebase") or {}
    for field in (
        "recording_started_utc",
        "recording_started_monotonic_s",
        "first_packet_ready_utc",
        "first_packet_ready_monotonic_s",
        "first_packet_ffmpeg_out_time_s",
    ):
        if field not in timebase:
            failures.append(f"video_timebase_missing:{field}")

    try:
        duration = float(stream.get("duration", 0))
        frame_stats: dict[str, dict[str, float]] = {}
        grayscale: dict[str, bytes] = {}
        for name, timestamp in {
            "first": 0.2,
            "middle": duration / 2,
            "last": max(0.2, duration - 0.3),
        }.items():
            frame = _extract_frame(video, timestamp)
            frame_stats[name], grayscale[name] = _frame_stats(frame, 1920, 1080)
            if (
                frame_stats[name]["nonblack_ratio"] < 0.05
                or frame_stats[name]["dark_ratio"] < 0.05
                or frame_stats[name]["variance"] < 5
            ):
                failures.append(f"video_{name}_frame_blank")
        if _changed_pixels(grayscale["first"], grayscale["last"]) < 1000:
            failures.append("video_first_last_unchanged")
        presentation_snapshot = _read_json(run_root / "presentation/snapshot.json")
        expected_frame_names = {"first_model_action", "terminal_outcome"}
        for incident in presentation_snapshot.get("incidents") or []:
            expected_frame_names.add(f"incident_open_{incident.get('opened_sequence')}")
            recovery = incident.get("recovery")
            if recovery:
                expected_frame_names.add(f"incident_resolved_{recovery.get('resolved_sequence')}")
        if scenario == "post_change_anomaly":
            expected_frame_names.add("causal_alarm")
        event_frames = video_manifest.get("event_frames") or {}
        available_names = set(event_frames)
        if "causal_alarm" in expected_frame_names and not any(
            name.startswith("causal_alarm_") for name in available_names
        ):
            failures.append("video_event_frame_missing:causal_alarm")
        expected_frame_names.discard("causal_alarm")
        for name in sorted(expected_frame_names - available_names):
            failures.append(f"video_event_frame_missing:{name}")
        presentation_events = _read_jsonl(run_root / "presentation/events.jsonl")
        presentation_by_id = {
            event.get("event_id"): event for event in presentation_events if event.get("event_id")
        }
        for name, entry in event_frames.items():
            timestamp = entry.get("timestamp_s")
            calculated_offset = entry.get("calculated_offset_s")
            source_offset = entry.get("source_monotonic_offset_s")
            margin = entry.get("ui_settle_margin_s")
            if (
                not isinstance(timestamp, int | float)
                or not isinstance(calculated_offset, int | float)
                or not isinstance(source_offset, int | float)
                or not isinstance(margin, int | float)
                or abs(timestamp - (source_offset + margin)) > 0.001
                or abs(timestamp - calculated_offset) > 0.001
                or entry.get("ffmpeg_basis") != "recording_monotonic_origin"
                or timestamp < 0
                or timestamp > duration
            ):
                failures.append(f"video_event_frame_offset:{name}")
                continue
            source_event = presentation_by_id.get(entry.get("source_event_id"))
            if source_event is None:
                failures.append(f"video_event_frame_source:{name}")
            else:
                next_event = next(
                    (
                        event
                        for event in presentation_events
                        if event.get("sequence", 0) > source_event.get("sequence", 0)
                        and isinstance(event.get("monotonic_offset_s"), int | float)
                    ),
                    None,
                )
                if next_event is not None and timestamp >= next_event["monotonic_offset_s"]:
                    failures.append(f"video_event_frame_crossed_next_event:{name}")
            frame = _extract_frame(video, float(timestamp))
            observer = _crop_rgb(frame, 1920, 1080, (1320, 96, 1920, 996))
            observer_stats, _ = _frame_stats(observer, 600, 900)
            if observer_stats["variance"] < 5 or observer_stats["nonblack_ratio"] < 0.05:
                failures.append(f"video_event_frame_blank:{name}")
    except (OSError, RuntimeError, ValueError) as exc:
        failures.append(f"video_frame_verification:{type(exc).__name__}:{exc}")

    return {
        "schema_version": 1,
        "run_id": summary["run_id"],
        "scenario_id": scenario,
        "required_nodes": expected_nodes,
        "observed_required_nodes": len(required & observed),
        "required_checkpoints": expected_results,
        "terminal_exit_codes": exits,
        "video_sha256": video_sha256,
        "failures": failures,
        "pass": not failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--data-root", type=Path, default=Path("data/coworker_demo/case_02"))
    parser.add_argument("--expected-model")
    args = parser.parse_args()
    result = verify(
        args.run_root,
        args.data_root.resolve(),
        expected_model=args.expected_model,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
