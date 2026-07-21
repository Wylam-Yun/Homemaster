"""ALFWorld-backed robot tools for benchmark runs."""

from __future__ import annotations

from typing import Any

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.grounding import (
    GroundingCandidate,
    build_grounding_candidates,
    canonical_command_name,
    ground_text,
    normalized_key,
)
from homemaster.benchmarking.alfworld.translator import (
    AlfworldCommandTranslator,
    TranslatorValidationError,
)
from homemaster.benchmarking.alfworld.types import make_execution_feedback
from homemaster.tools.spec import ToolSpec

_TERMINAL_EXECUTION_FAILURES = {
    "execution_state_uncertain",
    "harness_grounding_failure",
    "harness_navigation_failure",
    "harness_operation_failure",
    "unclassified_execution_failure",
}


def _adapter(run_context: RunContext) -> AlfworldEnvAdapter:
    adapter = run_context.deps.get("alfworld_env")
    if adapter is None:
        raise RuntimeError("missing run_context.deps['alfworld_env']")
    return adapter


def _translator(run_context: RunContext) -> AlfworldCommandTranslator:
    translator = run_context.deps.get("alfworld_translator")
    if translator is None:
        raise RuntimeError("missing run_context.deps['alfworld_translator']")
    return translator


def _current_subtask(run_context: RunContext) -> Any:
    return run_context.deps.get("alfworld_current_subtask")


def _judge_config_path(run_context: RunContext) -> Any:
    return run_context.deps.get("alfworld_semantic_judge_config")


def _env_type(run_context: RunContext) -> str:
    config = run_context.deps.get("alfworld_config")
    return str(getattr(config, "env_type", "AlfredThorEnv"))


