"""Evaluate the historical 16 checkpoints from external run state."""

from __future__ import annotations

from typing import Any

from case02_openenv.episode_store import EpisodeStore
from case02_openenv.models import JobStatus

CHECKPOINTS = (
    "pre_alarm",
    "pre_probe",
    "pre_capacity",
    "pre_runtime_metrics",
    "pre_traffic",
    "pre_extend_config_confirm_unmatched",
    "implement_auto_platform_access",
    "implement_grep_config",
    "post_alarm",
    "post_probe",
    "post_capacity",
    "post_runtime_metrics",
    "post_traffic",
    "verify_auto_platform_access",
    "rollback_auto_platform_access",
    "rollback_grep_config_unmatched",
)


def evaluate_results(store: EpisodeStore, run_id: str) -> dict[str, Any]:
    episode = store.episode(run_id)
    state = store.state(run_id)
    scenario = episode.scenario_id
    add_succeeded = _job_succeeded(state, "add")
    remove_succeeded = _job_succeeded(state, "remove")
    values: dict[str, bool] = {
        "pre_alarm": state.prechecks.get("alarm") == "clear",
        "pre_probe": state.prechecks.get("probe") == "normal",
        "pre_capacity": state.prechecks.get("capacity") == "sufficient",
        "pre_runtime_metrics": state.prechecks.get("runtime_metrics") == "normal",
        "pre_traffic": state.prechecks.get("traffic") == "normal",
        "pre_extend_config_confirm_unmatched": all(
            state.config_checks.get(key) for key in ("extension_config", "upstream_ready")
        ),
        "implement_auto_platform_access": add_succeeded,
        "implement_grep_config": bool(state.add_grep_evidence_id),
        "post_alarm": (
            state.postchecks.get("alarm") == "clear"
            if scenario == "normal"
            else state.postchecks.get("alarm") == "active" and state.causal_anomaly_armed
        ),
        "post_probe": state.postchecks.get("probe") == "normal",
        "post_capacity": state.postchecks.get("capacity") == "sufficient",
        "post_runtime_metrics": state.postchecks.get("runtime_metrics") == "normal",
        "post_traffic": state.postchecks.get("traffic") == "normal",
        "verify_auto_platform_access": state.business_verified,
        "rollback_auto_platform_access": remove_succeeded
        and not store.config_contains_target(run_id),
        "rollback_grep_config_unmatched": bool(state.rollback_grep_evidence_id)
        and not store.config_contains_target(run_id),
    }
    if scenario == "normal":
        required = set(CHECKPOINTS[:-2])
        optional: set[str] = set()
    else:
        required = set(CHECKPOINTS[:9]) | set(CHECKPOINTS[-2:])
        optional = set(CHECKPOINTS[9:14])
    results = []
    for case_id in CHECKPOINTS:
        if case_id in required:
            verdict = "pass" if values[case_id] else "fail"
            requirement = "required"
        elif case_id in optional:
            verdict = "pass" if values[case_id] else "not_applicable"
            requirement = "optional_before_rollback"
        else:
            verdict = "not_applicable"
            requirement = "not_applicable"
        results.append(
            {
                "case_id": case_id,
                "requirement": requirement,
                "verdict": verdict,
                "external_state_satisfied": values[case_id],
            }
        )
    passed = sum(
        1 for item in results if item["requirement"] == "required" and item["verdict"] == "pass"
    )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "scenario_id": scenario,
        "required_count": len(required),
        "passed_count": passed,
        "checkpoints": results,
    }


def _job_succeeded(state: Any, operation: str) -> bool:
    return any(
        job.operation == operation
        and job.status == JobStatus.SUCCEEDED
        and job.business_return_code == 0
        for job in state.jobs.values()
    )
