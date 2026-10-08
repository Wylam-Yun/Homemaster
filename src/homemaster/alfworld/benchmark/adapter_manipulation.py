"""Manipulation operations for the ALFWorld environment adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from homemaster.alfworld.benchmark._adapter_shared import (
    AlfworldEnvState,
    _AdapterOracleBackend,
    _batch_command_for_action,
    _empty_execution_trace_events,
    _event_error,
    _execute_manipulation,
    _execution_feedback,
    _external_action_from_event,
    _inventory_labels,
    _inventory_object_ids,
    _inventory_text,
    _model_visible_tool_args,
    _thor_step,
)
from homemaster.alfworld.benchmark.scene_execution import (
    ExternalActionResult,
    ManipulationExecutor,
    OracleExecutionContext,
    OracleManipulationExecutor,
    PutExecutionRequest,
    SceneObjectIndex,
)
from homemaster.alfworld.gateway import OracleActionGateway
from homemaster.alfworld.object_view import CurrentObjectView
from homemaster.alfworld.types import (
    AlfworldStepResult,
    make_execution_feedback,
)


class AlfworldAdapterManipulationMixin:
    """Manipulation entry points (thor actions, put executor, force toggles)."""
    def put_object(
        self,
        *,
        object_id: str,
        receptacle_object_id: str,
    ) -> ExternalActionResult:
        try:
            thor_env = self._resolve_thor_env()
            event = _thor_step(
                thor_env,
                {
                    "action": "PutObject",
                    "objectId": object_id,
                    "receptacleObjectId": receptacle_object_id,
                    "forceAction": True,
                    "placeStationary": True,
                },
            )
        except Exception as exc:
            return ExternalActionResult(
                status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                detail=str(exc),
            )
        self._event_sequence += 1
        return _external_action_from_event(event, event_sequence=self._event_sequence)

    def manipulate_with_thor(
        self,
        *,
        action: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        normalized_action = action.strip().lower()
        if not self._looks_like_thor_backend():
            return self._batch_command_step(
                _batch_command_for_action(normalized_action, tool_args),
                tool_name=tool_name,
                tool_args=tool_args,
            )
        if normalized_action == "put":
            return self._manipulate_put_with_executor(
                tool_name=tool_name,
                tool_args=tool_args,
            )

        previous = self.current_state
        self._pose_context = None
        command = f"thor {normalized_action}"
        try:
            thor_env = self._resolve_thor_env()
            result = _execute_manipulation(
                thor_env,
                normalized_action,
                tool_args,
                last_go_to_object_id=self._last_go_to_object_id,
            )
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
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
                "backend": "thor_api",
                "backend_actions": result.backend_actions,
            }
        )
        enriched_args.update(
            {key: value for key, value in result.resolved.items() if value is not None}
        )
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=result.feedback,
            inventory=_inventory_text(thor_env),
            last_command=command,
            last_feedback=result.feedback,
            reward=previous.reward,
            done=won,
            won=won,
            goal_condition_success_rate=goal_rate,
            frame_path=self._save_current_frame(step_index=previous.step_index + 1),
            step_index=previous.step_index + 1,
            invalid_action_count=previous.invalid_action_count + (0 if result.success else 1),
            admissible_commands=previous.admissible_commands,
        )
        self._state = state
        return AlfworldStepResult(
            tool_name=tool_name,
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=result.success,
            state=state,
            execution_feedback=_execution_feedback(
                tool_name,
                enriched_args,
                success=result.success,
                failure_reason=None if result.success else "invalid_action",
            ),
            feedback=result.feedback,
            backend_action_count=len(result.backend_actions),
        )

    def _batch_command_step(
        self,
        command: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        """Exercise the tiny batch protocol used by deterministic unit fakes."""
        return self.step(command, tool_name=tool_name, tool_args=tool_args)

    def _manipulate_with_thor_v18(
        self,
        action: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        allowed = {"take", "open", "close", "put", "use", "slice", "heat", "cool", "clean"}
        if action not in allowed:
            execution_feedback = make_execution_feedback(
                action="verify",
                success=False,
                error="invalid_tool_arguments",
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"thor {action}",
                success=False,
                state=previous,
                execution_feedback=execution_feedback,
                feedback=execution_feedback.to_model_payload()["detail"],
            )
        try:
            thor_env = self._resolve_thor_env()
            raw_event = getattr(thor_env, "last_event", None)
            backend = _AdapterOracleBackend(self)
            current_event = backend.capture_event()
            if self._scene_object_index is None:
                raise RuntimeError("scene object index is unavailable")
            context = (
                self._pose_context
                if isinstance(self._pose_context, OracleExecutionContext)
                else None
            )
            object_label = str(tool_args.get("object") or "").strip() or None
            target_label = (
                str(
                    tool_args.get("tool_receptacle") or tool_args.get("target_receptacle") or ""
                ).strip()
                or None
            )
            result = OracleManipulationExecutor(
                scene_index=self._scene_object_index,
                object_view=CurrentObjectView(
                    event=raw_event,
                    event_sequence=self._event_sequence,
                ),
                current_event=current_event,
                raw_event=raw_event,
                raw_event_reader=lambda: getattr(thor_env, "last_event", None),
                context=context,
                gateway=OracleActionGateway(backend=backend),
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
            ).execute(
                action,
                object_label=object_label,
                target_label=target_label,
            )
        except Exception:
            execution_feedback = make_execution_feedback(
                action=action,
                success=False,
                error="execution_state_uncertain",
                object_label=str(tool_args.get("object") or "").strip() or None,
                target_label=str(
                    tool_args.get("tool_receptacle") or tool_args.get("target_receptacle") or ""
                ).strip()
                or None,
            )
            result = SimpleNamespace(
                feedback=execution_feedback,
                context=None,
                backend_action_count=0,
                trace_events=(),
            )

        self._pose_context = result.context
        payload = result.feedback.to_model_payload()
        if result.backend_action_count == 0:
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"thor {action}",
                success=result.feedback.success,
                state=previous,
                execution_feedback=result.feedback,
                feedback=payload["detail"],
                backend_action_count=0,
                trace_events=result.trace_events,
            )

        won = self.is_current_goal_satisfied() if result.feedback.success else previous.won
        goal_rate = (
            self.current_goal_condition_success_rate()
            if result.feedback.success
            else previous.goal_condition_success_rate
        )
        inventory = result.feedback.inventory
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=str(payload["detail"] or "Action completed."),
            inventory=(
                "You are carrying: " + ", ".join(inventory)
                if inventory
                else "You are carrying nothing."
            ),
            last_command=f"thor {action}",
            last_feedback=str(payload["detail"] or "Action completed."),
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
            translated_command=f"thor {action}",
            success=result.feedback.success,
            state=state,
            execution_feedback=result.feedback,
            feedback=payload["detail"],
            backend_action_count=result.backend_action_count,
            trace_events=result.trace_events,
        )

    def _manipulate_put_with_executor(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        command = "thor put"
        requested_object = str(tool_args.get("object") or "").strip()
        requested_target = str(tool_args.get("target_receptacle") or "").strip()

        if not requested_object or not requested_target:
            return self._put_step_result(
                previous=previous,
                thor_env=None,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="invalid_tool_arguments",
                feedback="object and target_receptacle are required for put.",
                object_label=requested_object or None,
                target_label=requested_target or None,
                inventory_ids=(),
                object_state="unknown",
            )

        try:
            thor_env = self._resolve_thor_env()
        except RuntimeError as exc:
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=None,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="execution_state_uncertain",
                feedback=str(exc),
                object_label=requested_object,
                target_label=requested_target,
                inventory_ids=(),
                object_state="unknown",
                terminal=True,
                score_eligible=False,
            )

        scene_index = self._scene_object_index
        if scene_index is None or scene_index.scene_generation != self._scene_generation:
            self._scene_object_index = None
            self._refresh_scene_object_index()
            scene_index = self._scene_object_index
        if scene_index is None:
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="harness_grounding_failure",
                feedback="Could not read the authoritative scene object index.",
                object_label=requested_object,
                target_label=requested_target,
                inventory_ids=(),
                object_state="unknown",
                terminal=True,
                score_eligible=False,
            )

        object_ref = scene_index.resolve(requested_object)
        target_ref = scene_index.resolve(requested_target)
        if object_ref is None or target_ref is None:
            missing_label = requested_object if object_ref is None else requested_target
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="target_not_found",
                feedback=f"No exact scene object matched {missing_label}.",
                object_label=(
                    object_ref.canonical_label if object_ref is not None else requested_object
                ),
                target_label=(
                    target_ref.canonical_label if target_ref is not None else requested_target
                ),
                inventory_ids=_inventory_object_ids(thor_env),
                object_state="unknown",
                held_object_id=object_ref.object_id if object_ref is not None else None,
                target_object_id=target_ref.object_id if target_ref is not None else None,
                scene_index=scene_index,
            )

        object_label = object_ref.canonical_label
        target_label = target_ref.canonical_label
        inventory_ids = _inventory_object_ids(thor_env)
        if object_ref.metadata.get("pickupable") is not True:
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="action_not_applicable",
                feedback=f"{object_label} is not pickupable.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=inventory_ids,
                object_state="unknown",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )
        if target_ref.metadata.get("receptacle") is not True:
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="target_not_receptacle",
                feedback=f"{target_label} is not a receptacle.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=inventory_ids,
                object_state=("held" if object_ref.object_id in inventory_ids else "not_held"),
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )

        before = self.read_external_state(
            held_object_id=object_ref.object_id,
            target_receptacle_id=target_ref.object_id,
        )
        if before.status != "ok":
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="execution_state_uncertain",
                feedback="Could not prove the exact pre-put external state.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=before.inventory_object_ids,
                object_state="unknown",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
                terminal=True,
                score_eligible=False,
            )
        if (
            before.held_object_id != object_ref.object_id
            or object_ref.object_id not in before.inventory_object_ids
        ):
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="object_not_held",
                feedback=f"{object_label} is not currently held.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=before.inventory_object_ids,
                object_state="not_held",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )

        pose_context = self._pose_context
        context_is_valid = (
            pose_context is not None
            and pose_context.scene_generation == self._scene_generation
            and pose_context.goal_generation == self._goal_generation
            and pose_context.source_event_sequence == self._event_sequence
            and pose_context.anchor_object_id == target_ref.object_id
            and before.actual_agent_pose is not None
            and before.actual_agent_pose.matches(pose_context.current_actual_pose)
        )
        if not context_is_valid or pose_context is None:
            self._pose_context = None
            return self._put_step_result(
                previous=previous,
                thor_env=thor_env,
                tool_name=tool_name,
                tool_args=tool_args,
                command=command,
                classification="navigation_required",
                feedback=f"Navigate to {target_label} before putting the object.",
                object_label=object_label,
                target_label=target_label,
                inventory_ids=before.inventory_object_ids,
                object_state="held",
                held_object_id=object_ref.object_id,
                target_object_id=target_ref.object_id,
                scene_index=scene_index,
            )

        request = PutExecutionRequest(
            tool_call_id=str(tool_args.get("tool_call_id") or tool_name),
            held_object_id=object_ref.object_id,
            target_receptacle_id=target_ref.object_id,
            pose_context=pose_context,
        )
        execution = ManipulationExecutor(
            backend=self,
            budget=self._put_budget,
            monotonic_ms=self._monotonic_ms,
        ).execute_put(request)
        self._pose_context = None

        final_read = execution.final_read
        final_inventory = (
            final_read.inventory_object_ids
            if final_read is not None
            else _inventory_object_ids(thor_env)
        )
        state_changed = final_read is not None and final_read.action_state != before.action_state
        object_state = (
            "placed"
            if execution.success
            else "held"
            if object_ref.object_id in final_inventory
            else "unknown"
        )
        detail = execution.detail or _event_error(getattr(thor_env, "last_event", None))
        feedback = (
            f"Placed {object_label} on/in {target_label}."
            if execution.success
            else detail
            or (
                "The put result contradicted the external terminal state."
                if execution.classification == "execution_state_uncertain"
                else f"No locked local pose could place {object_label} on/in {target_label}."
            )
        )
        terminal = execution.classification != "success"
        return self._put_step_result(
            previous=previous,
            thor_env=thor_env,
            tool_name=tool_name,
            tool_args=tool_args,
            command=command,
            classification=execution.classification,
            feedback=feedback,
            object_label=object_label,
            target_label=target_label,
            inventory_ids=final_inventory,
            object_state=object_state,
            state_changed=state_changed,
            held_object_id=object_ref.object_id,
            target_object_id=target_ref.object_id,
            scene_index=scene_index,
            locked_candidates_hash=execution.locked_candidates_hash,
            pose_candidates_attempted=execution.pose_candidates_attempted,
            put_attempt_count=execution.put_attempt_count,
            backend_action_count=execution.backend_action_count,
            budget_stop_reason=execution.budget_stop_reason,
            detail=detail,
            terminal=terminal,
            score_eligible=not terminal,
            success=execution.success,
            trace_events=execution.trace_events,
        )

    def _put_step_result(
        self,
        *,
        previous: AlfworldEnvState,
        thor_env: Any | None,
        tool_name: str,
        tool_args: dict[str, Any],
        command: str,
        classification: str,
        feedback: str,
        object_label: str | None,
        target_label: str | None,
        inventory_ids: tuple[str, ...],
        object_state: str,
        state_changed: bool = False,
        held_object_id: str | None = None,
        target_object_id: str | None = None,
        scene_index: SceneObjectIndex | None = None,
        locked_candidates_hash: str | None = None,
        pose_candidates_attempted: int = 0,
        put_attempt_count: int = 0,
        backend_action_count: int = 0,
        budget_stop_reason: str | None = None,
        detail: str = "",
        terminal: bool = False,
        score_eligible: bool = True,
        success: bool = False,
        trace_events: tuple[dict[str, Any], ...] = (),
    ) -> AlfworldStepResult:
        if not trace_events:
            trace_events = _empty_execution_trace_events(
                execution_kind="put",
                context_id=(
                    f"put-{self._scene_generation}-{self._goal_generation}-"
                    f"{self._event_sequence}-{tool_args.get('tool_call_id') or tool_name}"
                ),
                scene_generation=self._scene_generation,
                goal_generation=self._goal_generation,
                source_event_sequence=self._event_sequence,
                tool_call_id=str(tool_args.get("tool_call_id") or tool_name),
                classification="success" if success else classification,
                budget_limit={
                    "candidates": self._put_budget.max_pose_candidates,
                    "backend_actions": self._put_budget.max_backend_actions,
                    "elapsed_ms": self._put_budget.max_elapsed_ms,
                },
                backend_action_count=backend_action_count,
                candidate_count=pose_candidates_attempted,
                put_attempt_count=put_attempt_count,
                budget_stop_reason=budget_stop_reason,
                held_object_id=held_object_id,
                target_receptacle_id=target_object_id,
            )
        enriched_args = dict(tool_args)
        enriched_args.update(
            {
                "action": "put",
                "object": object_label,
                "target": target_label,
                "inventory": _inventory_labels(scene_index, inventory_ids),
                "object_state": object_state,
                "state_changed": state_changed,
                "detail": detail or feedback,
                "final_classification": classification,
                "held_object_id": held_object_id,
                "target_object_id": target_object_id,
                "locked_candidates_hash": locked_candidates_hash,
                "pose_candidates_attempted": pose_candidates_attempted,
                "put_attempt_count": put_attempt_count,
                "backend_action_count": backend_action_count,
                "budget_stop_reason": budget_stop_reason,
                "terminal": terminal,
                "score_eligible": score_eligible,
            }
        )

        if thor_env is not None:
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
            inventory_text = _inventory_text(thor_env)
        else:
            won = previous.won
            goal_rate = previous.goal_condition_success_rate
            inventory_text = previous.inventory
        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=inventory_text,
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
            tool_args=_model_visible_tool_args(enriched_args),
            translated_command=command,
            success=success,
            state=state,
            execution_feedback=_execution_feedback(
                tool_name,
                enriched_args,
                success=success,
                failure_reason=None if success else classification,
            ),
            feedback=feedback,
            backend_action_count=backend_action_count,
            trace_events=trace_events,
        )

    def force_toggle_unique_object_type(
        self,
        object_type: str,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> AlfworldStepResult:
        previous = self.current_state
        target_type = object_type.strip().casefold()
        try:
            thor_env = self._resolve_thor_env()
            objects = getattr(thor_env.last_event, "metadata", {}).get("objects", [])
            matches = [
                obj for obj in objects if str(obj.get("objectType", "")).casefold() == target_type
            ]
            if len(matches) != 1:
                feedback = (
                    f"Expected exactly one {object_type} target in the scene, found {len(matches)}."
                )
                state = self._state_after_backend_failure(
                    previous=previous,
                    command=f"force use {object_type}",
                    feedback=feedback,
                )
                return AlfworldStepResult(
                    tool_name=tool_name,
                    tool_args=_model_visible_tool_args(tool_args),
                    translated_command=f"force use {object_type}",
                    success=False,
                    state=state,
                    execution_feedback=_execution_feedback(
                        tool_name,
                        tool_args,
                        success=False,
                        failure_reason="ambiguous_grounding",
                    ),
                    feedback=feedback,
                )
            target_object_id = str(matches[0]["objectId"])
            event = thor_env.step(
                {
                    "action": "ToggleObjectOn",
                    "objectId": target_object_id,
                    "forceAction": True,
                }
            )
            success = bool(event.metadata.get("lastActionSuccess"))
            feedback = f"You turn on the {object_type.lower()}." if success else "Nothing happens."
            won = self.is_current_goal_satisfied()
            goal_rate = self.current_goal_condition_success_rate()
        except Exception as exc:
            feedback = str(exc)
            state = self._state_after_backend_failure(
                previous=previous,
                command=f"force use {object_type}",
                feedback=feedback,
            )
            return AlfworldStepResult(
                tool_name=tool_name,
                tool_args=_model_visible_tool_args(tool_args),
                translated_command=f"force use {object_type}",
                success=False,
                state=state,
                execution_feedback=_execution_feedback(
                    tool_name, tool_args, success=False, failure_reason="env_error"
                ),
                feedback=feedback,
            )

        state = AlfworldEnvState(
            episode_id=previous.episode_id,
            task=previous.task,
            observation=feedback,
            inventory=previous.inventory,
            last_command=f"force use {object_type}",
            last_feedback=feedback,
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
            translated_command=f"force use {object_type}",
            success=success,
            state=state,
            execution_feedback=_execution_feedback(
                tool_name,
                tool_args,
                success=success,
                failure_reason=None if success else "invalid_action",
            ),
            feedback=feedback,
        )
