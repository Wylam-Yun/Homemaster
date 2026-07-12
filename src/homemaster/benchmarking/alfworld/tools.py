"""ALFWorld-backed robot tools for benchmark runs."""

from __future__ import annotations

import re
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
from homemaster.tools.results import ToolResult
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


def _observation_mode(run_context: RunContext) -> str:
    config = run_context.deps.get("alfworld_config")
    return str(getattr(config, "observation_mode", "visual_eval"))


def _env_type(run_context: RunContext) -> str:
    config = run_context.deps.get("alfworld_config")
    return str(getattr(config, "env_type", "AlfredThorEnv"))


def _result_from_step(step_result: Any, run_context: RunContext) -> ToolResult | ToolResultMessage:
    data = step_result.to_model_visible_data()
    data.pop("admissible_commands", None)
    data["tool_args"] = _model_visible_tool_args(data.get("tool_args", {}))
    outcome = run_context.deps.get("alfworld_episode_outcome")
    if outcome is not None:
        outcome.agent_tool_call_count += 1
        outcome.backend_action_count += int(getattr(step_result, "backend_action_count", 0))
    if step_result.failure_reason in _TERMINAL_EXECUTION_FAILURES:
        data.update(
            {
                "terminal": True,
                "classification": step_result.failure_reason,
                "score_eligible": False,
            }
        )
    if _observation_mode(run_context) == "visual_eval":
        return _visual_tool_result(
            name=step_result.tool_name,
            success=step_result.success,
            data=data,
            failure_reason=step_result.failure_reason,
            frame_path=step_result.state.frame_path,
            is_error=_visual_result_is_error(
                success=step_result.success,
                failure_reason=step_result.failure_reason,
            ),
        )
    return ToolResult(
        success=step_result.success,
        tool_name=step_result.tool_name,
        executor_mode="programmatic",
        data=data,
        failure_reason=step_result.failure_reason,
        retryable=not step_result.state.done,
        summary=step_result.feedback,
    )


def _validation_failure(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    run_context: RunContext,
    error: Exception,
) -> ToolResult | ToolResultMessage:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.update(
        {
            "tool_name": tool_name,
            "tool_args": _model_visible_tool_args(arguments),
            "translated_command": None,
            "feedback": str(error),
        }
    )
    if _observation_mode(run_context) == "visual_eval":
        return _visual_tool_result(
            name=tool_name,
            success=False,
            data=data,
            failure_reason="invalid_tool_arguments",
            frame_path=state.frame_path,
            is_error=True,
        )
    return ToolResult(
        success=False,
        tool_name=tool_name,
        executor_mode="programmatic",
        data=data,
        failure_reason="translator_validation_error",
        retryable=True,
        summary=str(error),
    )


def _write_trace(run_context: RunContext, step_result: Any) -> None:
    trace = run_context.deps.get("alfworld_trace")
    if trace is not None:
        for event in getattr(step_result, "trace_events", ()):
            if isinstance(event, dict):
                trace.write_event(event)
        trace.write_event(step_result.to_trace_event())


def _exec_navigate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    grounded = dict(arguments)
    target = str(arguments.get("target_receptacle", ""))
    target_grounding = _ground_target(
        run_context,
        target,
        allowed_kinds={"receptacle", "toggle"},
    )
    grounded["target_receptacle"] = target_grounding.value
    if _is_current_subtask_toggle_target(run_context, target_grounding.value):
        step_result = _adapter(run_context).virtual_navigate(
            target_grounding.value,
            tool_name="robot_navigate",
            tool_args=_with_grounding_metadata(
                grounded,
                {"target_receptacle": target_grounding},
            ),
        )
        _write_trace(run_context, step_result)
        return _result_from_step(step_result, run_context)
    try:
        command = _translator(run_context).navigate(
            target_receptacle=grounded.get("target_receptacle", ""),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_navigate",
            arguments=grounded,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_navigate",
        tool_args=_with_grounding_metadata(
            grounded,
            {"target_receptacle": target_grounding},
        ),
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context)


