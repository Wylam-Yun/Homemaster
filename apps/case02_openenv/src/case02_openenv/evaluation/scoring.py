"""Freeze trajectory, result and combined scores without self-recomputation."""

from __future__ import annotations

from typing import Any

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
    "presentation/events.jsonl",
    "presentation/snapshot.json",
    "presentation/verification.json",
)
VIDEO_ARTIFACTS = (
    "video/demo.mp4",
    "video/poster.png",
    "video/video_manifest.json",
)


def formal_success(
    *,
    trajectory_score: float,
    result_score: float,
    safety_failure: bool,
    environment_failure: bool,
    artifact_failure: bool,
    presentation_failure: bool,
) -> bool:
    return bool(
        trajectory_score == 100.0
        and result_score == 100.0
        and not safety_failure
        and not environment_failure
        and not artifact_failure
        and not presentation_failure
    )


def finalize_run(
    store: EpisodeStore,
    run_id: str,
    *,
    video_verified: bool = False,
    observer_was_alive: bool | None = None,
) -> dict[str, Any]:
    if video_verified and observer_was_alive is None:
        raise ValueError("observer_was_alive is required for video-verified finalization")
    episode = store.episode(run_id)
    store.verify_locked_sources(run_id)
    presentation_report = store.verify_presentation(
        run_id,
        observer_was_alive=(
            observer_was_alive if observer_was_alive is not None else True
        ),
    )
    events = store.audit(run_id)
    effective, rejected = normalize_events(events)
    write_jsonl(episode.run_root / "trajectory/effective_trajectory.jsonl", effective)
    atomic_write_json(episode.run_root / "trajectory/rejected_actions.json", rejected)
    match = match_trajectory(episode.trajectory_dag, episode.scenario_id, effective)
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
        "presentation_failure": not presentation_report["passed"],
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
        "presentation_failures": presentation_report["failures"],
        "video_verification": "passed" if video_verified else "pending",
        "formal_success": (
            formal_success(
                trajectory_score=trajectory_score,
                result_score=result_score,
                **failures,
            )
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
    for relative in (
        "presentation/events.jsonl",
        "presentation/snapshot.json",
        "presentation/verification.json",
    ):
        if (episode.run_root / relative).is_file():
            episode.registry.register(relative, producer="presentation")
    required_artifacts = CORE_ARTIFACTS + (VIDEO_ARTIFACTS if video_verified else ())
    artifact_failures = episode.registry.verify(required_paths=required_artifacts)
    summary["artifact_failure"] = bool(artifact_failures)
    summary["artifact_failures"] = artifact_failures
    if video_verified:
        summary["formal_success"] = formal_success(
            trajectory_score=trajectory_score,
            result_score=result_score,
            safety_failure=summary["safety_failure"],
            environment_failure=summary["environment_failure"],
            artifact_failure=summary["artifact_failure"],
            presentation_failure=summary["presentation_failure"],
        )
    atomic_write_json(episode.run_root / "scores/summary.json", summary)
    episode.registry.register("scores/summary.json", producer="evaluator")
    return {"success": True, "summary": summary}


def publish_video_verification(
    store: EpisodeStore,
    run_id: str,
    video_manifest: dict[str, Any],
    *,
    observer_was_alive: bool,
) -> dict[str, Any]:
    episode = store.episode(run_id)
    result = finalize_run(
        store,
        run_id,
        video_verified=True,
        observer_was_alive=observer_was_alive,
    )
    summary = result["summary"]
    summary["video_manifest_sha256"] = video_manifest.get("sha256")
    atomic_write_json(episode.run_root / "scores/summary.json", summary)
    episode.registry.register("scores/summary.json", producer="evaluator")
    return summary
