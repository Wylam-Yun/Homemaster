"""ALFWorld-backed robot tools for benchmark runs."""

from __future__ import annotations

from typing import Any

from homemaster.agent.messages import ContentBlock, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.translator import (
    AlfworldCommandTranslator,
    TranslatorValidationError,
)
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


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


def _observation_mode(run_context: RunContext) -> str:
    config = run_context.deps.get("alfworld_config")
    return str(getattr(config, "observation_mode", "visual_eval"))


def _result_from_step(step_result: Any, run_context: RunContext) -> ToolResult | ToolResultMessage:
    data = step_result.to_model_visible_data()
    data.pop("admissible_commands", None)
    data["tool_args"] = _model_visible_tool_args(data.get("tool_args", {}))
    if _observation_mode(run_context) == "visual_eval":
        return _visual_tool_result(
            name=step_result.tool_name,
            success=step_result.success,
            data=data,
            failure_reason=step_result.failure_reason,
            frame_path=step_result.state.frame_path,
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
    data.update({
        "tool_name": tool_name,
        "tool_args": _model_visible_tool_args(arguments),
        "translated_command": None,
        "feedback": str(error),
    })
    if _observation_mode(run_context) == "visual_eval":
        return _visual_tool_result(
            name=tool_name,
            success=False,
            data=data,
            failure_reason="invalid_tool_arguments",
            frame_path=state.frame_path,
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
        trace.write_event(step_result.to_trace_event())


def _exec_inspect_view(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.pop("admissible_commands", None)
    data.update({
        "tool_name": "robot_inspect_view",
        "tool_args": _model_visible_tool_args(arguments),
        "focus": arguments.get("focus"),
        "non_step_observation": True,
    })
    if _observation_mode(run_context) == "visual_eval":
        return _visual_tool_result(
            name="robot_inspect_view",
            success=True,
            data=data,
            frame_path=state.frame_path,
        )
    return ToolResult(
        success=True,
        tool_name="robot_inspect_view",
        executor_mode="programmatic",
        data=data,
        summary="Returned the current visual frame without stepping the environment.",
    )


def _exec_navigate(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult | ToolResultMessage:
    try:
        command = _translator(run_context).navigate(
            target_receptacle=arguments.get("target_receptacle", ""),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_navigate",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_navigate",
        tool_args=arguments,
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context)


def _exec_manipulate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult | ToolResultMessage:
    try:
        command = _translator(run_context).manipulate(**arguments)
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_manipulate",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_manipulate",
        tool_args=arguments,
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result, run_context)


def _exec_verify(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult | ToolResultMessage:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.pop("admissible_commands", None)
    data.update({
        "tool_name": "robot_verify",
        "tool_args": _model_visible_tool_args(arguments),
        "verified": state.won,
        "expected_done": arguments.get("expected_done"),
    })
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
) -> ToolResultMessage:
    payload: dict[str, Any] = {"success": success}
    if not success:
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
        is_error=not success,
        data=data,
    )


def _visual_error(failure_reason: str | None) -> str:
    if failure_reason in {"translator_validation_error", "invalid_tool_arguments"}:
        return "invalid_tool_arguments"
    if failure_reason in {"not_won_yet", "not_complete"}:
        return "not_complete"
    return "action_failed"


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


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


def make_alfworld_robot_inspect_view() -> ToolSpec:
    return ToolSpec(
        name="robot_inspect_view",
        description=(
            "Return the current visual frame without executing an ALFWorld "
            "environment action or consuming an environment step. Use only when "
            "the latest action image is unclear or you need to re-check the "
            "current view before choosing the next physical action."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": (
                        "Optional short description of what to inspect in the "
                        "current view, such as sofa surface or held object."
                    ),
                },
            },
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_inspect_view,
    )


def make_alfworld_robot_navigate() -> ToolSpec:
    return ToolSpec(
        name="robot_navigate",
        description="Move to an ALFWorld receptacle using its environment name.",
        input_schema={
            "type": "object",
            "properties": {
                "target_receptacle": {
                    "type": "string",
                    "description": "Destination receptacle, such as countertop 1.",
                },
            },
            "required": ["target_receptacle"],
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_navigate,
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
                    "description": (
                        "Optional description of the expected completed condition."
                    ),
                },
            },
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_verify,
    )