def make_alfworld_observe() -> ToolSpec:
    """Compatibility entry for explicit model observation before CL-13."""

    def executor(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResultMessage:
        del arguments
        state = _adapter(run_context).current_state
        payload = {
            "success": True,
            "episode_id": state.episode_id,
            "state_sequence": state.step_index,
            "observation_kind": "raster" if state.frame_path else "structured",
        }
        content = [ContentBlock(text=_json_dumps(payload))]
        if state.frame_path:
            content.append(ContentBlock.from_image_path(state.frame_path))
        else:
            content.append(ContentBlock(text=_json_dumps(state.to_model_visible_dict())))
        return ToolResultMessage(
            tool_call_id="",
            name="observe",
            content=content,
            data=payload,
        )

    return ToolSpec(
        name="observe",
        description="Capture the current ALFWorld view explicitly for model inspection.",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        executor_mode="programmatic",
        executor=executor,
    )


def _feedback_action(tool_name: str, arguments: dict[str, Any]) -> Any:
    action = str(arguments.get("action") or "").strip().lower()
    if action in {"take", "open", "close", "put", "use", "slice", "heat", "cool", "clean"}:
        return action
    if tool_name == "robot_go_to":
        return "navigate"
    return "verify"


def _result_from_step(
    step_result: Any,
    run_context: RunContext,
    *,
    evidence_refs: tuple[str, ...] = (),
) -> ToolResultMessage:
    data = step_result.execution_feedback.to_model_payload()
    outcome = run_context.deps.get("alfworld_episode_outcome")
    if outcome is not None:
        outcome.backend_action_count += int(getattr(step_result, "backend_action_count", 0))
    return _receipt_tool_result(
        name=step_result.tool_name,
        success=step_result.success,
        data=data,
        failure_reason=step_result.failure_reason,
        is_error=step_result.execution_feedback.terminal,
        evidence_refs=evidence_refs,
    )


def _validation_failure(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    run_context: RunContext,
    error: Exception,
) -> ToolResultMessage:
    feedback = make_execution_feedback(
        action=_feedback_action(tool_name, arguments),
        success=False,
        error="invalid_tool_arguments",
        object_label=str(arguments.get("object") or "") or None,
        target_label=str(
            arguments.get("target") or arguments.get("target_receptacle") or ""
        )
        or None,
    )
    data = feedback.to_model_payload()
    return _receipt_tool_result(
        name=tool_name,
        success=False,
        data=data,
        failure_reason=feedback.failure_reason,
        is_error=True,
    )


def _write_trace(run_context: RunContext, step_result: Any) -> tuple[str, ...]:
    trace = run_context.deps.get("alfworld_trace")
    if trace is None:
        return ()
    for event in getattr(step_result, "trace_events", ()):
        if isinstance(event, dict):
            trace.write_event(event)
    return (trace.write_event(step_result.to_trace_event()),)


def _exec_go_to(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResultMessage:
    grounded = dict(arguments)
    target = str(arguments.get("target", ""))
    target_grounding = _ground_target(
        run_context,
        target,
        allowed_kinds={"object", "receptacle", "toggle"},
    )
    grounded["target"] = target_grounding.value
    grounded_args = _with_grounding_metadata(grounded, {"target": target_grounding})
    if _env_type(run_context) == "AlfredTWEnv":
        try:
            command = _translator(run_context).navigate(
                target_receptacle=target_grounding.value
            )
        except TranslatorValidationError as exc:
            return _validation_failure(
                tool_name="robot_go_to",
                arguments=grounded,
                run_context=run_context,
                error=exc,
            )
        step_result = _adapter(run_context).step(
            command,
            tool_name="robot_go_to",
            tool_args=grounded_args,
        )
    else:
        step_result = _adapter(run_context).go_to_target(
            target_grounding.value,
            tool_name="robot_go_to",
            tool_args=grounded_args,
        )
    evidence_refs = _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context, evidence_refs=evidence_refs)


def _exec_manipulate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResultMessage:
    grounded, grounding_results = _ground_manipulate_arguments(run_context, arguments)
    if _env_type(run_context) == "AlfredThorEnv":
        step_result = _adapter(run_context).manipulate_with_thor(
            action=str(grounded.get("action", "")),
            tool_name="robot_manipulate",
            tool_args=_with_grounding_metadata(grounded, grounding_results),
        )
        evidence_refs = _write_trace(run_context, step_result)
        return _result_from_step(step_result, run_context, evidence_refs=evidence_refs)
    if grounded.get("action") == "use" and _is_current_subtask_toggle_target(
        run_context,
        str(grounded.get("object", "")),
    ):
        step_result = _adapter(run_context).force_toggle_unique_object_type(
            canonical_command_name(str(grounded.get("object", ""))),
            tool_name="robot_manipulate",
            tool_args=_with_grounding_metadata(grounded, grounding_results),
        )
        evidence_refs = _write_trace(run_context, step_result)
        return _result_from_step(step_result, run_context, evidence_refs=evidence_refs)
    try:
        command = _translator(run_context).manipulate(**grounded)
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_manipulate",
            arguments=grounded,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_manipulate",
        tool_args=_with_grounding_metadata(grounded, grounding_results),
    )
    evidence_refs = _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context, evidence_refs=evidence_refs)


