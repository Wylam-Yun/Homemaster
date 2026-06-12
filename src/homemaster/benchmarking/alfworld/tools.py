"""ALFWorld-backed robot tools for benchmark runs."""

from __future__ import annotations

from typing import Any

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


def _result_from_step(step_result: Any) -> ToolResult:
    data = step_result.to_model_visible_data()
    data.pop("admissible_commands", None)
    data["tool_args"] = _model_visible_tool_args(data.get("tool_args", {}))
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
) -> ToolResult:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.update({
        "tool_name": tool_name,
        "tool_args": _model_visible_tool_args(arguments),
        "translated_command": None,
        "feedback": str(error),
    })
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


def _exec_observe(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    try:
        command = _translator(run_context).observe(
            mode=arguments.get("mode", "look"),
            target=arguments.get("target"),
        )
    except TranslatorValidationError as exc:
        return _validation_failure(
            tool_name="robot_observe",
            arguments=arguments,
            run_context=run_context,
            error=exc,
        )
    step_result = _adapter(run_context).step(
        command,
        tool_name="robot_observe",
        tool_args=arguments,
    )
    _write_trace(run_context, step_result)
    return _result_from_step(step_result)


def _exec_navigate(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
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
    return _result_from_step(step_result)


def _exec_manipulate(
    *,
    arguments: dict[str, Any],
    run_context: RunContext,
) -> ToolResult:
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
    return _result_from_step(step_result)


def _exec_verify(*, arguments: dict[str, Any], run_context: RunContext) -> ToolResult:
    state = _adapter(run_context).current_state
    data = state.to_model_visible_dict()
    data.update({
        "tool_name": "robot_verify",
        "tool_args": _model_visible_tool_args(arguments),
        "verified": state.won,
        "expected_done": arguments.get("expected_done"),
    })
    if state.won:
        return ToolResult(
            success=True,
            tool_name="robot_verify",
            executor_mode="programmatic",
            data=data,
            summary="Environment reports won=true.",
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


def make_alfworld_robot_observe() -> ToolSpec:
    return ToolSpec(
        name="robot_observe",
        description="Observe the ALFWorld environment using look, inventory, or examine.",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["look", "inventory", "examine"],
                    "description": "Observation command mode.",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Object or receptacle to examine when mode is examine."
                    ),
                },
            },
        },
        executor_mode="programmatic",
        selectable_by_model=True,
        executor=_exec_observe,
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
            "Manipulate ALFWorld objects with take, put, open, close, use, heat, "
            "cool, clean, or slice."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
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
                "object": {"type": "string"},
                "source_receptacle": {"type": "string"},
                "target_receptacle": {"type": "string"},
                "tool_receptacle": {"type": "string"},
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
