"""Run a two-subtask THOR taskset and persist per-subtask black-box evidence."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from homemaster.alfworld.benchmark.adapter import (
    AlfworldEnvAdapter,
    _event_logical_scene,
    _external_event_read,
    build_alfworld_batch_env_with_first_trial,
)
from homemaster.alfworld.trial_selection import (
    build_trial_selection_entry,
    trial_logical_scene,
)
from homemaster.alfworld.types import AlfworldBenchmarkConfig, AlfworldStepResult

TASKSET_ID = "easy_living_room_219"
TRIAL_IDS = (
    "valid_unseen/look_at_obj_in_light-RemoteControl-None-FloorLamp-219/"
    "trial_T20190909_032721_511027/traj_data.json",
    "valid_unseen/look_at_obj_in_light-CellPhone-None-FloorLamp-219/"
    "trial_T20190908_044113_026049/traj_data.json",
)


def _external_state(adapter: AlfworldEnvAdapter) -> dict[str, Any]:
    thor_env = adapter._resolve_thor_env()
    event = getattr(thor_env, "last_event", None)
    read = _external_event_read(
        event,
        event_sequence=adapter.event_sequence,
        thor_env=thor_env,
    )
    metadata = getattr(event, "metadata", {}) or {}
    return {
        "status": read.status,
        "world_sha256": read.world_sha256,
        "visibility_sha256": read.visibility_sha256,
        "frame_sha256": read.frame_sha256,
        "logical_scene": _event_logical_scene(event),
        "last_action": metadata.get("lastAction"),
        "last_action_success": metadata.get("lastActionSuccess"),
        "event_sequence": adapter.event_sequence,
    }


def _state(adapter: AlfworldEnvAdapter) -> dict[str, Any]:
    return adapter.current_state.to_debug_dict()


def _thor_processes() -> dict[int, str]:
    result = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        check=False,
    )
    processes: dict[int, str] = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2 or "thor-" not in parts[1].lower():
            continue
        try:
            processes[int(parts[0])] = parts[1]
        except ValueError:
            continue
    return processes


def _run_action(
    adapter: AlfworldEnvAdapter,
    *,
    label: str,
    action: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    before_state = _state(adapter)
    before = _external_state(adapter)
    if action == "navigate":
        result = adapter.go_to_target(
            label,
            tool_name="robot_go_to",
            tool_args={"target": label, **arguments},
        )
    else:
        result = adapter.manipulate_with_thor(
            action=action,
            tool_name="robot_manipulate",
            tool_args=arguments,
        )
    if not isinstance(result, AlfworldStepResult):
        raise TypeError("ALFWorld adapter returned an unexpected step type")
    after = _external_state(adapter)
    return {
        "label": label,
        "action": action,
        "request": arguments,
        "success": result.success,
        "failure_reason": result.failure_reason,
        "backend_action_count": result.backend_action_count,
        "external_return_code": 0 if result.success else 1,
        "external_status": after["status"],
        "before_external_state": before,
        "after_external_state": after,
        "before_state": before_state,
        "after_state": _state(adapter),
        "trace_events": list(result.trace_events),
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    asset_root = Path(
        os.environ.get(
            "HOMEMASTER_LIVE_ALFWORLD_ROOT",
            "/home/haodong2/weilin/red_bird/alfworld",
        )
    )
    output_root = Path(
        os.environ.get(
            "HOMEMASTER_PHASE5_EVIDENCE_ROOT",
            "plan/V3.5/evidence/phase-5",
        )
    ).resolve() / f"taskset-{TASKSET_ID}"
    trial_root = asset_root / "data" / "json_2.1.1"
    config = AlfworldBenchmarkConfig(
        alfworld_root=asset_root,
        alfworld_config=asset_root / "configs" / "base_config.yaml",
        data_root=asset_root / "data",
        trace_root=output_root / "trace",
        split="valid_unseen",
        episodes=1,
        seed=42,
    )
    trial_data: list[dict[str, Any]] = []
    selections = []
    trial_paths = [trial_root / trial_id for trial_id in TRIAL_IDS]
    for path in trial_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        trial_data.append(data)
        selections.append(
            build_trial_selection_entry(
                path,
                trial_root=trial_root,
                expected_logical_scene=trial_logical_scene(data),
                identity_status="taskset_declared",
            )
        )

    thor_processes_before = _thor_processes()
    env = build_alfworld_batch_env_with_first_trial(config, first_trial_path=trial_paths[0])
    thor_processes_after_start = _thor_processes()
    started_thor_processes = {
        pid: command
        for pid, command in thor_processes_after_start.items()
        if pid not in thor_processes_before
    }
    adapter = AlfworldEnvAdapter(
        env=env,
        episode_prefix=f"valid_unseen/{TASKSET_ID}",
        seed=42,
        frame_dir=output_root / "subtask-01" / "frames",
        allow_offscreen_object_navigation=False,
    )
    close_result: Any = None
    try:
        reset = adapter.reset(selection_entry=selections[0])
        if not reset.ready or reset.state is None:
            raise RuntimeError(f"taskset reset failed: {reset.setup_failure}")
        reset_external = _external_state(adapter)
        first_actions = [
            _run_action(adapter, label="RemoteControl", action="navigate", arguments={}),
            _run_action(
                adapter,
                label="RemoteControl",
                action="take",
                arguments={"object": "RemoteControl"},
            ),
            _run_action(adapter, label="FloorLamp", action="navigate", arguments={}),
            _run_action(
                adapter,
                label="FloorLamp",
                action="use",
                arguments={"object": "FloorLamp"},
            ),
        ]
        first_state = _state(adapter)
        _write(
            output_root / "subtask-01" / "episode.json",
            {
                "schema": "homemaster-v35-phase5-taskset-subtask-v1",
                "taskset_id": TASKSET_ID,
                "subtask_index": 1,
                "trial_id": selections[0].trial_id,
                "trial_sha256": selections[0].trial_sha256,
                "expected_logical_scene": selections[0].expected_logical_scene,
                "goal_identity": selections[0].goal_identity,
                "scene_generation": reset.scene_generation,
                "goal_generation": reset.goal_generation,
                "scene_reset_fingerprint": reset.scene_reset_fingerprint,
                "snapshot_sha256": reset.snapshot_sha256,
                "reset": {"ready": reset.ready, "external_return_code": 0},
                "initial_state": reset.state.to_debug_dict(),
                "initial_external_state": reset_external,
                "actions": first_actions,
                "terminal": {
                    "won": first_state["won"],
                    "done": first_state["done"],
                    "goal_condition_success_rate": first_state["goal_condition_success_rate"],
                    "classification": "agent_success" if first_state["won"] else "agent_model_failure",
                },
            },
        )

        adapter.set_frame_dir(output_root / "subtask-02" / "frames")
        advance = adapter.advance_goal(
            trial_data[1],
            subtask_label=f"{TASKSET_ID}-subtask-02",
            selection_entry=selections[1],
        )
        if not advance.ready or advance.state is None:
            raise RuntimeError(f"taskset goal advance failed: {advance.advance_failure}")
        second_actions = [
            _run_action(adapter, label="CellPhone", action="navigate", arguments={}),
            _run_action(
                adapter,
                label="CellPhone",
                action="take",
                arguments={"object": "CellPhone"},
            ),
            _run_action(adapter, label="FloorLamp", action="navigate", arguments={}),
            _run_action(
                adapter,
                label="FloorLamp",
                action="use",
                arguments={"object": "FloorLamp"},
            ),
        ]
        second_state = _state(adapter)
        second_classification = "agent_success" if second_state["won"] else "agent_model_failure"
        _write(
            output_root / "subtask-02" / "episode.json",
            {
                "schema": "homemaster-v35-phase5-taskset-subtask-v1",
                "taskset_id": TASKSET_ID,
                "subtask_index": 2,
                "trial_id": selections[1].trial_id,
                "trial_sha256": selections[1].trial_sha256,
                "expected_logical_scene": selections[1].expected_logical_scene,
                "goal_identity": selections[1].goal_identity,
                "scene_generation": advance.scene_generation,
                "goal_generation": advance.goal_generation,
                "scene_reset_fingerprint": advance.scene_reset_fingerprint,
                "snapshot_sha256": advance.snapshot_sha256,
                "goal_advance": {
                    "ready": advance.ready,
                    "benchmark_control_action_count": advance.benchmark_control_action_count,
                    "external_return_code": 0,
                    "before_scene_state_sha256": advance.before_scene_state_sha256,
                    "after_scene_state_sha256": advance.after_scene_state_sha256,
                    "scene_unchanged": advance.before_scene_state_sha256 == advance.after_scene_state_sha256,
                    "classification": advance.classification,
                },
                "initial_state": advance.state.to_debug_dict(),
                "actions": second_actions,
                "terminal": {
                    "won": second_state["won"],
                    "done": second_state["done"],
                    "goal_condition_success_rate": second_state["goal_condition_success_rate"],
                    "classification": second_classification,
                },
            },
        )
    finally:
        close_result = adapter.close()
        thor_processes_after_close = _thor_processes()
        remaining_thor_processes = {
            pid: command
            for pid, command in thor_processes_after_close.items()
            if pid in started_thor_processes
        }
        cleanup = {
            "status": close_result.status,
            "evidence_ref": close_result.evidence_ref,
            "external_return_code": 0 if close_result.status == "succeeded" else 1,
            "started_thor_processes": started_thor_processes,
            "remaining_thor_processes": remaining_thor_processes,
            "process_alive_after_close": (
                bool(remaining_thor_processes) if started_thor_processes else None
            ),
        }
        _write(output_root / "cleanup.json", cleanup)
        _write(
            output_root / "taskset.json",
            {
                "schema": "homemaster-v35-phase5-taskset-v1",
                "taskset_id": TASKSET_ID,
                "subtasks": [
                    {"index": 1, "evidence_ref": "subtask-01/episode.json"},
                    {"index": 2, "evidence_ref": "subtask-02/episode.json"},
                ],
                "cleanup": cleanup,
            },
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
