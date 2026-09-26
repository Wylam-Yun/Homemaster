"""Runner adapter backed by the isolated ALFWorld worker."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from homemaster.alfworld import AlfworldHarness
from homemaster.alfworld.gateway import CleanupResult
from homemaster.alfworld.trial_selection import (
    TrialSelectionEntry,
    trial_goal_identity,
    trial_logical_scene,
)
from homemaster.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldEnvState,
    AlfworldGoalAdvanceResult,
    AlfworldResetResult,
    AlfworldStepResult,
)
from homemaster.alfworld.worker_client import AlfworldWorkerClient, WorkerThorBackend


class WorkerAlfworldAdapter:
    """Expose the runner adapter seam without importing ALFWorld in-process."""

    def __init__(self, client: AlfworldWorkerClient) -> None:
        self.client = client
        self.harness = AlfworldHarness(WorkerThorBackend(client))
        self._selection: TrialSelectionEntry | None = None
        self._snapshot_sha256: str | None = None
        self._snapshot_ref: str | None = None
        self._frame_dir = client._frame_dir

    @classmethod
    def start(
        cls,
        *,
        config: AlfworldBenchmarkConfig,
        frame_dir: Path,
        log_path: Path,
        manifest_path: Path,
    ) -> WorkerAlfworldAdapter:
        binding_candidates = (
            config.alfworld_root.parent / "alfworld-binding.json",
            Path.cwd() / ".runtime" / "alfworld-binding.json",
            Path(__file__).resolve().parents[4] / ".runtime" / "alfworld-binding.json",
        )
        binding_path = next((path for path in binding_candidates if path.is_file()), None)
        if binding_path is None:
            raise FileNotFoundError(
                "ALFWorld worker binding is missing; run scripts/setup-alfworld.sh"
            )
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        asset_root = Path(str(binding["root"]))
        data_root = Path(str(binding.get("data_root") or asset_root / "data"))
        client = AlfworldWorkerClient.start(
            python_executable=Path(str(binding["python"])),
            asset_root=asset_root,
            data_root=data_root,
            config_path=asset_root / "configs" / "base_config.yaml",
            trial_manifest=manifest_path,
            trial_index=0,
            env_type=config.env_type,
            split=config.split,
            seed=config.seed,
            allow_offscreen_object_navigation=config.allow_offscreen_object_navigation,
            display=os.environ.get("DISPLAY", ":99"),
            frame_dir=frame_dir,
            log_path=log_path,
            reset_on_start=False,
        )
        return cls(client)

    @property
    def current_state(self) -> AlfworldEnvState:
        return self.client.current_state

    @property
    def backend_id(self) -> str:
        return self.client.backend_id

    @property
    def generation(self) -> int:
        return self.client.generation

    def set_frame_dir(self, path: Path) -> None:
        self._frame_dir = path.resolve()
        self.client.set_frame_dir(self._frame_dir)

    def reset(self, *, selection_entry: TrialSelectionEntry) -> AlfworldResetResult:
        self._selection = selection_entry
        receipt = self.harness.reset(selection_entry)
        if not receipt.succeeded:
            self._close_after_reset_failure()
            return AlfworldResetResult(
                backend_kind="thor",
                ready=False,
                state=None,
                scene_generation=self.client.generation,
                goal_generation=1,
                scene_reset_fingerprint=None,
                goal_trial_fingerprint=selection_entry.goal_fingerprint,
                snapshot_sha256=None,
                snapshot_ref=None,
                setup_trigger="setup_runtime_failed",
                setup_failure="setup_runtime_failed",
                classification="runtime_failure",
                score_eligible=False,
                setup_backend_action_count=0,
                recovery_status="not_needed",
                cleanup_status="succeeded",
                quarantine_required=False,
                environment_disposition="closed",
                evidence_ref=receipt.evidence_ref,
            )
        fingerprint = hashlib.sha256(
            json.dumps(self.client.raw_state, sort_keys=True, default=str).encode()
        ).hexdigest()
        self._snapshot_sha256 = fingerprint
        self._snapshot_ref = f"worker:{self.client.worker_pid}:reset-state"
        return AlfworldResetResult(
            backend_kind="thor",
            ready=True,
            state=self.current_state,
            scene_generation=self.client.generation,
            goal_generation=1,
            scene_reset_fingerprint=fingerprint,
            goal_trial_fingerprint=selection_entry.goal_fingerprint,
            snapshot_sha256=None,
            snapshot_ref=None,
            setup_trigger=None,
            setup_failure=None,
            classification=None,
            score_eligible=True,
            setup_backend_action_count=0,
            recovery_status="not_needed",
            cleanup_status="not_applicable",
            quarantine_required=False,
            environment_disposition="ready",
            evidence_ref=receipt.evidence_ref,
        )

    def advance_goal(
        self,
        traj_data: dict[str, Any],
        *,
        subtask_label: str,
        selection_entry: TrialSelectionEntry,
    ) -> AlfworldGoalAdvanceResult:
        try:
            if trial_logical_scene(traj_data) != selection_entry.expected_logical_scene:
                raise ValueError("goal scene mismatch")
            if trial_goal_identity(traj_data) != selection_entry.goal_identity:
                raise ValueError("goal identity mismatch")
        except ValueError as exc:
            self._close_after_reset_failure()
            return self._goal_terminal(selection_entry, "goal_identity_unreadable", str(exc), 0)

        receipt = self.client.set_task(selection_entry)
        result = dict(receipt.state)
        before_sha = result.get("before_world_sha256")
        after_sha = result.get("after_world_sha256")
        if (
            not receipt.succeeded
            or not isinstance(before_sha, str)
            or not isinstance(after_sha, str)
            or before_sha != after_sha
        ):
            self._close_after_reset_failure()
            trigger = "goal_world_drift" if before_sha != after_sha else "goal_advance_rejected"
            return self._goal_terminal(
                selection_entry,
                trigger,
                receipt.error,
                1 if receipt.backend_attempted else 0,
                before_sha,
                after_sha,
            )

        episode_prefix = self._selection.trial_id if self._selection else "alfworld"
        state = replace(self.current_state, episode_id=f"{episode_prefix}/{subtask_label}")
        self.client._state = state
        self.client._raw_state["episode_id"] = state.episode_id
        self._selection = selection_entry
        return AlfworldGoalAdvanceResult(
            backend_kind="thor",
            ready=True,
            state=state,
            scene_generation=self.client.generation,
            goal_generation=self.client.goal_generation,
            scene_reset_fingerprint=self._snapshot_sha256,
            goal_trial_fingerprint=selection_entry.goal_fingerprint,
            snapshot_sha256=self._snapshot_sha256,
            before_scene_state_sha256=before_sha,
            after_scene_state_sha256=after_sha,
            advance_trigger=None,
            advance_failure=None,
            classification=None,
            score_eligible=True,
            benchmark_control_action_count=1,
            cleanup_status="not_needed",
            quarantine_required=False,
            environment_disposition="ready",
            evidence_ref=receipt.evidence_ref,
        )

    def _goal_terminal(
        self,
        selection_entry: TrialSelectionEntry,
        trigger: str,
        detail: str | None,
        action_count: int,
        before_sha: str | None = None,
        after_sha: str | None = None,
    ) -> AlfworldGoalAdvanceResult:
        classification = (
            "artifact_failure"
            if trigger in {"goal_identity_unreadable", "goal_scene_mismatch"}
            else "execution_state_uncertain"
        )
        return AlfworldGoalAdvanceResult(
            backend_kind="thor",
            ready=False,
            state=None,
            scene_generation=self.client.generation,
            goal_generation=self.client.goal_generation,
            scene_reset_fingerprint=self._snapshot_sha256,
            goal_trial_fingerprint=selection_entry.goal_fingerprint,
            snapshot_sha256=self._snapshot_sha256,
            before_scene_state_sha256=before_sha,
            after_scene_state_sha256=after_sha,
            advance_trigger=trigger,
            advance_failure=trigger,
            classification=classification,
            score_eligible=False,
            benchmark_control_action_count=action_count,
            cleanup_status="succeeded",
            quarantine_required=True,
            environment_disposition="closed",
            evidence_ref=detail or "worker:set-task",
        )

    def go_to_target(
        self, target: str, *, tool_name: str, tool_args: dict[str, Any]
    ) -> AlfworldStepResult:
        return self.client.go_to_target(target, tool_name=tool_name, tool_args=tool_args)

    def manipulate_with_thor(
        self, *, action: str, tool_name: str, tool_args: dict[str, Any]
    ) -> AlfworldStepResult:
        return self.client.manipulate_with_thor(
            action=action, tool_name=tool_name, tool_args=tool_args
        )

    def is_current_goal_satisfied(self) -> bool:
        return self.current_state.won

    def current_goal_condition_success_rate(self) -> float:
        return self.current_state.goal_condition_success_rate

    def _looks_like_thor_backend(self) -> bool:
        return True

    async def screenshot(self) -> bytes:
        return await self.client.screenshot()

    def close(self) -> CleanupResult:
        receipt = self.harness.close()
        return CleanupResult(
            status="succeeded" if receipt.succeeded else "failed",
            evidence_ref=receipt.evidence_ref,
        )

    def write_worker_evidence(self, path: Path, close_result: CleanupResult) -> None:
        close_request = next(
            (
                item
                for item in reversed(self.client.request_history)
                if item["operation"] == "close"
            ),
            None,
        )
        stderr_path = self.client.stderr_path
        payload = {
            "schema": "homemaster-v35-worker-lifecycle-v1",
            "worker_pid": self.client.worker_pid,
            "worker_exit_code": self.client.worker_exit_code,
            "request_history": list(self.client.request_history),
            "raw_state": self.client.raw_state,
            "close": {
                "status": close_result.status,
                "evidence_ref": close_result.evidence_ref,
                "request": close_request,
            },
            "stderr_path": str(stderr_path),
            "stderr": stderr_path.read_text(encoding="utf-8", errors="replace")
            if stderr_path.is_file()
            else "",
            "cleanup": {"worker_exited": self.client.worker_exit_code is not None},
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    def _close_after_reset_failure(self) -> None:
        try:
            self.harness.close()
        except Exception:
            pass


__all__ = ["WorkerAlfworldAdapter"]
