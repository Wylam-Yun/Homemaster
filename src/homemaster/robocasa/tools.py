"""RoboCasa tool definitions: discrete primitives over the isolated worker."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from typing import Any

from homemaster.agent.normalized import RunContext
from homemaster.robocasa.worker_client import RoboCasaWorkerError
from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    OutcomeCertainty,
    RegisteredTool,
    ResultImage,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
)

_MUTATING_OPS = {
    "skill",
    "arm",
    "navigate",
    "move_to",
    "move_delta",
    "rotate_pitch",
    "set_gripper",
    "release",
    "scripted_grasp",
    "move_base",
}

_NUM = {"type": "number"}
_VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
_PIX = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}


def _adapter(run_context: RunContext) -> Any:
    adapter = run_context.deps.get("robocasa_env")
    if adapter is None:
        raise RuntimeError("missing run_context.deps['robocasa_env']")
    return adapter


class _RoboCasaExecutor:
    def __init__(self, *, operation: str, tool_name: str) -> None:
        self._operation = operation
        self._tool_name = tool_name

    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        run_context = context.services.get("run_context")
        if not isinstance(run_context, RunContext):
            return _failure(
                "missing_runtime_context",
                "Tool requires a connected run context",
                backend_attempted=False,
            )
        try:
            adapter = _adapter(run_context)
        except RuntimeError:
            return _failure(
                "missing_runtime_context",
                "RoboCasa environment is not bound to this run",
                backend_attempted=False,
            )
        payload = _build_payload(self._operation, arguments)
        if payload is None:
            return _failure(
                "invalid_tool_arguments",
                "RoboCasa tool arguments failed validation",
                backend_attempted=False,
                status=ToolExecutionStatus.INVALID,
            )
        try:
            # Blocking worker I/O runs in a thread so the event loop (and the
            # run deadline/cancellation) stays live during long primitives.
            if self._operation == "observe":
                result = await asyncio.to_thread(
                    adapter.observe, cameras=payload.get("cameras")
                )
            elif self._operation == "check_success":
                result = await asyncio.to_thread(adapter.check_success)
            else:
                result = await asyncio.to_thread(
                    adapter.invoke, self._operation, payload
                )
        except RoboCasaWorkerError as exc:
            return self._worker_error_result(exc, run_context)
        refs = _write_trace(run_context, self._tool_name, self._operation, arguments, result)
        outcome = run_context.deps.get("robocasa_episode_outcome")
        if outcome is not None and self._operation in _MUTATING_OPS:
            outcome.backend_action_count += 1
        return self._result(result, adapter, refs)

    def _worker_error_result(
        self, exc: RoboCasaWorkerError, run_context: RunContext
    ) -> ToolExecutionResult:
        frame = exc.response
        if frame is not None and frame.backend_attempted is False:
            # Rejected before reaching the backend (e.g. invalid_payload) —
            # the environment is untouched.
            return ToolExecutionResult(
                status=ToolExecutionStatus.INVALID,
                text=f"RoboCasa worker rejected the request: {exc}",
                data={"tool_name": self._tool_name},
                error=ToolExecutionError(code="robocasa_worker_rejected", message=str(exc)),
                backend_attempted=False,
            )
        if frame is None:
            # Transport-level failure: the pipe is desynced and the adapter
            # has terminated the worker.  The cell cannot continue — mark the
            # episode terminal as an infrastructure error so the runner does
            # not score it against the agent.
            outcome = run_context.deps.get("robocasa_episode_outcome")
            if outcome is not None:
                outcome.mark_terminal(
                    classification="infrastructure_error",
                    tool_call_id=None,
                    evidence_ref=str(exc)[:200],
                )
        return ToolExecutionResult(
            status=ToolExecutionStatus.OUTCOME_UNKNOWN,
            text=f"RoboCasa worker error: {exc}",
            data={
                "tool_name": self._tool_name,
                "outcome": "unknown",
                "infrastructure": frame is None,
            },
            error=ToolExecutionError(code="robocasa_worker_error", message=str(exc)),
            backend_attempted=self._operation in _MUTATING_OPS or frame is None,
            outcome_certainty=OutcomeCertainty.UNKNOWN,
        )

    def _result(
        self, result: dict[str, Any], adapter: Any, refs: tuple[str, ...]
    ) -> ToolExecutionResult:
        state = result.get("state") if isinstance(result.get("state"), dict) else {}
        images = _result_images(adapter, result)
        if self._operation in _MUTATING_OPS:
            primitive_result = result.get("result")
            errored = bool(result.get("errored"))
            data = {
                "primitive": result.get("primitive"),
                "result": primitive_result,
                "step_index": result.get("step_index"),
                "success": state.get("success"),
                "task_progress": state.get("task_progress"),
                "invalid_action_count": state.get("invalid_action_count"),
                "proprio": state.get("proprio"),
                "evidence_refs": list(refs),
                "backend_attempted": True,
            }
            if errored:
                message = str(
                    (primitive_result or {}).get("error")
                    if isinstance(primitive_result, dict)
                    else primitive_result
                ) or "RoboCasa primitive reported an error"
                return ToolExecutionResult(
                    status=ToolExecutionStatus.FAILURE,
                    text=message,
                    data=data,
                    images=images,
                    evidence_refs=refs,
                    error=ToolExecutionError(code="robocasa_primitive_error", message=message),
                    backend_attempted=True,
                )
            return ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                text=_summarize(self._operation, primitive_result),
                data=data,
                images=images,
                evidence_refs=refs,
                backend_attempted=True,
            )
        data = {
            "state": state,
            "result": result.get("result"),
            "success": state.get("success", result.get("success")),
            "task_progress": result.get("task_progress", state.get("task_progress")),
            "evidence_refs": list(refs),
        }
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=_summarize(self._operation, result),
            data=data,
            images=images,
            evidence_refs=refs,
            backend_attempted=False,
        )


def _build_payload(operation: str, arguments: Mapping[str, object]) -> dict[str, Any] | None:
    args = dict(arguments)
    try:
        if operation == "observe":
            cameras = args.get("cameras")
            if cameras is None:
                return {}
            if not isinstance(cameras, list) or not all(isinstance(c, str) for c in cameras):
                return None
            return {"cameras": list(cameras)}
        if operation == "navigate":
            if "x" in args and "y" in args:
                return {
                    "xy": [float(args["x"]), float(args["y"])],
                    **({"tol": float(args["tol"])} if "tol" in args else {}),
                    **({"gripper": args["gripper"]} if "gripper" in args else {}),
                }
            return None
        if operation in {"move_to", "scripted_grasp"}:
            xyz = args.get("xyz")
            if not _is_vec3(xyz):
                return None
            return {"xyz": [float(v) for v in xyz], **_opt(args, _OPT_FLOATS | {"gripper"})}
        if operation == "move_delta":
            dxyz = args.get("dxyz")
            if not _is_vec3(dxyz):
                return None
            return {
                "dxyz": [float(v) for v in dxyz],
                **_opt(args, _OPT_FLOATS | {"gripper"}),
            }
        if operation in {"rotate_pitch", "set_gripper", "release", "move_base", "check_success"}:
            return _opt(args, _OPT_FLOATS | _OPT_INTS | {"gripper"})
        if operation == "back_project":
            pixels = args.get("pixels")
            if not isinstance(pixels, list) or not pixels:
                return None
            out = {"pixels": pixels}
            if "camera" in args:
                out["camera"] = str(args["camera"])
            return out
        if operation == "world_map":
            return {
                key: args[key]
                for key in ("z_min", "z_max", "x_range", "y_range", "min_cluster_size", "camera")
                if key in args
            }
        return args
    except (TypeError, ValueError):
        return None


_OPT_FLOATS = {
    "tol",
    "step_clip",
    "target_pitch",
    "approach_z",
    "grasp_z_offset",
    "forward",
    "lateral",
    "turn",
}
_OPT_INTS = {"max_steps", "n", "steps"}


def _opt(args: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    return {key: args[key] for key in keys if key in args}


def _is_vec3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
    )


def _result_images(adapter: Any, result: dict[str, Any]) -> tuple[ResultImage, ...]:
    images: list[ResultImage] = []
    for artifact in adapter.image_artifacts(result):
        content = artifact["content"]
        images.append(
            ResultImage(
                media_type="image/png",
                data_base64=base64.b64encode(content).decode("ascii"),
                content_sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return tuple(images)


def _write_trace(
    run_context: RunContext,
    tool_name: str,
    operation: str,
    arguments: Mapping[str, object],
    result: dict[str, Any],
) -> tuple[str, ...]:
    trace = run_context.deps.get("robocasa_trace")
    if trace is None:
        return ()
    return (
        trace.write_event(
            {
                "tool_name": tool_name,
                "operation": operation,
                "tool_args": dict(arguments),
                "result": result.get("result"),
                "step_index": result.get("step_index"),
                "success": (result.get("state") or {}).get("success"),
                "task_progress": (result.get("state") or {}).get("task_progress")
                or result.get("task_progress"),
            }
        ),
    )


def _summarize(operation: str, result: Any) -> str:
    if operation in {"skill", "arm"} and isinstance(result, dict):
        return (
            f"rldx {operation}: status={result.get('status')} "
            f"chunks={result.get('chunks')} steps_applied={result.get('steps_applied')} "
            f"grasped={result.get('grasped')}"
        )
    if operation == "observe":
        return "RoboCasa observation captured."
    if operation == "check_success":
        if isinstance(result, dict):
            return f"success={result.get('success')} task_progress={result.get('task_progress')}"
        return "check_success completed."
    if isinstance(result, dict):
        return str(result.get("result") or result.get("primitive") or f"{operation} done")[:400]
    return f"{operation} done."


def _failure(
    code: str,
    message: str,
    *,
    backend_attempted: bool,
    status: ToolExecutionStatus = ToolExecutionStatus.FAILURE,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=status,
        text=message,
        error=ToolExecutionError(code=code, message=message),
        backend_attempted=backend_attempted,
    )


def _registered(
    name: str,
    description: str,
    schema: Mapping[str, object],
    operation: str,
    *,
    mutating: bool,
) -> RegisteredTool:
    return RegisteredTool(
        definition=ToolDefinition(
            internal_id=f"homemaster.{name}.v1",
            model_alias=name,
            description=description,
            input_schema=dict(schema),
            output_schema={"type": "object"},
            verification_policy=VerificationPolicy(),
            provenance=ToolProvenance(source="homemaster.robocasa", reference=f"robocasa.{name}"),
            version="3.5.0",
            state_effects=("backend.advance",) if mutating else (),
            # Mutating ops serialize on the one shared worker runtime; read
            # tools may run in parallel.  ``_configured_tool`` recomputes
            # these from state_effects at registration time.
            concurrency_policy=(
                ConcurrencyPolicy.RESOURCE_KEY if mutating else ConcurrencyPolicy.PARALLEL
            ),
            resource_key="robocasa:backend" if mutating else None,
        ),
        executor=_RoboCasaExecutor(operation=operation, tool_name=name),
    )


def make_robocasa_observe() -> RegisteredTool:
    return _registered(
        "robocasa_observe",
        "Capture the current camera views and proprioceptive/task state.",
        {
            "type": "object",
            "properties": {
                "cameras": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["agentview", "wrist", "navview"]},
                }
            },
        },
        "observe",
        mutating=False,
    )


def make_robocasa_skill() -> RegisteredTool:
    return _registered(
        "robocasa_skill",
        "Run the RLDX-1 VLA closed-loop skill on the live task language "
        "(full-body, may move the base). The workhorse manipulation tool.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "skill",
        mutating=True,
    )


def make_robocasa_arm() -> RegisteredTool:
    return _registered(
        "robocasa_arm",
        "Run the RLDX-1 VLA skill with the base clamped (arm-only fine "
        "adjustment). Prefer this when already positioned near the target.",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "arm",
        mutating=True,
    )


def make_robocasa_navigate() -> RegisteredTool:
    return _registered(
        "robocasa_navigate",
        "Drive the mobile base to a world (x, y) position.",
        {
            "type": "object",
            "properties": {
                "x": _NUM,
                "y": _NUM,
                "tol": _NUM,
                "gripper": {"type": "string", "enum": ["hold", "open", "close"]},
            },
            "required": ["x", "y"],
        },
        "navigate",
        mutating=True,
    )


def make_robocasa_move_to() -> RegisteredTool:
    return _registered(
        "robocasa_move_to",
        "Move the end-effector to a world [x, y, z] position (OSC arm control).",
        {
            "type": "object",
            "properties": {
                "xyz": _VEC3,
                "gripper": {"type": "string", "enum": ["hold", "open", "close"]},
            },
            "required": ["xyz"],
        },
        "move_to",
        mutating=True,
    )


def make_robocasa_move_delta() -> RegisteredTool:
    return _registered(
        "robocasa_move_delta",
        "Move the end-effector by a [dx, dy, dz] offset (OSC arm control).",
        {
            "type": "object",
            "properties": {
                "dxyz": _VEC3,
                "gripper": {"type": "string", "enum": ["hold", "open", "close"]},
            },
            "required": ["dxyz"],
        },
        "move_delta",
        mutating=True,
    )


def make_robocasa_rotate_pitch() -> RegisteredTool:
    return _registered(
        "robocasa_rotate_pitch",
        "Rotate the wrist pitch (for example to aim the gripper at a handle).",
        {
            "type": "object",
            "properties": {"target_pitch": _NUM, "gripper": _NUM, "n": {"type": "integer"}},
        },
        "rotate_pitch",
        mutating=True,
    )


def make_robocasa_set_gripper() -> RegisteredTool:
    return _registered(
        "robocasa_set_gripper",
        "Set the gripper aperture (0 closed, 1 open).",
        {
            "type": "object",
            "properties": {"gripper": _NUM, "steps": {"type": "integer"}},
        },
        "set_gripper",
        mutating=True,
    )


def make_robocasa_release() -> RegisteredTool:
    return _registered(
        "robocasa_release",
        "Fully open the gripper to release a held object.",
        {
            "type": "object",
            "properties": {"steps": {"type": "integer"}},
        },
        "release",
        mutating=True,
    )


def make_robocasa_scripted_grasp() -> RegisteredTool:
    return _registered(
        "robocasa_scripted_grasp",
        "Run the scripted approach-and-grasp primitive at a world [x, y, z] "
        "(fallback when the VLA skill fails to grasp).",
        {
            "type": "object",
            "properties": {
                "xyz": _VEC3,
                "approach_z": _NUM,
                "grasp_z_offset": _NUM,
                "step_clip": _NUM,
            },
            "required": ["xyz"],
        },
        "scripted_grasp",
        mutating=True,
    )


def make_robocasa_move_base() -> RegisteredTool:
    return _registered(
        "robocasa_move_base",
        "Nudge the base by a small forward/lateral/turn amount.",
        {
            "type": "object",
            "properties": {
                "forward": _NUM,
                "lateral": _NUM,
                "turn": _NUM,
                "steps": {"type": "integer"},
                "gripper": {"type": "string", "enum": ["hold", "open", "close"]},
            },
        },
        "move_base",
        mutating=True,
    )


def make_robocasa_back_project() -> RegisteredTool:
    return _registered(
        "robocasa_back_project",
        "Back-project image pixels [[row, col], ...] to world XYZ coordinates "
        "to localize objects seen in the camera views.",
        {
            "type": "object",
            "properties": {
                "pixels": {"type": "array", "items": _PIX, "minItems": 1},
                "camera": {"type": "string", "enum": ["agentview", "wrist", "navview"]},
            },
            "required": ["pixels"],
        },
        "back_project",
        mutating=False,
    )


def make_robocasa_world_map() -> RegisteredTool:
    return _registered(
        "robocasa_world_map",
        "Query clustered world-map points in a height window to find "
        "object/receptacle positions.",
        {
            "type": "object",
            "properties": {
                "z_min": _NUM,
                "z_max": _NUM,
                "x_range": {"type": "array", "items": _NUM, "minItems": 2, "maxItems": 2},
                "y_range": {"type": "array", "items": _NUM, "minItems": 2, "maxItems": 2},
                "min_cluster_size": {"type": "integer"},
                "camera": {"type": "string", "enum": ["agentview", "wrist", "navview"]},
            },
        },
        "world_map",
        mutating=False,
    )


def make_robocasa_verify() -> RegisteredTool:
    return _registered(
        "robocasa_verify",
        "Ask the environment whether the task is complete (authoritative "
        "success check).",
        {"type": "object", "properties": {}, "additionalProperties": False},
        "check_success",
        mutating=False,
    )


def build_robocasa_tools() -> tuple[RegisteredTool, ...]:
    return (
        make_robocasa_observe(),
        make_robocasa_skill(),
        make_robocasa_arm(),
        make_robocasa_navigate(),
        make_robocasa_move_to(),
        make_robocasa_move_delta(),
        make_robocasa_rotate_pitch(),
        make_robocasa_set_gripper(),
        make_robocasa_release(),
        make_robocasa_scripted_grasp(),
        make_robocasa_move_base(),
        make_robocasa_back_project(),
        make_robocasa_world_map(),
        make_robocasa_verify(),
    )


__all__ = [
    "build_robocasa_tools",
    "make_robocasa_arm",
    "make_robocasa_back_project",
    "make_robocasa_move_base",
    "make_robocasa_move_delta",
    "make_robocasa_move_to",
    "make_robocasa_navigate",
    "make_robocasa_observe",
    "make_robocasa_release",
    "make_robocasa_rotate_pitch",
    "make_robocasa_scripted_grasp",
    "make_robocasa_set_gripper",
    "make_robocasa_skill",
    "make_robocasa_verify",
    "make_robocasa_world_map",
]
