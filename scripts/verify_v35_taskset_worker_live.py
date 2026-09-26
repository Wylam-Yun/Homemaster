#!/usr/bin/env python3
"""Black-box reset/set_task/action/close check for the isolated taskset worker."""

from __future__ import annotations

import json
import os
from pathlib import Path

from homemaster.alfworld.benchmark.worker_adapter import WorkerAlfworldAdapter
from homemaster.alfworld.taskset_loader import load_taskset_config
from homemaster.alfworld.trial_selection import (
    build_trial_selection_entry,
    load_verified_trial_data,
)
from homemaster.alfworld.types import AlfworldBenchmarkConfig


def _manifest(path: Path, selections: tuple[object, ...]) -> Path:
    entries = []
    for entry in selections:
        entries.append(
            {
                "trial_id": entry.trial_id,
                "trial_sha256": entry.trial_sha256,
                "expected_logical_scene": entry.expected_logical_scene,
                "goal_identity": entry.goal_identity,
                "goal_fingerprint": entry.goal_fingerprint,
                "identity_status": entry.identity_status,
            }
        )
    path.write_text(
        json.dumps({"schema_version": "alfworld-trial-selection-v1", "entries": entries}, indent=2),
        encoding="utf-8",
    )
    return path


def _reset_evidence(result: object) -> dict[str, object]:
    return {
        "ready": result.ready,
        "scene_generation": result.scene_generation,
        "goal_generation": result.goal_generation,
        "scene_reset_fingerprint": result.scene_reset_fingerprint,
        "goal_trial_fingerprint": result.goal_trial_fingerprint,
        "snapshot_sha256": result.snapshot_sha256,
        "snapshot_ref": result.snapshot_ref,
        "classification": result.classification,
        "score_eligible": result.score_eligible,
        "setup_backend_action_count": result.setup_backend_action_count,
        "recovery_status": result.recovery_status,
        "cleanup_status": result.cleanup_status,
        "quarantine_required": result.quarantine_required,
        "environment_disposition": result.environment_disposition,
        "evidence_ref": result.evidence_ref,
        "state": result.state.to_debug_dict() if result.state is not None else None,
    }


def _advance_evidence(result: object) -> dict[str, object]:
    return {
        "ready": result.ready,
        "scene_generation": result.scene_generation,
        "goal_generation": result.goal_generation,
        "scene_reset_fingerprint": result.scene_reset_fingerprint,
        "goal_trial_fingerprint": result.goal_trial_fingerprint,
        "snapshot_sha256": result.snapshot_sha256,
        "before_scene_state_sha256": result.before_scene_state_sha256,
        "after_scene_state_sha256": result.after_scene_state_sha256,
        "advance_trigger": result.advance_trigger,
        "advance_failure": result.advance_failure,
        "classification": result.classification,
        "score_eligible": result.score_eligible,
        "benchmark_control_action_count": result.benchmark_control_action_count,
        "cleanup_status": result.cleanup_status,
        "quarantine_required": result.quarantine_required,
        "environment_disposition": result.environment_disposition,
        "evidence_ref": result.evidence_ref,
        "state": result.state.to_debug_dict() if result.state is not None else None,
    }


