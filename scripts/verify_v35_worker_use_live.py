#!/usr/bin/env python3
"""Black-box Oracle-grounded take/use check for the isolated THOR worker."""

from __future__ import annotations

import json
import os
from pathlib import Path

from homemaster.alfworld.benchmark.worker_adapter import WorkerAlfworldAdapter
from homemaster.alfworld.trial_selection import build_trial_selection_entry
from homemaster.alfworld.types import AlfworldBenchmarkConfig

DEFAULT_TRIAL = (
    "valid_seen/look_at_obj_in_light-AlarmClock-None-DeskLamp-323/"
    "trial_T20190909_044715_250790/traj_data.json"
)


def main() -> int:
    repo = Path(os.environ.get("HOMEMASTER_LIVE_REPO", Path.cwd())).resolve()
    asset_root = Path(
        os.environ.get("HOMEMASTER_LIVE_ALFWORLD_ROOT", str(repo / ".runtime" / "alfworld"))
    ).resolve()
    trial_root = asset_root / "data" / "json_2.1.1"
    trial_path = trial_root / os.environ.get("HOMEMASTER_USE_TRIAL", DEFAULT_TRIAL)
    selection = build_trial_selection_entry(
        trial_path,
        trial_root=trial_root,
        expected_logical_scene="FloorPlan323",
        identity_status="live_worker_use_check",
    )
    evidence_root = Path(
        os.environ.get(
            "HOMEMASTER_PHASE5_EVIDENCE_ROOT",
            str(repo / "plan" / "V3.5" / "evidence" / "phase-5"),
        )
    ).resolve() / "live-worker-use-20260926"
    evidence_root.mkdir(parents=True, exist_ok=True)
    manifest = evidence_root / "worker-trial-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "alfworld-trial-selection-v1",
                "entries": [
                    {
                        "trial_id": selection.trial_id,
                        "trial_sha256": selection.trial_sha256,
                        "expected_logical_scene": selection.expected_logical_scene,
                        "goal_identity": selection.goal_identity,
                        "goal_fingerprint": selection.goal_fingerprint,
                        "identity_status": selection.identity_status,
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    config = AlfworldBenchmarkConfig(
        alfworld_root=asset_root,
        alfworld_config=asset_root / "configs" / "base_config.yaml",
        trace_root=evidence_root,
        data_root=asset_root / "data",
        env_type="AlfredThorEnv",
        split="valid_seen",
        episodes=1,
        provider_config=None,
        provider_name=None,
        run_id="v35-live-worker-use-20260926",
        use_isolated_worker=True,
        allow_offscreen_object_navigation=True,
    )
    adapter = WorkerAlfworldAdapter.start(
        config=config,
        frame_dir=evidence_root / "frames",
        log_path=evidence_root / "worker.stderr.log",
        manifest_path=manifest,
    )
    results: list[dict[str, object]] = []
    close_result = None
    try:
        reset = adapter.reset(selection_entry=selection)
        results.append({"name": "reset", "ready": reset.ready, "state": adapter.client.raw_state})
        for name, result in (
            (
                "go_to_dresser",
                adapter.go_to_target(
                    "dresser", tool_name="robot_go_to", tool_args={"target": "dresser"}
                ),
            ),
            (
                "take_alarmclock",
                adapter.manipulate_with_thor(
                    action="take",
                    tool_name="robot_manipulate",
                    tool_args={
                        "action": "take",
                        "object": "alarmclock",
                        "source_receptacle": "dresser",
                    },
                ),
            ),
            (
                "go_to_desklamp",
                adapter.go_to_target(
                    "desklamp", tool_name="robot_go_to", tool_args={"target": "desklamp"}
                ),
            ),
            (
                "use_desklamp",
                adapter.manipulate_with_thor(
                    action="use",
                    tool_name="robot_manipulate",
                    tool_args={"action": "use", "object": "desklamp"},
                ),
            ),
        ):
            results.append(
                {
                    "name": name,
                    "success": result.success,
                    "backend_action_count": result.backend_action_count,
                    "feedback": result.feedback,
                    "state": adapter.client.raw_state,
                }
            )
    finally:
        close_result = adapter.close()
    raw_state = adapter.client.raw_state
    objects = raw_state.get("objects", [])
    alarmclock = next(
        (item for item in objects if isinstance(item, dict) and item.get("objectType") == "AlarmClock"),
        {},
    )
    lamps = [
        item
        for item in objects
        if isinstance(item, dict) and item.get("objectType") == "DeskLamp"
    ]
    evidence = {
        "schema": "homemaster-v35-live-worker-use-v1",
        "trial_id": selection.trial_id,
        "worker_identity": {
            "allow_offscreen_object_navigation": adapter.client._health.get(
                "allow_offscreen_object_navigation"
            )
        },
        "results": results,
        "final_state": raw_state,
        "independent_external_state": {
            "alarmclock_picked_up": alarmclock.get("isPickedUp") is True,
            "desk_lamps_toggled": [
                {"objectId": item.get("objectId"), "isToggled": item.get("isToggled")}
                for item in lamps
            ],
        },
        "request_history": list(adapter.client.request_history),
        "close": {
            "status": close_result.status,
            "evidence_ref": close_result.evidence_ref,
            "worker_exit_code": adapter.client.worker_exit_code,
        },
        "stderr": (evidence_root / "worker.stderr.log").read_text(
            encoding="utf-8", errors="replace"
        ),
    }
    (evidence_root / "worker-use.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    successful_actions = all(item.get("success") is True for item in results[1:])
    toggled = any(item.get("isToggled") is True for item in lamps)
    return int(
        not (
            results[0].get("ready") is True
            and successful_actions
            and alarmclock.get("isPickedUp") is True
            and toggled
            and close_result.status == "succeeded"
            and adapter.client.worker_exit_code is not None
            and "Traceback (most recent call last)" not in evidence["stderr"]
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