def _exec_go_to(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    grounded = dict(arguments)
    target = str(arguments.get("target", ""))
    target_grounding = _ground_target(
        run_context,
        target,
        allowed_kinds={"object", "receptacle", "toggle"},
    )
    grounded["target"] = target_grounding.value
    step_result = _adapter(run_context).go_to_target(
        target_grounding.value,
        tool_name="robot_go_to",
        tool_args=_with_grounding_metadata(
            grounded,
            {"target": target_grounding},
        ),
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context)


def _exec_find_object(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    grounded = dict(arguments)
    target = str(arguments.get("object", ""))
    object_grounding = _ground_target(
        run_context,
        target,
        allowed_kinds={"object", "toggle"},
    )
    grounded["object"] = object_grounding.value
    step_result = _adapter(run_context).find_object(
        object_grounding.value,
        tool_name="robot_find_object",
        tool_args=_with_grounding_metadata(
            grounded,
            {"object": object_grounding},
        ),
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context)


def _exec_manipulate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    grounded, grounding_results = _ground_manipulate_arguments(run_context, arguments)
    if _env_type(run_context) == "AlfredThorEnv":
        step_result = _adapter(run_context).manipulate_with_thor(
            action=str(grounded.get("action", "")),
            tool_name="robot_manipulate",
            tool_args=_with_grounding_metadata(grounded, grounding_results),
        )
        _write_trace(run_context, step_result)
        return _result_from_step(step_result, run_context)
    if grounded.get("action") == "use" and _is_current_subtask_toggle_target(
        run_context,
        str(grounded.get("object", "")),
    ):
        step_result = _adapter(run_context).force_toggle_unique_object_type(
            canonical_command_name(str(grounded.get("object", ""))),
            tool_name="robot_manipulate",
            tool_args=_with_grounding_metadata(grounded, grounding_results),
        )
        _write_trace(run_context, step_result)
        return _result_from_step(step_result, run_context)
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
    _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context)


def _exec_verify(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.pop("admissible_commands", None)
    data.update(
        {
            "tool_name": "robot_verify",
            "tool_args": _model_visible_tool_args(arguments),
            "verified": state.won,
            "expected_done": arguments.get("expected_done"),
        }
    )
    if state.won:
        if _observation_mode(run_context) == "visual_eval":
            return _visual_tool_result(
                name="robot_verify",
                success=True,
                data=data,
                frame_path=state.frame_path,
            )
        return ToolResult(
            success=True,
            tool_name="robot_verify",
            executor_mode="programmatic",
            data=data,
            summary="Environment reports won=true.",
        )
    if _observation_mode(run_context) == "visual_eval":
        return _visual_tool_result(
            name="robot_verify",
            success=False,
            data=data,
            failure_reason="not_complete",
            frame_path=state.frame_path,
            is_error=False,
        )
    return ToolResult(
        success=False,
        tool_name="robot_verify",
        executor_mode="programmatic",
        data=data,
        failure_reason="not_won_yet",
        retryable=not state.done,
        summary="Environment has not reported won=true.",
    )


def _visual_tool_result(
    *,
    name: str,
    success: bool,
    data: dict[str, Any],
    frame_path: str | None,
    failure_reason: str | None = None,
    is_error: bool | None = None,
) -> ToolResultMessage:
    result_data = data
    effective_failure_reason = failure_reason
    if name == "robot_manipulate" and _is_put_projection(data):
        try:
            payload = _put_visible_payload(
                data=data,
                success=success,
                failure_reason=failure_reason,
            )
        except Exception:
            payload = _put_visible_base(data=data, success=False)
            payload.update(
                {
                    "error": "unclassified_execution_failure",
                    "detail": "Execution detail could not be safely projected.",
                    "detail_redacted": True,
                }
            )
            effective_failure_reason = "unclassified_execution_failure"
            result_data = dict(data)
            result_data.update(
                {
                    "terminal": True,
                    "classification": "unclassified_execution_failure",
                    "score_eligible": False,
                }
            )
    else:
        payload = {"success": success}
    if name == "robot_find_object":
        found = _find_object_visible_payload(data)
        if found:
            payload["found_object"] = found
    if name == "robot_go_to":
        target = _go_to_visible_payload(data)
        if target:
            payload["target"] = target
    if not success and "error" not in payload:
        payload["error"] = _visual_error(failure_reason)
    content = [ContentBlock(text=_json_dumps(payload))]
    if frame_path:
        try:
            content.append(ContentBlock.from_image_path(frame_path))
        except OSError:
            pass
    return ToolResultMessage(
        tool_call_id="",
        name=name,
        content=content,
        is_error=(
            _visual_result_is_error(
                success=success,
                failure_reason=effective_failure_reason,
            )
            if is_error is None
            else is_error
        ),
        data=result_data,
    )


def _visual_result_is_error(*, success: bool, failure_reason: str | None) -> bool:
    if success:
        return False
    if failure_reason in {"invalid_action", "not_won_yet", "not_complete"}:
        return False
    return True


def _visual_error(failure_reason: str | None) -> str:
    if failure_reason in {"translator_validation_error", "invalid_tool_arguments"}:
        return "invalid_tool_arguments"
    if failure_reason in {"not_won_yet", "not_complete"}:
        return "not_complete"
    stable_errors = {
        "action_not_applicable",
        "execution_state_uncertain",
        "harness_grounding_failure",
        "harness_navigation_failure",
        "harness_operation_failure",
        "navigation_required",
        "object_not_held",
        "placement_failed",
        "target_not_found",
        "target_not_receptacle",
        "unclassified_execution_failure",
    }
    if failure_reason in stable_errors:
        return str(failure_reason)
    return "action_failed"


def _is_put_projection(data: dict[str, Any]) -> bool:
    if data.get("action") == "put":
        return True
    tool_args = data.get("tool_args")
    return isinstance(tool_args, dict) and tool_args.get("action") == "put"


def _put_visible_base(*, data: dict[str, Any], success: bool) -> dict[str, Any]:
    tool_args = data.get("tool_args")
    args = tool_args if isinstance(tool_args, dict) else {}
    inventory = data.get("inventory")
    if not isinstance(inventory, list):
        inventory = []
    return {
        "success": success,
        "action": "put",
        "object": data.get("object") or args.get("object"),
        "target": data.get("target") or args.get("target_receptacle"),
        "inventory": inventory,
        "object_state": data.get("object_state"),
        "state_changed": bool(data.get("state_changed")),
    }


def _put_visible_payload(
    *,
    data: dict[str, Any],
    success: bool,
    failure_reason: str | None,
) -> dict[str, Any]:
    payload = _put_visible_base(data=data, success=success)
    if not success:
        payload["error"] = _visual_error(failure_reason)
    detail = data.get("detail")
    if detail is not None:
        projected, redacted = _project_execution_detail(str(detail))
        payload["detail"] = projected
        if redacted:
            payload["detail_redacted"] = True
    return payload


def _project_execution_detail(detail: str) -> tuple[str, bool]:
    projected = detail
    patterns = (
        (r"\b[A-Za-z][A-Za-z0-9]*\|[^\s,;)]+", "[REDACTED_OBJECT_ID]"),
        (
            r"\(\s*[+-]?\d+(?:\.\d+)?\s*,\s*[+-]?\d+(?:\.\d+)?\s*,"
            r"\s*[+-]?\d+(?:\.\d+)?\s*\)",
            "[REDACTED_COORDINATES]",
        ),
        (
            r"\bcandidate(?:\s+poses?)?\s*\[[^\]]*\]",
            "[REDACTED_CANDIDATES]",
        ),
        (r"\bexpert(?:\s+(?:target|answer))?[^;]*", "[REDACTED_EXPERT]"),
    )
    for pattern, replacement in patterns:
        projected = re.sub(pattern, replacement, projected, flags=re.IGNORECASE)
    return projected, projected != detail


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _find_object_visible_payload(data: dict[str, Any]) -> dict[str, str]:
    tool_args = data.get("tool_args", {})
    if not isinstance(tool_args, dict):
        return {}
    payload = {}
    for key in ("object", "object_label", "source_receptacle"):
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()
    return payload


def _go_to_visible_payload(data: dict[str, Any]) -> dict[str, str]:
    tool_args = data.get("tool_args", {})
    if not isinstance(tool_args, dict):
        return {}
    payload = {}
    for key in (
        "target",
        "resolved_kind",
        "resolved_label",
        "object_label",
        "source_receptacle",
    ):
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            payload[key] = value.strip()
    return payload


def _model_visible_tool_args(tool_args: Any) -> dict[str, Any]:
    cleaned = _drop_admissible_commands(tool_args)
    if isinstance(cleaned, dict):
        return cleaned
    return {}


def _drop_admissible_commands(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _drop_admissible_commands(item)
            for key, item in value.items()
            if str(key) != "admissible_commands"
        }
    if isinstance(value, list | tuple):
        return [_drop_admissible_commands(item) for item in value]
    return value


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


def make_alfworld_robot_navigate() -> ToolSpec:
    return ToolSpec(
        name="robot_navigate",
        description=(
            "Move to a known place, receptacle, furniture, or appliance. Use "
            "this for named locations and containers that are already known "
            "from the task or recent observations. Do not use this to search "
            "for movable task objects; use robot_find_object when a movable "
            "object's current source location is unknown."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target_receptacle": {
                    "type": "string",
                    "description": (
                        "Destination place/receptacle/furniture/appliance, "
                        "using the environment-style name when known."
                    ),
                },
            },
            "required": ["target_receptacle"],
        },
        executor_mode="programmatic",
        selectable_by_model=False,
        executor=_exec_navigate,
    )


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


def make_alfworld_robot_find_object() -> ToolSpec:
    return ToolSpec(
        name="robot_find_object",
        description=(
            "Automatically locate a movable target object in the current ALFWorld "
            "scene and move to the place that contains it. Use this before take "
            "when the task names an object but you do not know its source "
            "receptacle. The tool returns the canonical object label and source "
            "receptacle to use in later robot_manipulate calls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "object": {
                    "type": "string",
                    "description": (
                        "Movable target object to find, using the task name "
                        "or environment-style object name."
                    ),
                },
            },
            "required": ["object"],
        },
        executor_mode="programmatic",
        selectable_by_model=False,
        executor=_exec_find_object,
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