def main() -> int:
    repo = Path(os.environ.get("HOMEMASTER_LIVE_REPO", Path.cwd())).resolve()
    asset_root = Path(
        os.environ.get("HOMEMASTER_LIVE_ALFWORLD_ROOT", str(repo / ".runtime" / "alfworld"))
    ).resolve()
    evidence_root = Path(
        os.environ.get(
            "HOMEMASTER_PHASE5_EVIDENCE_ROOT",
            str(repo / "plan" / "V3.5" / "evidence" / "phase-5"),
        )
    ).resolve()
    taskset_path = Path(
        os.environ.get(
            "HOMEMASTER_TASKSET_CONFIG",
            str(repo / "src" / "homemaster" / "alfworld" / "alfworld_tasksets.yaml"),
        )
    ).resolve()
    taskset_config = load_taskset_config(
        taskset_path,
        alfworld_root=asset_root,
        alfworld_config=asset_root / "configs" / "base_config.yaml",
    )
    taskset_id = os.environ.get("HOMEMASTER_TASKSET_ID", "easy_living_room_219")
    taskset = next(item for item in taskset_config.tasksets if item.id == taskset_id)
    trial_root = asset_root / "data" / "json_2.1.1"
    inputs = []
    expected_scene = f"FloorPlan{taskset.floorplan}"
    for subtask in taskset.subtasks[:2]:
        assert subtask.traj_path is not None
        selection = build_trial_selection_entry(
            subtask.traj_path,
            trial_root=trial_root,
            expected_logical_scene=expected_scene,
            identity_status="taskset_declared",
        )
        inputs.append((selection, load_verified_trial_data(selection, trial_root=trial_root)))
    selections = tuple(item[0] for item in inputs)
    output = evidence_root / f"taskset-worker-{taskset.id}"
    output.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(output / "worker-trial-manifest.json", selections)
    config = AlfworldBenchmarkConfig(
        alfworld_root=asset_root,
        alfworld_config=asset_root / "configs" / "base_config.yaml",
        trace_root=output,
        data_root=asset_root / "data",
        env_type="AlfredThorEnv",
        split=taskset_config.split,
        episodes=1,
        provider_config=None,
        provider_name=None,
        run_id=f"taskset-worker-{taskset.id}",
        use_isolated_worker=True,
    )
    adapter = WorkerAlfworldAdapter.start(
        config=config,
        frame_dir=output / "subtask-01" / "frames",
        log_path=output / "worker.stderr.log",
        manifest_path=manifest,
    )
    close_receipt = None
    try:
        adapter.set_frame_dir(output / "subtask-01" / "frames")
        reset = adapter.reset(selection_entry=selections[0])
        first_state = adapter.current_state.to_debug_dict()
        adapter.set_frame_dir(output / "subtask-02" / "frames")
        advance = adapter.advance_goal(
            inputs[1][1],
            subtask_label=f"{taskset.id}-subtask-02",
            selection_entry=selections[1],
        )
        second_state = adapter.current_state.to_debug_dict() if advance.ready else None
        action = None
        if advance.ready:
            action_result = adapter.go_to_target(
                "FloorLamp",
                tool_name="robot_go_to",
                tool_args={"target": "FloorLamp"},
            )
            action = {
                "success": action_result.success,
                "external_state": adapter.client.raw_state,
                "feedback": action_result.execution_feedback.to_model_payload(),
            }
    finally:
        close_receipt = adapter.close()
    stderr = (output / "worker.stderr.log").read_text(encoding="utf-8", errors="replace")
    evidence = {
        "schema": "homemaster-v35-phase5-taskset-worker-v1",
        "taskset_id": taskset.id,
        "subtask_count_checked": 2,
        "manifest": str(manifest),
        "reset": _reset_evidence(reset),
        "first_state": first_state,
        "advance": _advance_evidence(advance),
        "second_state": second_state,
        "action": action,
        "request_history": list(adapter.client.request_history),
        "close": {
            "cleanup_status": close_receipt.status,
            "evidence_ref": close_receipt.evidence_ref,
            "request": next(
                (item for item in reversed(adapter.client.request_history) if item["operation"] == "close"),
                None,
            ),
        },
        "worker_pid": adapter.client.worker_pid,
        "worker_exit_code": adapter.client._process.returncode,
        "stderr": stderr,
        "cleanup": {"worker_exited": adapter.client._process.poll() is not None},
    }
    (output / "taskset-worker.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    close_request = evidence["close"]["request"]
    if not reset.ready or not advance.ready:
        return 1
    if (
        close_receipt.status != "succeeded"
        or not isinstance(close_request, dict)
        or close_request.get("external_return_code") != 0
        or adapter.client._process.poll() is None
    ):
        return 1
    if "Traceback (most recent call last)" in stderr:
        return 1
    if advance.before_scene_state_sha256 != advance.after_scene_state_sha256:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
