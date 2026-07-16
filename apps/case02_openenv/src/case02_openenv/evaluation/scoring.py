"""Freeze trajectory, result and combined scores without self-recomputation."""

from __future__ import annotations

from typing import Any

import yaml

from case02_openenv.artifacts import atomic_write_json
from case02_openenv.episode_store import EpisodeStore
from case02_openenv.evaluation.matcher import match_trajectory
from case02_openenv.evaluation.results import evaluate_results
from case02_openenv.evaluation.trajectory import normalize_events, write_jsonl

CORE_ARTIFACTS = (
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
)
VIDEO_ARTIFACTS = (
    "video/demo.mp4",
    "video/poster.png",
    "video/video_manifest.json",
)


def finalize_run(
    store: EpisodeStore, run_id: str, *, video_verified: bool = False
) -> dict[str, Any]:
    episode = store.episode(run_id)
    events = store.audit(run_id)
    effective, rejected = normalize_events(events)
    write_jsonl(episode.run_root / "trajectory/effective_trajectory.jsonl", effective)
    atomic_write_json(episode.run_root / "trajectory/rejected_actions.json", rejected)
    dag = yaml.safe_load((store.data_root / "agent_trajectory_ground_truth.yaml").read_text())
    match = match_trajectory(dag, episode.scenario_id, effective)
    atomic_write_json(episode.run_root / "trajectory/trajectory_match.json", match)
    result = evaluate_results(store, run_id)
    atomic_write_json(episode.run_root / "environment/evaluator_inputs.json", result)
    trajectory_score = 100.0 * match["matched_node_count"] / match["required_node_count"]
    result_score = 100.0 * result["passed_count"] / result["required_count"]
    trajectory_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "score": trajectory_score,
        "matched": match["matched_node_count"],
        "required": match["required_node_count"],
        "nodes": match["nodes"],
        "safety_violations": match["safety_violations"],
    }
    result_payload = {
        "schema_version": 1,
        "run_id": run_id,
        "score": result_score,
        "passed": result["passed_count"],
        "required": result["required_count"],
        "checkpoints": result["checkpoints"],
    }
    atomic_write_json(episode.run_root / "scores/trajectory_score.json", trajectory_payload)
    atomic_write_json(episode.run_root / "scores/result_score.json", result_payload)
    failures: dict[str, bool] = {
        "safety_failure": bool(match["safety_violations"]),
        "environment_failure": False,
        "artifact_failure": False,
    }
    summary = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario_id": episode.scenario_id,
        "trajectory_score": trajectory_score,
        "result_score": result_score,
        "overall_score": (trajectory_score + result_score) / 2,
        "required_nodes": match["required_node_count"],
        "matched_nodes": match["matched_node_count"],
        "required_checkpoints": result["required_count"],
        "passed_checkpoints": result["passed_count"],
        **failures,
        "video_verification": "passed" if video_verified else "pending",
        "formal_success": (
            bool(trajectory_score == 100.0 and result_score == 100.0 and not any(failures.values()))
            if video_verified
            else None
        ),
    }
    atomic_write_json(episode.run_root / "scores/summary.json", summary)
    for relative in (
        "environment/audit_events.jsonl",
        "environment/state_snapshots.jsonl",
        "environment/evaluator_inputs.json",
        "trajectory/raw_actions.jsonl",
        "trajectory/effective_trajectory.jsonl",
        "trajectory/trajectory_match.json",
        "scores/trajectory_score.json",
        "scores/result_score.json",
        "scores/summary.json",
    ):
        path = episode.run_root / relative
        if path.is_file():
            episode.registry.register(relative, producer="evaluator")
    required_artifacts = CORE_ARTIFACTS + (VIDEO_ARTIFACTS if video_verified else ())
    artifact_failures = episode.registry.verify(required_paths=required_artifacts)
    summary["artifact_failure"] = bool(artifact_failures)
    summary["artifact_failures"] = artifact_failures
    if video_verified:
        summary["formal_success"] = bool(
            trajectory_score == 100.0
            and result_score == 100.0
            and not summary["safety_failure"]
            and not summary["environment_failure"]
            and not summary["artifact_failure"]
        )
    atomic_write_json(episode.run_root / "scores/summary.json", summary)
    episode.registry.register("scores/summary.json", producer="evaluator")
    return {"success": True, "summary": summary}


def publish_video_verification(
    store: EpisodeStore, run_id: str, video_manifest: dict[str, Any]
) -> dict[str, Any]:
    episode = store.episode(run_id)
    result = finalize_run(store, run_id, video_verified=True)
    summary = result["summary"]
    summary["video_manifest_sha256"] = video_manifest.get("sha256")
    atomic_write_json(episode.run_root / "scores/summary.json", summary)
    episode.registry.register("scores/summary.json", producer="evaluator")
    return summary
