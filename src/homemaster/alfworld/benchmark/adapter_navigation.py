"""Navigation operations for the ALFWorld environment adapter."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from homemaster.alfworld.benchmark._adapter_shared import (
    AlfworldEnvState,
    _AdapterOracleBackend,
    _empty_execution_trace_events,
    _execution_feedback,
    _external_action_from_event,
    _find_object_location,
    _first,
    _first_info,
    _is_invalid_feedback,
    _model_visible_tool_args,
    _navigation_candidates_hash,
    _navigation_execution_feedback,
    _NavigationResult,
    _observed_label_for_type,
    _ordered_navigation_sources,
    _pose_context_created_trace_event,
    _pose_context_invalidated_trace_event,
    _resolve_navigation_target,
    _safe_navigation_feedback,
    _TargetResolutionResult,
    _teleport_action_from_pose,
    _teleport_to_object_ids,
    _teleport_to_visible_object,
    _thor_step,
)
from homemaster.alfworld.benchmark.scene_execution import (
    AgentPose,
    ExternalActionResult,
    NavigationAnchorResolver,
    OracleNavigationExecutor,
)
from homemaster.alfworld.gateway import OracleActionGateway
from homemaster.alfworld.object_view import CurrentObjectView
from homemaster.alfworld.pose_snapshot import load_public_object_vocabulary
from homemaster.alfworld.types import AlfworldStepResult


class AlfworldAdapterNavigationMixin:
    """Navigation/finding entry points (virtual go-to, move_to, find_object)."""
    def _indexed_navigation_target(
        self,
        thor_env: Any,
        label: str,
    ) -> _TargetResolutionResult:
        if self._scene_object_index is None:
            self._refresh_scene_object_index()
        if self._scene_object_index is None:
            return _resolve_navigation_target(thor_env, label)
        return _resolve_navigation_target(
            thor_env,
            label,
            scene_index=self._scene_object_index,
        )

    def move_to(self, *, pose: AgentPose) -> ExternalActionResult:
        try:
            thor_env = self._resolve_thor_env()
            event = _thor_step(thor_env, _teleport_action_from_pose(pose))
        except Exception as exc:
            return ExternalActionResult(
                status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                detail=str(exc),
            )
        self._event_sequence += 1
        return _external_action_from_event(event, event_sequence=self._event_sequence)

    def navigate_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        self._pose_context = None
        label = target.strip()
        command = f"virtual go to {label}"
        backend_action_count = 0
        try:
            thor_env = self._resolve_thor_env()
            nav_result = _teleport_to_visible_object(thor_env, label)
            backend_action_count = nav_result.backend_action_count
            success = nav_result.success
            observation = (
                f"Navigation backend moved to the target area for {label}."
                if success
                else nav_result.feedback
            )
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
        except Exception as exc:
            success = False
            observation = str(exc)
            won = previous.won
            goal_rate = previous.goal_condition_success_rate
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=observation,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + (0 if success else 1),
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=success,
            state=state,
            execution_feedback=_execution_feedback(
                tool_name,
                tool_args,
                success=success,
                failure_reason=None if success else "navigation_target_not_visible",
            ),
            feedback=observation,
            backend_action_count=backend_action_count,
        )

    def go_to_target(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        if not self._looks_like_thor_backend():
            return self._batch_command_step(
                f"go to {target.strip()}",
                tool_name=tool_name,
                tool_args=tool_args,
            )
        previous = self.current_state
        previous_pose_context = self._pose_context
        self._pose_context = None
        label = target.strip()
        command = f"go to target {label}"
        tool_call_id = str(tool_args.get("tool_call_id") or tool_name)
        navigation_context_id = (
            f"navigation-{self._scene_generation}-{self._goal_generation}-"
            f"{self._event_sequence}-{tool_call_id}"
        )
        try:
            thor_env = self._resolve_thor_env()
            resolved = self._indexed_navigation_target(thor_env, label)
        except Exception as exc:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=str(exc),
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=str(exc),
                trace_events=_empty_execution_trace_events(
                    execution_kind="navigation",
                    context_id=navigation_context_id,
                    scene_generation=self._scene_generation,
                    goal_generation=self._goal_generation,
                    source_event_sequence=self._event_sequence,
                    tool_call_id=tool_call_id,
                    classification="env_error",
                    budget_limit={
                        "candidates": self._navigation_budget.max_navigation_candidates,
                        "backend_actions": (self._navigation_budget.max_navigation_backend_actions),
                        "elapsed_ms": self._navigation_budget.max_navigation_elapsed_ms,
                    },
                ),
            )

        enriched_args = dict(tool_args)
        if resolved.success:
            enriched_args.update(
                {
                    "resolved_kind": resolved.resolved_kind,
                    "resolved_label": resolved.resolved_label,
                    "object_label": resolved.object_label,
                    "object_type": resolved.object_type,
                    "source_receptacle": resolved.source_receptacle,
                    "object_id": resolved.object_id,
                }
            )
        if not resolved.success:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=resolved.feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(enriched_args),
                translated_command=command,
                success=False,
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name, enriched_args, success=False, failure_reason="target_not_found"
                ),
                feedback=resolved.feedback,
                trace_events=_empty_execution_trace_events(
                    execution_kind="navigation",
                    context_id=navigation_context_id,
                    scene_generation=self._scene_generation,
                    goal_generation=self._goal_generation,
                    source_event_sequence=self._event_sequence,
                    tool_call_id=tool_call_id,
                    classification="target_not_found",
                    budget_limit={
                        "candidates": self._navigation_budget.max_navigation_candidates,
                        "backend_actions": (self._navigation_budget.max_navigation_backend_actions),
                        "elapsed_ms": self._navigation_budget.max_navigation_elapsed_ms,
                    },
                ),
            )

        try:
            thor_env = self._resolve_thor_env()
            nav = _teleport_to_object_ids(
                thor_env,
                [resolved.object_id],
                navigation_budget=self._navigation_budget,
                monotonic_ms=self._monotonic_ms,
                context_id=navigation_context_id,
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                source_event_sequence=self._event_sequence,
                tool_call_id=tool_call_id,
            )
            self._event_sequence += nav.backend_action_count
            if nav.budget_stop_reason is not None:
                enriched_args["budget_stop_reason"] = nav.budget_stop_reason
            nav_result = self._state_from_virtual_navigation(
                previous=previous,
                command=f"virtual go to {resolved.resolved_label or label}",
                tool_name=tool_name,
                tool_args=enriched_args,
                success=nav.success,
                failure_reason=nav.failure_reason,
                feedback=(
                    f"Navigation backend moved to the target area for "
                    f"{resolved.resolved_label or label}."
                    if nav.success
                    else nav.feedback
                ),
            )
        except Exception as exc:
            nav_result = self._state_from_virtual_navigation(
                previous=previous,
                command=f"virtual go to {resolved.resolved_label or label}",
                tool_name=tool_name,
                tool_args=enriched_args,
                success=False,
                failure_reason="execution_state_uncertain",
                feedback=str(exc),
            )
            nav = _NavigationResult(
                success=False,
                failure_reason="execution_state_uncertain",
                feedback=str(exc),
                budget_stop_reason=None,
                backend_action_count=0,
                event=None,
                actual_pose=None,
                reachable=[],
                trace_events=_empty_execution_trace_events(
                    execution_kind="navigation",
                    context_id=navigation_context_id,
                    scene_generation=self._scene_generation,
                    goal_generation=self._goal_generation,
                    source_event_sequence=self._event_sequence,
                    tool_call_id=tool_call_id,
                    classification="execution_state_uncertain",
                    budget_limit={
                        "candidates": self._navigation_budget.max_navigation_candidates,
                        "backend_actions": (self._navigation_budget.max_navigation_backend_actions),
                        "elapsed_ms": self._navigation_budget.max_navigation_elapsed_ms,
                    },
                ),
                context_id=navigation_context_id,
                locked_candidates_hash=_navigation_candidates_hash(()),
                candidates_attempted=0,
            )
        if nav_result.success and nav.actual_pose is not None:
            enriched_args["navigation_anchor"] = {
                "object_id": resolved.object_id,
                "label": resolved.resolved_label,
                "position": {
                    "x": float(nav.actual_pose.x),
                    "y": float(nav.actual_pose.y),
                    "z": float(nav.actual_pose.z),
                },
                "rotation": float(nav.actual_pose.rotation),
                "horizon": float(nav.actual_pose.horizon),
                "scene_generation": self._scene_generation,
                "goal_generation": self._goal_generation,
                "source_event_sequence": self._event_sequence,
            }
        self._last_go_to_object_id = resolved.object_id if nav_result.success else None
        trace_events = list(getattr(nav, "trace_events", ()))
        if previous_pose_context is not None:
            trace_events.insert(
                0,
                _pose_context_invalidated_trace_event(
                    previous_pose_context,
                    reason="superseded_by_navigation",
                ),
            )
        if nav_result.success and resolved.object_id and nav.event is not None:
            self._pose_context = self._new_pose_context(
                nav=nav,
                anchor_object_id=resolved.object_id,
                tool_name=tool_name,
                tool_args=tool_args,
            )
            if self._pose_context is not None:
                insertion_index = max(0, len(trace_events) - 1)
                trace_events.insert(
                    insertion_index,
                    _pose_context_created_trace_event(self._pose_context),
                )
        feedback = (
            f"Reached {resolved.resolved_label or label}"
            if nav_result.success
            else nav_result.feedback
        )
        if nav_result.success and resolved.source_receptacle:
            feedback += f" at {resolved.source_receptacle}"
        if nav_result.success:
            feedback += "."
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=nav_result.success,
            state=nav_result.state,
            execution_feedback=_execution_feedback(
                tool_name,
                enriched_args,
                success=nav_result.success,
                failure_reason=nav_result.failure_reason,
            ),
            feedback=feedback,
            backend_action_count=nav.backend_action_count,
            trace_events=tuple(trace_events),
        )

    def _go_to_target_v18(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        command = f"go to target {target.strip()}"
        try:
            thor_env = self._resolve_thor_env()
            raw_event = getattr(thor_env, "last_event", None)
            backend = _AdapterOracleBackend(self)
            current_event = backend.capture_event()
            if (
                self._scene_object_index is None
                or self._scene_reset_fingerprint is None
                or self._snapshot_sha256 is None
            ):
                raise RuntimeError("V1.8 navigation identity is unavailable")
            result = OracleNavigationExecutor(
                scene_index=self._scene_object_index,
                public_object_types=load_public_object_vocabulary().object_types,
                object_view=CurrentObjectView(
                    event=raw_event,
                    event_sequence=self._event_sequence,
                ),
                current_event=current_event,
                pose_store=self._pose_store,
                parent_resolver=NavigationAnchorResolver(),
                gateway=OracleActionGateway(backend=backend),
                allow_offscreen_object_navigation=self._allow_offscreen_object_navigation,
            ).execute(
                target,
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                scene_reset_fingerprint=self._scene_reset_fingerprint,
                snapshot_sha256=self._snapshot_sha256,
            )
        except Exception:
            result = None

        if result is None:
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                state=previous,
                execution_feedback=_execution_feedback(
                    tool_name,
                    tool_args,
                    success=False,
                    failure_reason="execution_state_uncertain",
                ),
                feedback="The current execution state could not be verified.",
            )

        if result.backend_action_count == 0:
            feedback = _safe_navigation_feedback(result.error, target)
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                state=previous,
                execution_feedback=_navigation_execution_feedback(result.error, target),
                feedback=feedback,
                backend_action_count=0,
                trace_events=result.trace_events,
            )

        self._pose_context = None
        state_result = self._state_from_virtual_navigation(
            previous=previous,
            command=command,
            tool_name=tool_name,
            tool_args=tool_args,
            success=result.success,
            failure_reason=result.error,
            feedback=(
                f"Reached {target.strip()}."
                if result.success
                else _safe_navigation_feedback(result.error, target)
            ),
        )
        if result.success:
            self._pose_context = result.context
        return replace(
            state_result,
            execution_feedback=_navigation_execution_feedback(
                result.error,
                target,
                success=result.success,
                state_changed=True if result.success else None,
            ),
            backend_action_count=result.backend_action_count,
            trace_events=result.trace_events,
        )

    def _state_from_virtual_navigation(
        self,
        *,
        previous: AlfworldEnvState,
        command: str,
        tool_name: str,
        tool_args: dict[str, Any],
        success: bool,
        feedback: str,
        failure_reason: str | None = None,
    ) -> AlfworldStepResult:
        won = self.is_current_goal_satisfied() if success else previous.won
        goal_rate = (
            self.current_goal_condition_success_rate()
            if success
            else previous.goal_condition_success_rate
        )
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=feedback,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count,
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=success,
            state=state,
            execution_feedback=_execution_feedback(
                tool_name,
                tool_args,
                success=success,
                failure_reason=(
                    None if success else (failure_reason or "harness_navigation_failure")
                ),
            ),
            feedback=feedback,
        )

    def find_object(
        self,
        target: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        self._pose_context = None
        label = target.strip()
        command = f"find object {label}"
        try:
            thor_env = self._resolve_thor_env()
            found = _find_object_location(thor_env, label)
        except Exception as exc:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=str(exc),
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=command,
                success=False,
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=str(exc),
            )

        enriched_args = dict(tool_args)
        enriched_args.update(
            {
                "object_label": found.object_label,
                "object_type": found.object_type,
                "source_receptacle": found.source_receptacle,
            }
        )
        if not found.success:
            state = self._state_after_backend_failure(
                previous=previous,
                command=command,
                feedback=found.feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(enriched_args),
                translated_command=command,
                success=False,
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name,
                    enriched_args,
                    success=False,
                    failure_reason="object_not_found",
                ),
                feedback=found.feedback,
            )

        search_result = self._search_visible_object_source(
            target=found.object_type or label,
            preferred_source=found.source_receptacle,
            previous=previous,
            command=command,
            tool_name=tool_name,
            tool_args=enriched_args,
        )
        if search_result is not None:
            return search_result

        nav_result = self.navigate_to_target(
            found.object_type or label,
            tool_name=tool_name,
            tool_args=enriched_args,
        )
        feedback = (
            f"Found {found.object_label or label}. {nav_result.feedback}"
            if nav_result.success
            else nav_result.feedback
        )
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=nav_result.success,
            state=nav_result.state,
            execution_feedback=_execution_feedback(
                tool_name,
                enriched_args,
                success=nav_result.success,
                failure_reason=nav_result.failure_reason,
            ),
            feedback=feedback,
        )

    def _search_visible_object_source(
        self,
        *,
        target: str,
        preferred_source: str | None,
        previous: AlfworldEnvState,
        command: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult | None:
        sources = _ordered_navigation_sources(
            previous.admissible_commands,
            preferred_source=preferred_source,
        )
        if not sources:
            return None

        final_payload: tuple[str, float, bool, bool, float, tuple[str, ...]] | None = None
        for source in sources:
            nav_command = f"go to {source}"
            try:
                obs, scores, dones, infos = self._env.step([nav_command])
            except Exception:
                continue
            observation = str(_first(obs, ""))
            reward = float(_first(scores, 0.0))
            done = bool(_first(dones, False))
            won = bool(_first_info(infos, "won", False))
            goal_rate = float(_first_info(infos, "goal_condition_success_rate", 0.0))
            admissible = tuple(str(item) for item in _first_info(infos, "admissible_commands", []))
            final_payload = (observation, reward, done, won, goal_rate, admissible)
            if _is_invalid_feedback(observation):
                continue
            object_label = _observed_label_for_type(observation, target)
            if object_label is None:
                continue
            enriched_args = dict(tool_args)
            enriched_args["object_label"] = object_label
            enriched_args["source_receptacle"] = source
            state = AlfworldEnvState(
                episode_id=previous.episode_id,
                task=previous.task,
                observation=observation,
                inventory=previous.inventory,
                last_command=nav_command,
                last_feedback=observation,
                reward=reward,
                done=done,
                won=won,
                goal_condition_success_rate=goal_rate,
                frame_path=self._save_current_frame(step_index=previous.step_index + 1),
                step_index=previous.step_index + 1,
                invalid_action_count=previous.invalid_action_count,
                admissible_commands=admissible,
            )
            self._state = state
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(enriched_args),
                translated_command=f"{command} -> {nav_command}",
                success=True,
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name, enriched_args, success=True, failure_reason=None
                ),
                feedback=f"Found {object_label} at {source}. {observation}",
            )

        if final_payload is None:
            return None
        observation, reward, done, won, goal_rate, admissible = final_payload
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=observation,
            inventory=previous.inventory,
            last_command=command,
            last_feedback=(f"Could not find a visible {target} at any known navigable place."),
            reward=reward,
            done=done,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + 1,
            admissible_commands=admissible,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(tool_args),
            translated_command=command,
            success=False,
            state=state,
            execution_feedback=_execution_feedback(
                tool_name, tool_args, success=False, failure_reason="object_not_visible"
            ),
            feedback=f"Could not find a visible {target} at any known navigable place.",
        )
