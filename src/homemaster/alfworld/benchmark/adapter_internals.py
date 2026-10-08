"""Shared internal helpers for the ALFWorld environment adapter."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.alfworld.benchmark._adapter_shared import (
    _DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS,
    _DEFAULT_NAVIGATION_MAX_CANDIDATES,
    _DEFAULT_NAVIGATION_MAX_ELAPSED_MS,
    _DEFAULT_PUT_MAX_BACKEND_ACTIONS,
    _DEFAULT_PUT_MAX_CANDIDATES,
    _DEFAULT_PUT_MAX_ELAPSED_MS,
    AlfworldEnvState,
    _event_hash,
    _latest_thor_frame,
    _local_pose_candidates,
    _NavigationResult,
)
from homemaster.alfworld.benchmark.scene_execution import (
    ExecutionBudget,
    OracleExecutionContext,
    PoseContext,
    SceneObjectIndex,
)
from homemaster.alfworld.pose_snapshot import FrozenOraclePoseStore
from homemaster.alfworld.trial_selection import TrialSelectionEntry
from homemaster.alfworld.types import AlfworldResetResult


class AlfworldAdapterInternalsMixin:
    """Constructor plus cross-cutting helpers shared by every mixin group."""
    def __init__(
        self,
        *,
        env: Any,
        episode_prefix: str,
        seed: int,
        frame_dir: Path | None = None,
        allow_offscreen_object_navigation: bool = True,
    ) -> None:
        self._env = env
        self._episode_prefix = episode_prefix
        self._seed = seed
        self._frame_dir = frame_dir
        self._allow_offscreen_object_navigation = allow_offscreen_object_navigation
        self._state: AlfworldEnvState | None = None
        self._last_go_to_object_id: str | None = None
        self._scene_generation = 0
        self._goal_generation = 0
        self._event_sequence = 0
        self._application_run_id = "unbound"
        self._scene_object_index: SceneObjectIndex | None = None
        self._pose_context: PoseContext | OracleExecutionContext | None = None
        self._scene_reset_fingerprint: str | None = None
        self._snapshot_sha256: str | None = None
        self._trial_selection: TrialSelectionEntry | None = None
        self._pose_store = FrozenOraclePoseStore()
        self._lifecycle = "not_started"
        self._last_reset_result: AlfworldResetResult | None = None
        self._navigation_budget = SimpleNamespace(
            max_navigation_candidates=_DEFAULT_NAVIGATION_MAX_CANDIDATES,
            max_navigation_backend_actions=_DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS,
            max_navigation_elapsed_ms=_DEFAULT_NAVIGATION_MAX_ELAPSED_MS,
        )
        self._put_budget = ExecutionBudget(
            max_pose_candidates=_DEFAULT_PUT_MAX_CANDIDATES,
            max_backend_actions=_DEFAULT_PUT_MAX_BACKEND_ACTIONS,
            max_elapsed_ms=_DEFAULT_PUT_MAX_ELAPSED_MS,
        )
        self._monotonic_ms = lambda: time.perf_counter() * 1000.0
        if hasattr(self._env, "seed"):
            self._env.seed(seed)

    def set_frame_dir(self, frame_dir: Path | None) -> None:
        self._frame_dir = frame_dir

    @property
    def current_state(self) -> AlfworldEnvState:
        if self._state is None:
            raise RuntimeError("ALFWorld environment has not been reset")
        return self._state

    @property
    def backend_id(self) -> str:
        episode = self._state.episode_id if self._state is not None else self._episode_prefix
        return f"alfworld:{episode}"

    @property
    def authoritative_object_index(self) -> SceneObjectIndex | None:
        """Read-only authoritative object identity snapshot for permission checks.

        Builds the index from the latest backend metadata when absent;
        performs no navigation, manipulation, or reset. Returns None when
        the backend exposes no usable object list so callers fail closed.
        """
        if self._scene_object_index is None:
            try:
                self._refresh_scene_object_index()
            except Exception:
                return None
        return self._scene_object_index

    @property
    def generation(self) -> int:
        return self._scene_generation * 1_000_000 + self._goal_generation

    @property
    def state_sequence(self) -> int:
        return self.current_state.step_index

    @property
    def event_sequence(self) -> int:
        return self._event_sequence

    @property
    def current_pose_context(self) -> PoseContext | OracleExecutionContext | None:
        return self._pose_context

    @property
    def lifecycle(self) -> str:
        return self._lifecycle

    @property
    def last_reset_result(self) -> AlfworldResetResult | None:
        return self._last_reset_result

    async def screenshot(self) -> bytes:
        state = self.current_state
        if not state.frame_path:
            raise RuntimeError("ALFWorld current state has no frame")
        return Path(state.frame_path).read_bytes()

    def bind_application_run(self, run_id: str, generation: int) -> None:
        del generation
        self._application_run_id = run_id

    def _looks_like_thor_backend(self) -> bool:
        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError:
            return False
        event = getattr(thor_env, "last_event", None)
        metadata = getattr(event, "metadata", None)
        return callable(getattr(thor_env, "step", None)) and isinstance(metadata, dict)

    def _refresh_scene_object_index(self) -> None:
        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError:
            return
        event = getattr(thor_env, "last_event", None)
        metadata = getattr(event, "metadata", None)
        if not isinstance(metadata, dict):
            return
        objects = metadata.get("objects")
        if not isinstance(objects, list):
            return
        self._scene_object_index = SceneObjectIndex.from_objects(
            objects=objects,
            scene_generation=self._scene_generation,
            snapshot_event_sequence=self._event_sequence,
            require_receptacle_metadata=False,
        )

    def _resolve_thor_env(self) -> Any:
        """Walk the batch env wrapper to the底层 ThorEnv instance.

        AlfredThorEnv batch env: self._env.envs[0].env  (Thor thread.env = ThorEnv)
        Falls back to attribute search only for diagnostics around the THOR adapter.
        """
        env = self._env
        envs = getattr(env, "envs", None)
        if isinstance(envs, list | tuple) and envs:
            thor_env = getattr(envs[0], "env", None)
            if thor_env is not None:
                return thor_env
        # Fallback: search for set_task on the env itself.
        if hasattr(env, "set_task"):
            return env
        raise RuntimeError(
            "could not resolve底层 ThorEnv from the ALFWorld batch env; "
            "advance_goal / is_current_goal_satisfied require AlfredThorEnv"
        )

    def _new_pose_context(
        self,
        *,
        nav: _NavigationResult,
        anchor_object_id: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> PoseContext | None:
        current_pose = nav.actual_pose
        if current_pose is None:
            return None
        local_candidates = _local_pose_candidates(
            event=nav.event,
            anchor_object_id=anchor_object_id,
            reachable=nav.reachable,
            current_pose=current_pose,
        )
        tool_call_id = str(tool_args.get("tool_call_id") or tool_name)
        return PoseContext.lock(
            context_id=(
                f"pose-{self._scene_generation}-{self._goal_generation}-"
                f"{self._event_sequence}-{anchor_object_id}"
            ),
            scene_generation=self._scene_generation,
            goal_generation=self._goal_generation,
            source_event_sequence=self._event_sequence,
            source_frame_hash=_event_hash(nav.event),
            anchor_object_id=anchor_object_id,
            current_actual_pose=current_pose,
            local_candidates=local_candidates,
            created_tool_call_id=tool_call_id,
        )

    def _state_after_backend_failure(
        self,
        *,
        previous: AlfworldEnvState,
        command: str,
        feedback: str,
    ) -> AlfworldEnvState:
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=previous.observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=feedback,
            reward=previous.reward,
            done=previous.done,
            won=previous.won,
            goal_condition_success_rate=previous.goal_condition_success_rate,
            frame_path=previous.frame_path,
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + 1,
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return state

    def _save_current_frame(self, *, step_index: int) -> str | None:
        if self._frame_dir is None:
            return None
        frame = _latest_thor_frame(self._env)
        if frame is None:
            return None
        try:
            from PIL import Image

            self._frame_dir.mkdir(parents=True, exist_ok=True)
            path = self._frame_dir / f"frame-{step_index:04d}.png"
            Image.fromarray(frame).save(path)
            return str(path)
        except Exception:
            return None