def _exec_verify(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResultMessage:
    state = _adapter(run_context).current_state
    feedback = make_execution_feedback(
        action="verify",
        success=state.won,
        error=None if state.won else "action_not_applicable",
        state_changed=False,
        state_read_status="ok",
    )
    data = feedback.to_model_payload()
    if state.won:
        return _receipt_tool_result(
            name="robot_verify",
            success=True,
            data=data,
        )
    return _receipt_tool_result(
        name="robot_verify",
        success=False,
        data=data,
        failure_reason=feedback.failure_reason,
        is_error=False,
    )


def _receipt_tool_result(
    *,
    name: str,
    success: bool,
    data: dict[str, Any],
    failure_reason: str | None = None,
    is_error: bool | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> ToolResultMessage:
    if evidence_refs:
        data = {**data, "evidence_refs": list(evidence_refs)}
    content = [ContentBlock(text=_json_dumps(data))]
    return ToolResultMessage(
        tool_call_id="",
        name=name,
        content=content,
        is_error=(False if success else bool(is_error)),
        data=data,
    )


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _ground_manipulate_arguments(
    run_context: RunContext,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    grounded = dict(arguments)
    action = grounded.get("action")
    if isinstance(action, str):
        grounded["action"] = action.strip().lower()
    results: dict[str, Any] = {}
    specs = {
        "object": {"object", "toggle"},
        "source_receptacle": {"receptacle"},
        "target_receptacle": {"receptacle", "toggle"},
        "tool_receptacle": {"receptacle", "object"},
    }
    for field, allowed in specs.items():
        value = grounded.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        result = _ground_target(run_context, value, allowed_kinds=allowed)
        grounded[field] = result.value
        results[field] = result
    return grounded, results


def _ground_target(
    run_context: RunContext,
    value: str,
    *,
    allowed_kinds: set[str],
) -> Any:
    candidates = build_grounding_candidates(
        state=_adapter(run_context).current_state,
        subtask=_current_subtask(run_context),
        extra_labels=_extra_virtual_target_candidates(run_context),
    )
    return ground_text(
        value,
        candidates=candidates,
        allowed_kinds=allowed_kinds,
        judge_config_path=_judge_config_path(run_context),
    )


def _extra_virtual_target_candidates(run_context: RunContext) -> list[GroundingCandidate]:
    subtask = _current_subtask(run_context)
    if subtask is None:
        return []
    candidates: list[GroundingCandidate] = []
    toggle = getattr(subtask, "toggle", None)
    if toggle:
        candidates.append(
            GroundingCandidate(canonical_command_name(toggle), "toggle", "subtask_toggle")
        )
        candidates.append(GroundingCandidate(str(toggle), "toggle", "subtask_toggle"))
    return candidates


def _is_current_subtask_toggle_target(run_context: RunContext, value: str) -> bool:
    subtask = _current_subtask(run_context)
    toggle = getattr(subtask, "toggle", None) if subtask is not None else None
    if not toggle:
        return False
    return normalized_key(value, drop_instance=True) == normalized_key(
        canonical_command_name(toggle),
        drop_instance=True,
    )


def _with_grounding_metadata(
    arguments: dict[str, Any],
    results: dict[str, Any],
) -> dict[str, Any]:
    if not results:
        return arguments
    payload = dict(arguments)
    metadata: dict[str, dict[str, str | None]] = {}
    for field, result in results.items():
        metadata[field] = {
            "method": getattr(result, "method", None),
            "matched_label": getattr(result, "matched_label", None),
            "kind": getattr(result, "kind", None),
        }
    payload["grounding"] = metadata
    return payload


def make_alfworld_robot_go_to() -> ToolSpec:
    return ToolSpec(
        name="robot_go_to",
        description=(
            "Move directly to any named ALFWorld target using the navigation "
            "backend. The target may be a movable object, a receptacle, "
            "furniture, an appliance, or a switch/toggle object. Use this "
            "instead of guessing source locations or ALFWorld navigation names."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": (
                        "Task target to move to, such as the object to pick up "
                        "or the place/tool/container needed next."
                    ),
                },
            },
            "required": ["target"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_go_to,
    )


def make_alfworld_robot_manipulate() -> ToolSpec:
    return ToolSpec(
        name="robot_manipulate",
        description=(
            "Execute one high-level ALFWorld manipulation action. For heat, cool, "
            "clean, and slice, do not decompose the task into low-level open/put/"
            "close/use steps; call the abstract action directly when the required "
            "object/tool preconditions are met."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "One ALFWorld action. use is only for switch/toggle objects; "
                        "heat/cool/clean are abstract state-change actions."
                    ),
                    "enum": [
                        "take",
                        "put",
                        "open",
                        "close",
                        "use",
                        "heat",
                        "cool",
                        "clean",
                        "slice",
                    ],
                },
                "object": {
                    "type": "string",
                    "description": (
                        "Object to manipulate. For heat/cool/clean, this object "
                        "should usually be in inventory."
                    ),
                },
                "source_receptacle": {
                    "type": "string",
                    "description": "Receptacle to take the object from.",
                },
                "target_receptacle": {
                    "type": "string",
                    "description": "Receptacle to put/open/close.",
                },
                "tool_receptacle": {
                    "type": "string",
                    "description": (
                        "Tool/receptacle for state-changing actions: microwave for "
                        "heat, fridge for cool, sinkbasin for clean, knife or "
                        "butterknife for slice."
                    ),
                },
            },
            "required": ["action"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_manipulate,
    )


def make_alfworld_robot_verify() -> ToolSpec:
    return ToolSpec(
        name="robot_verify",
        description=(
            "Check whether ALFWorld reports the task as won. "
            "This is the only benchmark success signal."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "expected_done": {
                    "type": "string",
                    "description": ("Optional description of the expected completed condition."),
                },
            },
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_verify,
    )
