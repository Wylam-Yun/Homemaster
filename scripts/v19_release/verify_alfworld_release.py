#!/usr/bin/env python3
"""Independently verify a V1.9 ALFWorld migration or release report."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Literal

SCHEMA_VERSION = "homemaster-v1.9-alfworld-release-run-v1"
MANIFEST_SCHEMA_VERSION = "alfworld-v19-release-trials-v1"
EXECUTION_MANIFEST_SCHEMA_VERSION = "alfworld-trial-selection-v1"
AGENT_CLASSIFICATIONS = {"agent_success", "agent_model_failure"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GateMode = Literal["migration", "release"]

ROOT_KEYS = {
    "artifacts",
    "candidate_sha",
    "episodes",
    "execution_manifest_ref",
    "manifest",
    "profile",
    "provider",
    "run_id",
    "schema_version",
    "summary",
    "summary_ref",
}
PROFILE = {
    "env_type": "AlfredThorEnv",
    "episodes": 4,
    "observation_mode": "visual_eval",
    "split": "valid_unseen",
}
PROVIDER_KEYS = {"api_format", "model", "name", "transport"}
MANIFEST_KEYS = {"entries", "sha256"}
MANIFEST_ENTRY_KEYS = {
    "expected_logical_scene",
    "goal_fingerprint",
    "rank_digest",
    "source_rank",
    "trial_id",
    "trial_sha256",
}
EPISODE_KEYS = {
    "action_counts",
    "classification",
    "index",
    "model_trace_ref",
    "provider_attempt_count",
    "provider_attempts_ref",
    "reset_ref",
    "run_id",
    "runtime_events_ref",
    "score_eligible",
    "successful_provider_attempt_count",
    "success",
    "summary_ref",
    "terminal_evidence_ref",
    "terminal_tool_call_id",
    "trace_ref",
    "trial_id",
}
ACTION_COUNT_KEYS = {
    "agent_tool_calls",
    "control_backend_actions",
    "model_backend_actions",
    "setup_backend_actions",
    "total_backend_actions",
    "total_external_requests",
}
SUMMARY_KEYS = {
    "attempted",
    "contract_violations",
    "eligible",
    "formal_score_available",
    "harness_invalid",
    "selected",
    "success",
    "task_failures",
}
ATTEMPT_KEYS = {
    "cause_code",
    "error_type",
    "model_attempt_id",
    "outbound_images",
    "request_sha256",
    "response_completed",
    "stripped_images",
}
EXECUTION_ENTRY_KEYS = {
    "expected_logical_scene",
    "goal_fingerprint",
    "goal_identity",
    "identity_status",
    "trial_id",
    "trial_sha256",
}


def verify_alfworld_release(
    report_path: Path,
    *,
    manifest_path: Path,
    gate: GateMode,
    expected_sha: str,
    expect_selected: int | None = None,
    expect_attempted: int | None = None,
    expect_eligible: int | None = None,
    expect_success: int | None = None,
) -> dict[str, Any]:
    if gate not in {"migration", "release"}:
        raise ValueError("gate must be migration or release")
    expected_sha = _commit(expected_sha, label="expected SHA")
    root = report_path.resolve(strict=True).parent
    report = _read_object(report_path, label="ALFWorld release report")
    _exact_keys(report, ROOT_KEYS, label="ALFWorld release report")
    if report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported ALFWorld release report schema")
    if _commit(report["candidate_sha"], label="candidate SHA") != expected_sha:
        raise ValueError("candidate SHA does not match expected SHA")

    artifacts = _verify_artifacts(report["artifacts"], root=root)
    manifest = _verify_manifest_binding(
        report["manifest"],
        manifest_path=manifest_path,
    )
    profile = _object(report["profile"], label="profile")
    if profile != PROFILE:
        raise ValueError("ALFWorld release runtime profile drifted")
    provider = _verify_provider(report["provider"])

    summary_ref = _required_ref(report["summary_ref"], artifacts)
    execution_ref = _required_ref(report["execution_manifest_ref"], artifacts)
    execution = _read_object(_artifact_path(root, execution_ref), label="execution manifest")
    _verify_execution_manifest(execution, manifest_entries=manifest["entries"])

    persisted_summary = _read_object(
        _artifact_path(root, summary_ref),
        label="persisted ALFWorld summary",
    )
    summary_config = _object(persisted_summary.get("config"), label="summary config")
    for key, expected in PROFILE.items():
        if summary_config.get(key) != expected:
            raise ValueError(f"persisted summary profile mismatch: {key}")
    if summary_config.get("provider_name") != provider["name"]:
        raise ValueError("provider identity mismatches persisted summary")
    if persisted_summary.get("run_id") != report["run_id"]:
        raise ValueError("run identity mismatches persisted summary")

    raw_summary_episodes = persisted_summary.get("episodes")
    raw_report_episodes = report["episodes"]
    if not isinstance(raw_summary_episodes, list) or not isinstance(raw_report_episodes, list):
        raise ValueError("episode evidence must be arrays")
    if len(raw_summary_episodes) != len(manifest["entries"]):
        raise ValueError("persisted summary episode count mismatches locked manifest")
    if len(raw_report_episodes) != len(manifest["entries"]):
        raise ValueError("report episode count mismatches locked manifest")

    seen_attempt_ids: set[str] = set()
    verified_episodes = []
    for index, (entry, summary_episode, report_episode) in enumerate(
        zip(manifest["entries"], raw_summary_episodes, raw_report_episodes, strict=True)
    ):
        verified_episodes.append(
            _verify_episode(
                index=index,
                manifest_entry=entry,
                summary_episode=_object(summary_episode, label=f"summary episode {index}"),
                report_episode=_object(report_episode, label=f"report episode {index}"),
                provider=provider,
                root=root,
                artifacts=artifacts,
                seen_attempt_ids=seen_attempt_ids,
            )
        )

    selected = len(manifest["entries"])
    attempted = sum(int(item["attempted"]) for item in verified_episodes)
    eligible = sum(int(item["score_eligible"]) for item in verified_episodes)
    success = sum(int(item["success"]) for item in verified_episodes)
    harness_invalid = selected - eligible
    task_failures = [item["trial_id"] for item in verified_episodes if not item["success"]]
    formal_score_available = (
        selected == 4
        and attempted == selected
        and eligible == selected
        and harness_invalid == 0
    )
    recomputed = {
        "selected": selected,
        "attempted": attempted,
        "eligible": eligible,
        "success": success,
        "formal_score_available": formal_score_available,
        "harness_invalid": harness_invalid,
        "contract_violations": 0,
        "task_failures": task_failures,
    }
    declared_summary = _object(report["summary"], label="report summary")
    _exact_keys(declared_summary, SUMMARY_KEYS, label="report summary")
    if declared_summary != recomputed:
        raise ValueError("declared report summary does not match independently recomputed values")
    if persisted_summary.get("formal_score_available") is not True:
        raise ValueError("persisted formal score is unavailable")
    if persisted_summary.get("harness_invalid_episodes") != harness_invalid:
        raise ValueError("persisted Harness invalid count drifted")
    if not formal_score_available:
        raise ValueError("formal score is unavailable after evidence recomputation")

    expectations = {
        "selected": expect_selected,
        "attempted": expect_attempted,
        "eligible": expect_eligible,
    }
    for name, expected in expectations.items():
        if expected is not None and recomputed[name] != expected:
            raise ValueError(f"expected {name}={expected}, got {recomputed[name]}")
    if gate == "migration" and expect_success is not None:
        raise ValueError("migration gate must report task failures without a success expectation")
    required_success = expect_success if expect_success is not None else selected
    if gate == "release" and success != required_success:
        raise ValueError(f"expected success={required_success}, got {success}")

    return {
        "status": "PASS",
        "gate": gate,
        "candidate_sha": expected_sha,
        **recomputed,
    }


def _verify_episode(
    *,
    index: int,
    manifest_entry: dict[str, Any],
    summary_episode: dict[str, Any],
    report_episode: dict[str, Any],
    provider: dict[str, str],
    root: Path,
    artifacts: dict[str, str],
    seen_attempt_ids: set[str],
) -> dict[str, Any]:
    _exact_keys(report_episode, EPISODE_KEYS, label=f"report episode {index}")
    if report_episode["index"] != index or report_episode["trial_id"] != manifest_entry["trial_id"]:
        raise ValueError(f"episode {index} order does not match locked manifest")
    if report_episode["run_id"] != summary_episode.get("run_id"):
        raise ValueError(f"episode {index} run identity drifted")

    refs = {
        name: _required_ref(report_episode[name], artifacts)
        for name in (
            "model_trace_ref",
            "provider_attempts_ref",
            "reset_ref",
            "runtime_events_ref",
            "summary_ref",
            "trace_ref",
        )
    }
    attempts = _read_jsonl(
        _artifact_path(root, refs["provider_attempts_ref"]),
        label=f"episode {index} provider attempts",
        allow_empty=False,
    )
    successful_attempts = 0
    for attempt_index, attempt in enumerate(attempts):
        _exact_keys(attempt, ATTEMPT_KEYS, label=f"episode {index} attempt {attempt_index}")
        attempt_id = attempt["model_attempt_id"]
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id in seen_attempt_ids:
            raise ValueError("provider attempt IDs must be non-empty and globally unique")
        seen_attempt_ids.add(attempt_id)
        _sha256(attempt["request_sha256"], label="provider request hash")
        if not isinstance(attempt["outbound_images"], list):
            raise ValueError("provider outbound image bindings must be an array")
        if not isinstance(attempt["response_completed"], bool) or not isinstance(
            attempt["stripped_images"], bool
        ):
            raise ValueError("provider attempt completion fields must be booleans")
        successful_attempts += int(attempt["response_completed"])
    if attempts[0]["outbound_images"]:
        raise ValueError("initial ALFWorld provider request must not contain a screenshot")

    runtime_events = _read_jsonl(
        _artifact_path(root, refs["runtime_events_ref"]),
        label=f"episode {index} runtime events",
        allow_empty=False,
    )
    request_events = [row for row in runtime_events if row.get("type") == "transport.request_started"]
    if len(request_events) != len(attempts):
        raise ValueError(f"episode {index} provider attempt ledger/event count drifted")
    for event in request_events:
        payload = _object(event.get("payload"), label="transport request payload")
        if payload.get("model") != provider["model"]:
            raise ValueError("provider model identity mismatches runtime evidence")
    tool_calls = [row for row in runtime_events if row.get("type") == "tool.call_started"]
    tool_call_ids = [row.get("tool_call_id") for row in tool_calls]
    if any(not isinstance(value, str) or not value for value in tool_call_ids):
        raise ValueError("runtime tool call identity is missing")
    if len(tool_call_ids) != len(set(tool_call_ids)):
        raise ValueError("runtime tool call identities are not unique within an episode")

    trace_path = _artifact_path(root, refs["trace_ref"])
    trace_rows = _read_jsonl(trace_path, label=f"episode {index} action trace", allow_empty=True)
    trace_model_actions = 0
    step_row_keys = {"backend_action_count", "tool_args", "tool_name", "tool_success"}
    for row in trace_rows:
        present_step_keys = step_row_keys.intersection(row)
        if not present_step_keys:
            continue
        if present_step_keys != step_row_keys:
            raise ValueError("action trace contains a malformed canonical step row")
        if not isinstance(row["tool_name"], str) or not row["tool_name"]:
            raise ValueError("action trace step tool name is invalid")
        if not isinstance(row["tool_args"], dict) or not isinstance(row["tool_success"], bool):
            raise ValueError("action trace step payload is invalid")
        count = row.get("backend_action_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("action trace backend_action_count is invalid")
        trace_model_actions += count
    _read_jsonl(
        _artifact_path(root, refs["model_trace_ref"]),
        label=f"episode {index} model trace",
        allow_empty=False,
    )

    reset = _read_object(_artifact_path(root, refs["reset_ref"]), label="reset transaction")
    if reset.get("schema_version") != "alfworld-reset-transaction-v1":
        raise ValueError("reset transaction schema drifted")
    if reset.get("ready") is not True:
        raise ValueError("reset transaction was not ready")
    if reset.get("goal_trial_fingerprint") != manifest_entry["goal_fingerprint"]:
        raise ValueError("reset goal identity does not match locked trial order")

    episode_summary = _read_object(
        _artifact_path(root, refs["summary_ref"]),
        label=f"episode {index} summary artifact",
    )
    for key in (
        "classification",
        "failure_reason",
        "goal_condition_success_rate",
        "invalid_actions",
        "run_id",
        "runtime_status",
        "steps",
        "success",
    ):
        if episode_summary.get(key) != summary_episode.get(key):
            raise ValueError(f"episode {index} persisted summary field drifted: {key}")

    counts = {
        "agent_tool_calls": _nonnegative_int(
            summary_episode.get("agent_tool_call_count"), label="agent tool call count"
        ),
        "setup_backend_actions": _nonnegative_int(
            summary_episode.get("setup_backend_action_count"), label="setup action count"
        ),
        "control_backend_actions": _nonnegative_int(
            summary_episode.get("control_backend_action_count"), label="control action count"
        ),
        "model_backend_actions": _nonnegative_int(
            summary_episode.get("model_backend_action_count"), label="model action count"
        ),
        "total_backend_actions": _nonnegative_int(
            summary_episode.get("total_backend_action_count"), label="total backend action count"
        ),
        "total_external_requests": _nonnegative_int(
            summary_episode.get("total_external_request_count"),
            label="total external request count",
        ),
    }
    if counts["agent_tool_calls"] != len(tool_calls):
        raise ValueError(f"episode {index} agent tool call count drifted")
    if counts["model_backend_actions"] != trace_model_actions:
        raise ValueError(f"episode {index} model action count does not reconcile to trace")
    if summary_episode.get("backend_action_count") != counts["model_backend_actions"]:
        raise ValueError(f"episode {index} legacy backend action count drifted")
    if reset.get("setup_backend_action_count") != counts["setup_backend_actions"]:
        raise ValueError(f"episode {index} setup action count drifted")
    if counts["control_backend_actions"] != 0:
        raise ValueError("single-episode release run cannot contain control backend actions")
    if counts["total_backend_actions"] != (
        counts["setup_backend_actions"]
        + counts["control_backend_actions"]
        + counts["model_backend_actions"]
    ):
        raise ValueError(f"episode {index} total backend action count drifted")
    if counts["total_external_requests"] != counts["total_backend_actions"]:
        raise ValueError(f"episode {index} external request count drifted")

    classification = summary_episode.get("classification")
    score_eligible = summary_episode.get("score_eligible") is True
    success = summary_episode.get("success") is True
    if score_eligible != (classification in AGENT_CLASSIFICATIONS):
        raise ValueError(f"episode {index} score eligibility/classification drifted")
    if success != (classification == "agent_success"):
        raise ValueError(f"episode {index} success/classification drifted")
    terminal_call_id = summary_episode.get("terminal_tool_call_id")
    terminal_evidence_ref = summary_episode.get("terminal_evidence_ref")
    if terminal_call_id is not None:
        if terminal_call_id not in tool_call_ids:
            raise ValueError(f"episode {index} terminal call ID is absent from runtime trace")
        _verify_line_evidence_ref(terminal_evidence_ref, trace_path=trace_path)
    elif terminal_evidence_ref is not None:
        raise ValueError(f"episode {index} terminal evidence has no terminal call ID")

    declared_counts = _object(report_episode["action_counts"], label="action counts")
    _exact_keys(declared_counts, ACTION_COUNT_KEYS, label="action counts")
    declared = {
        **report_episode,
        "action_counts": declared_counts,
    }
    expected_declared = {
        "index": index,
        "trial_id": manifest_entry["trial_id"],
        "run_id": summary_episode.get("run_id"),
        **{key: refs[key] for key in refs},
        "provider_attempt_count": len(attempts),
        "successful_provider_attempt_count": successful_attempts,
        "classification": classification,
        "success": success,
        "score_eligible": score_eligible,
        "action_counts": counts,
        "terminal_tool_call_id": terminal_call_id,
        "terminal_evidence_ref": terminal_evidence_ref,
    }
    if declared != expected_declared:
        raise ValueError(f"episode {index} declared evidence does not match artifacts")
    return {
        "trial_id": manifest_entry["trial_id"],
        "attempted": bool(attempts),
        "score_eligible": score_eligible,
        "success": success,
    }


def _verify_manifest_binding(value: Any, *, manifest_path: Path) -> dict[str, Any]:
    binding = _object(value, label="manifest binding")
    _exact_keys(binding, MANIFEST_KEYS, label="manifest binding")
    actual_hash = _sha256_file(manifest_path)
    if _sha256(binding["sha256"], label="manifest hash") != actual_hash:
        raise ValueError("locked manifest hash mismatches report")
    manifest = _read_object(manifest_path, label="locked release manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("locked release manifest schema drifted")
    if manifest.get("selection") != {
        "algorithm": "sha256-rank-v1",
        "count": 4,
        "seed": "homemaster-v1.9-release",
    }:
        raise ValueError("locked release manifest selection policy drifted")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != 4:
        raise ValueError("locked release manifest must contain exactly four entries")
    entries = []
    for index, raw in enumerate(raw_entries):
        entry = _object(raw, label=f"manifest entry {index}")
        projected = {key: entry.get(key) for key in MANIFEST_ENTRY_KEYS}
        if set(entry) != MANIFEST_ENTRY_KEYS | {"goal_identity", "identity_status"}:
            raise ValueError(f"manifest entry {index} keys drifted")
        for key in ("goal_fingerprint", "rank_digest", "trial_sha256"):
            _sha256(projected[key], label=f"manifest entry {index} {key}")
        if projected["source_rank"] != index + 1:
            raise ValueError("locked release manifest order/rank drifted")
        entries.append(projected)
    if binding["entries"] != entries:
        raise ValueError("locked manifest identity/order mismatches report")
    return {"entries": entries, "sha256": actual_hash}


def _verify_execution_manifest(value: dict[str, Any], *, manifest_entries: list[dict[str, Any]]) -> None:
    _exact_keys(value, {"entries", "schema_version"}, label="execution manifest")
    if value["schema_version"] != EXECUTION_MANIFEST_SCHEMA_VERSION:
        raise ValueError("execution manifest schema drifted")
    entries = value["entries"]
    if not isinstance(entries, list) or len(entries) != len(manifest_entries):
        raise ValueError("execution manifest count drifted")
    for actual, locked in zip(entries, manifest_entries, strict=True):
        if not isinstance(actual, dict):
            raise ValueError("execution manifest entry must be an object")
        _exact_keys(actual, EXECUTION_ENTRY_KEYS, label="execution manifest entry")
        expected = {
            key: locked[key]
            for key in (
                "expected_logical_scene",
                "goal_fingerprint",
                "trial_id",
                "trial_sha256",
            )
        }
        if any(actual.get(key) != expected[key] for key in expected):
            raise ValueError("execution manifest identity/order drifted")


def _verify_provider(value: Any) -> dict[str, str]:
    provider = _object(value, label="provider identity")
    _exact_keys(provider, PROVIDER_KEYS, label="provider identity")
    for key in PROVIDER_KEYS:
        if not isinstance(provider[key], str) or not provider[key].strip():
            raise ValueError(f"provider {key} is missing")
    if any(token in provider["name"].casefold() for token in ("mock", "scripted", "loopback")):
        raise ValueError("provider identity is not a real release provider")
    return provider


def _verify_artifacts(value: Any, *, root: Path) -> dict[str, str]:
    artifacts = _object(value, label="artifacts")
    if not artifacts:
        raise ValueError("artifact hash map must not be empty")
    verified: dict[str, str] = {}
    for ref, raw_hash in artifacts.items():
        if not isinstance(ref, str):
            raise ValueError("artifact reference must be a string")
        expected = _sha256(raw_hash, label=f"artifact hash for {ref}")
        path = _artifact_path(root, ref)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"artifact is missing or is a symlink: {ref}")
        if _sha256_file(path) != expected:
            raise ValueError(f"artifact hash mismatch: {ref}")
        verified[ref] = expected
    return verified


def _required_ref(value: Any, artifacts: dict[str, str]) -> str:
    if not isinstance(value, str) or value not in artifacts:
        raise ValueError(f"required artifact is missing from hash map: {value!r}")
    return value


def _artifact_path(root: Path, ref: str) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref or "\x00" in ref:
        raise ValueError("artifact reference must be a canonical POSIX-relative path")
    relative = PurePosixPath(ref)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"unsafe artifact reference: {ref}")
    try:
        path = (root / Path(*relative.parts)).resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"artifact is missing: {ref}") from exc
    if not path.is_relative_to(root):
        raise ValueError(f"artifact reference escapes report root: {ref}")
    return path


def _verify_line_evidence_ref(value: Any, *, trace_path: Path) -> None:
    if not isinstance(value, str) or not value.startswith("trace.jsonl#sha256="):
        raise ValueError("terminal evidence must reference a hashed action trace row")
    digest = _sha256(value.removeprefix("trace.jsonl#sha256="), label="terminal evidence hash")
    line_hashes = {
        hashlib.sha256(line.encode("utf-8")).hexdigest()
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line
    }
    if digest not in line_hashes:
        raise ValueError("terminal evidence hash is absent from action trace")


def _read_jsonl(path: Path, *, label: str, allow_empty: bool) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label} must contain JSON objects")
    if not allow_empty and not rows:
        raise ValueError(f"{label} is empty")
    return rows


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    return _object(value, label=label)


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ValueError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _commit(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a full lowercase Git commit")
    return value


def _nonnegative_int(value: Any, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", choices=("migration", "release"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expect-selected", type=int)
    parser.add_argument("--expect-attempted", type=int)
    parser.add_argument("--expect-eligible", type=int)
    parser.add_argument("--expect-success", type=int)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = verify_alfworld_release(
            args.report,
            manifest_path=args.manifest,
            gate=args.gate,
            expected_sha=args.expected_sha,
            expect_selected=args.expect_selected,
            expect_attempted=args.expect_attempted,
            expect_eligible=args.expect_eligible,
            expect_success=args.expect_success,
        )
    except Exception as exc:
        print(f"ALFWorld release verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
