"""Shared leaf module for the ALFWorld environment adapter.

Holds the module-level constants, result types, env constructors, and helper
functions used by the AlfworldEnvAdapter mixins. Kept dependency-free of the
facade so mixins only import this leaf and never each other.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from homemaster.alfworld.benchmark.scene_execution import (
    AgentPose,
    ExternalActionResult,
    ExternalRead,
    PoseContext,
    ReadStatus,
    SceneObjectIndex,
    SceneObjectRef,
)
from homemaster.alfworld.gateway import (
    CleanupResult,
    ExternalActionRequest,
    ExternalEventRead,
)
from homemaster.alfworld.pose_snapshot import OraclePose, SceneObjectScanInput
from homemaster.alfworld.reset_transaction import external_event_evidence_payload
from homemaster.alfworld.trial_selection import TrialSelectionEntry
from homemaster.alfworld.types import (
    AlfworldBenchmarkConfig,
    AlfworldExecutionFeedback,
    make_execution_feedback,
)
from homemaster.alfworld.types import (
    AlfworldEnvState as AlfworldEnvState,
)

if TYPE_CHECKING:
    from homemaster.alfworld.benchmark.adapter import AlfworldEnvAdapter


class _NavigationResult(SimpleNamespace):
    success: bool
    feedback: str
    failure_reason: str | None
    budget_stop_reason: str | None
    backend_action_count: int
    event: Any | None
    actual_pose: AgentPose | None
    reachable: list[dict[str, float]]
    trace_events: tuple[dict[str, Any], ...]
    context_id: str | None
    locked_candidates_hash: str | None
    candidates_attempted: int


class _ObjectLocationResult(SimpleNamespace):
    success: bool
    feedback: str
    object_label: str | None
    source_receptacle: str | None
    object_type: str | None


class _TargetResolutionResult(SimpleNamespace):
    success: bool
    feedback: str
    resolved_kind: str | None
    resolved_label: str | None
    object_label: str | None
    source_receptacle: str | None
    object_type: str | None
    object_id: str | None


class _ManipulationResolutionResult(SimpleNamespace):
    success: bool
    feedback: str
    object_id: str | None = None
    object_label: str | None = None
    object_type: str | None = None


class _ThorActionResult(SimpleNamespace):
    success: bool
    feedback: str
    backend_actions: list[str]
    resolved: dict[str, Any]


_OBJECT_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "basin": ("sinkbasin", "bathtubbasin"),
    "counter": ("countertop",),
    "handsoap": ("soapbar",),
    "handsoapbar": ("soapbar",),
    "microwaveoven": ("microwave",),
    "refrigerator": ("fridge",),
    "sink": ("sinkbasin",),
    "soap": ("soapbar",),
    "towelholder": ("handtowelholder",),
}

_DEFAULT_NAVIGATION_MAX_CANDIDATES = 65
_DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS = 66
_DEFAULT_NAVIGATION_MAX_ELAPSED_MS = 34_804.0
_DEFAULT_PUT_MAX_CANDIDATES = 9
_DEFAULT_PUT_MAX_BACKEND_ACTIONS = 17
_DEFAULT_PUT_MAX_ELAPSED_MS = 5_669.0


def split_to_train_eval(split: str) -> str:
    mapping = {
        "train": "train",
        "valid_seen": "eval_in_distribution",
        "valid_unseen": "eval_out_of_distribution",
    }
    if split not in mapping:
        raise ValueError(f"unsupported ALFWorld split: {split}")
    return mapping[split]


@contextlib.contextmanager
def _prepend_sys_path(path: Path) -> Iterator[None]:
    value = str(path)
    added = value not in sys.path
    if added:
        sys.path.insert(0, value)
    try:
        yield
    finally:
        if added:
            sys.path.remove(value)


def load_alfworld_yaml(
    path: Path,
    *,
    data_root: Path | None = None,
) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml is required for ALFWorld benchmark config loading; "
            "install HomeMaster with the alfworld extra"
        ) from exc

    with path.open("r", encoding="utf-8") as reader:
        payload = yaml.safe_load(reader)
    if not isinstance(payload, dict):
        raise ValueError(f"ALFWorld config must be a mapping: {path}")
    if data_root is not None:
        return _replace_alfworld_data(payload, data_root.resolve())
    return payload


def _replace_alfworld_data(value: Any, data_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _replace_alfworld_data(item, data_root) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_alfworld_data(item, data_root) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_alfworld_data(item, data_root) for item in value)
    if isinstance(value, str):
        for marker in ("${ALFWORLD_DATA}", "$ALFWORLD_DATA"):
            if value == marker:
                return str(data_root)
            prefix = marker + "/"
            if value.startswith(prefix):
                return str(data_root / value[len(prefix) :])
    return value


def _alfworld_import_scope(config: AlfworldBenchmarkConfig):
    if config.use_installed_alfworld:
        return contextlib.nullcontext()
    return _prepend_sys_path(config.alfworld_root)


def build_alfworld_batch_env(config: AlfworldBenchmarkConfig) -> Any:
    with _alfworld_import_scope(config):
        from alfworld.agents.environment import get_environment

        payload = load_alfworld_yaml(
            config.alfworld_config,
            data_root=config.data_root,
        )
        if split_to_train_eval(config.split) != "train":
            dataset = payload.setdefault("dataset", {})
            configured_limit = dataset.get("num_eval_games", -1)
            if not isinstance(configured_limit, int) or configured_limit <= 0:
                dataset["num_eval_games"] = max(1, config.episodes)
            else:
                dataset["num_eval_games"] = min(configured_limit, max(1, config.episodes))
        env_cls = get_environment(config.env_type)
        alfred_env = env_cls(payload, train_eval=split_to_train_eval(config.split))
        env = alfred_env.init_env(batch_size=1)
        if hasattr(env, "seed"):
            env.seed(config.seed)
        return env


def build_alfworld_batch_env_with_first_trial(
    config: AlfworldBenchmarkConfig,
    *,
    first_trial_path: Path,
) -> Any:
    """Build a batch env whose reset() loads `first_trial_path` first.

    Used by the long-horizon taskset runner: the first subtask needs a real
    scene load (reset), and subsequent subtasks swap goals via advance_goal
    without resetting. We pin json_file_list to [first_trial_path] so the first
    reset loads that trial's scene + object_poses.
    """
    with _alfworld_import_scope(config):
        from alfworld.agents.environment import get_environment

        payload = load_alfworld_yaml(
            config.alfworld_config,
            data_root=config.data_root,
        )
        env_cls = get_environment(config.env_type)
        alfred_env = env_cls(payload, train_eval=split_to_train_eval(config.split))
        # Pin the first trial before init_env triggers any file collection.
        first_trial_str = str(first_trial_path)
        if hasattr(alfred_env, "json_file_list"):
            alfred_env.json_file_list = [first_trial_str]
        if hasattr(alfred_env, "num_games"):
            alfred_env.num_games = 1
        env = alfred_env.init_env(batch_size=1)
        # init_env may have re-collected files; re-pin defensively.
        if hasattr(env, "json_file_list"):
            env.json_file_list = [first_trial_str]
        if hasattr(env, "seed"):
            env.seed(config.seed)
        return env


def _is_invalid_feedback(observation: str) -> bool:
    normalized = observation.strip().lower().rstrip(".")
    return normalized == "nothing happens"


def _safe_navigation_feedback(error: str | None, target: str) -> str:
    label = target.strip() or "the requested target"
    templates = {
        "target_not_found": f"{label} is not a supported target.",
        "target_not_visible": f"{label} is not visible in the current view.",
        "object_already_held": f"{label} is already held.",
        "oracle_anchor_unresolved": f"No verified navigation anchor is available for {label}.",
        "oracle_pose_missing": f"No verified navigation pose is available for {label}.",
        "oracle_pose_malformed": f"The verified navigation pose for {label} is invalid.",
        "oracle_navigation_failed": f"Navigation to {label} was rejected.",
        "oracle_pose_mismatch": f"Navigation to {label} did not reach the verified pose.",
        "oracle_target_not_visible": f"{label} was not visible after navigation.",
        "execution_state_uncertain": "The current execution state could not be verified.",
    }
    return templates.get(error, "Navigation could not be completed.")


def _navigation_execution_feedback(
    error: str | None,
    target: str,
    *,
    success: bool = False,
    state_changed: bool | None = False,
) -> AlfworldExecutionFeedback:
    target_state = None
    target_state_status = "not_applicable"
    if success:
        target_state = "visible"
        target_state_status = "ok"
    elif error in {"target_not_visible", "oracle_target_not_visible"}:
        target_state = "not_visible"
        target_state_status = "ok"
    return make_execution_feedback(
        action="navigate",
        success=success,
        error=error,
        target_label=target,
        target_state=target_state,
        target_state_status=target_state_status,
        state_changed=state_changed,
        state_read_status="ok" if state_changed is not None else "not_applicable",
    )


def _execution_feedback(
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    success: bool,
    failure_reason: str | None,
) -> AlfworldExecutionFeedback:
    raw_action = str(tool_args.get("action") or "").strip().lower()
    allowed_actions = {
        "take",
        "open",
        "close",
        "put",
        "use",
        "slice",
        "heat",
        "cool",
        "clean",
        "verify",
    }
    if raw_action in allowed_actions:
        action = raw_action
    elif tool_name == "robot_go_to":
        action = "navigate"
    else:
        action = "verify"
    error_map = {
        "env_error": "execution_state_uncertain",
        "invalid_action": "invalid_tool_arguments",
        "navigation_target_not_visible": "target_not_visible",
        "object_not_visible": "target_not_visible",
        "object_not_found": "target_not_found",
        "ambiguous_grounding": "unclassified_execution_failure",
        "harness_navigation_failure": "oracle_navigation_failed",
    }
    closed_errors = {
        "invalid_tool_arguments",
        "unknown_tool",
        "target_not_found",
        "target_not_visible",
        "object_already_held",
        "object_not_held",
        "target_not_receptacle",
        "target_closed",
        "action_not_applicable",
        "navigation_required",
        "oracle_anchor_unresolved",
        "oracle_pose_missing",
        "oracle_pose_malformed",
        "oracle_navigation_failed",
        "oracle_pose_mismatch",
        "oracle_target_not_visible",
        "harness_operation_failure",
        "execution_state_uncertain",
        "unclassified_execution_failure",
    }
    mapped = error_map.get(failure_reason or "", failure_reason)
    if mapped not in closed_errors:
        mapped = "unclassified_execution_failure"
    object_label = str(tool_args.get("object") or "").strip() or None
    target_label = (
        str(
            tool_args.get("target")
            or tool_args.get("target_receptacle")
            or tool_args.get("tool_receptacle")
            or ""
        ).strip()
        or None
    )
    return make_execution_feedback(
        action=action,
        success=success,
        error=None if success else mapped,
        object_label=object_label,
        target_label=target_label,
    )


def _build_set_task_args(thor_env: Any) -> SimpleNamespace:
    """Build the args namespace ThorEnv.set_task expects (reward_config path).

    Mirrors alfred_thor_env.py:106-112 which sets args.reward_config to the
    bundled config/rewards.json under the alfworld.agents package.
    """
    import alfworld.agents

    args = SimpleNamespace()
    args.reward_config = os.path.join(alfworld.agents.__path__[0], "config", "rewards.json")
    return args


def _first(value: Any, default: Any) -> Any:
    if isinstance(value, list | tuple) and value:
        return value[0]
    return default


def _first_info(infos: dict[str, Any], key: str, default: Any) -> Any:
    value = infos.get(key, default)
    if isinstance(value, list | tuple) and value:
        return value[0]
    return value


def _episode_id_from_gamefile(gamefile: str, prefix: str) -> str:
    path = Path(gamefile)
    parts = path.parts
    if len(parts) >= 3:
        return "/".join(parts[-3:-1])
    try:
        payload = json.loads(gamefile)
        if isinstance(payload, str):
            return payload
    except ValueError:
        pass
    return f"{prefix}/unknown"


def _batch_command_for_action(action: str, tool_args: dict[str, Any]) -> str:
    object_label = str(tool_args.get("object") or "").strip()
    source = str(tool_args.get("source_receptacle") or "").strip()
    target = str(tool_args.get("target_receptacle") or "").strip()
    if action == "take" and object_label and source:
        return f"take {object_label} from {source}"
    if action == "put" and object_label and target:
        return f"move {object_label} to {target}"
    subject = target or object_label
    return f"{action} {subject}".strip()


def _model_visible_tool_args(tool_args: dict[str, Any]) -> dict[str, Any]:
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


def _execute_manipulation(
    thor_env: Any,
    action: str,
    tool_args: dict[str, Any],
    *,
    last_go_to_object_id: str | None = None,
) -> _ThorActionResult:
    if action == "take":
        target = _resolve_manipulation_object(
            thor_env,
            _required_tool_arg(tool_args, "object"),
            require_pickupable=True,
            preferred_object_id=last_go_to_object_id,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"object_resolution": target})
        event = _thor_step(
            thor_env,
            {
                "action": "PickupObject",
                "objectId": target.object_id,
                "forceAction": True,
            },
        )
        return _single_action_result(
            event,
            success_feedback=f"Picked up {target.object_type or 'object'}.",
            failure_feedback=f"Could not pick up {target.object_type or 'object'}.",
            backend_action="PickupObject",
            resolved={"object_resolution": target},
        )

    if action == "put":
        held = _held_object(thor_env)
        if held is None:
            return _failed_thor_action("No object is currently held.", {})
        target = _resolve_manipulation_object(
            thor_env,
            _required_tool_arg(tool_args, "target_receptacle"),
            require_receptacle=True,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"target_resolution": target})
        event = _thor_step(
            thor_env,
            {
                "action": "PutObject",
                "objectId": str(held.get("objectId", "")),
                "receptacleObjectId": target.object_id,
                "forceAction": True,
                "placeStationary": True,
            },
        )
        return _single_action_result(
            event,
            success_feedback=f"Placed the held object on/in {target.object_type or 'target'}.",
            failure_feedback=(
                f"Could not place the held object on/in {target.object_type or 'target'}."
            ),
            backend_action="PutObject",
            resolved={
                "held_object_id": str(held.get("objectId", "")) or None,
                "target_resolution": target,
            },
        )

    if action in {"open", "close"}:
        target = _resolve_manipulation_object(
            thor_env,
            _first_tool_arg(tool_args, "target_receptacle", "object"),
            require_openable=True,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"target_resolution": target})
        obj = _object_by_id(thor_env, target.object_id)
        want_open = action == "open"
        if obj is not None and obj.get("isOpen") is want_open:
            state_text = "open" if want_open else "closed"
            return _ThorActionResult(
                success=True,
                feedback=f"{target.object_type or 'target'} is already {state_text}.",
                backend_actions=[],
                resolved=_resolved_payload({"target_resolution": target}),
            )
        backend = "OpenObject" if want_open else "CloseObject"
        event = _thor_step(
            thor_env,
            {
                "action": backend,
                "objectId": target.object_id,
                "forceAction": True,
            },
        )
        return _single_action_result(
            event,
            success_feedback=f"{backend} succeeded for {target.object_type or 'target'}.",
            failure_feedback=f"Could not {action} {target.object_type or 'target'}.",
            backend_action=backend,
            resolved={"target_resolution": target},
        )

    if action == "use":
        target = _resolve_manipulation_object(
            thor_env,
            _first_tool_arg(tool_args, "object", "target_receptacle"),
            require_toggleable=True,
        )
        if not target.success:
            return _failed_thor_action(target.feedback, {"object_resolution": target})
        obj = _object_by_id(thor_env, target.object_id)
        if obj is not None and obj.get("isToggled") is True:
            return _ThorActionResult(
                success=True,
                feedback=f"{target.object_type or 'target'} is already on.",
                backend_actions=[],
                resolved=_resolved_payload({"object_resolution": target}),
            )
        event = _thor_step(
            thor_env,
            {
                "action": "ToggleObjectOn",
                "objectId": target.object_id,
                "forceAction": True,
            },
        )
        return _single_action_result(
            event,
            success_feedback=f"Turned on {target.object_type or 'target'}.",
            failure_feedback=f"Could not turn on {target.object_type or 'target'}.",
            backend_action="ToggleObjectOn",
            resolved={"object_resolution": target},
        )

    if action == "heat":
        return _execute_heat(thor_env, tool_args)
    if action == "cool":
        return _execute_cool(thor_env, tool_args)
    if action == "clean":
        return _execute_clean(thor_env, tool_args)
    if action == "slice":
        return _failed_thor_action(
            "slice is not implemented in the THOR manipulation backend yet.",
            {},
        )
    return _failed_thor_action(f"unsupported manipulation action: {action}", {})


def _execute_heat(thor_env: Any, tool_args: dict[str, Any]) -> _ThorActionResult:
    held = _held_object(thor_env)
    if held is None:
        return _failed_thor_action("No object is currently held for heat.", {})
    tool = _resolve_manipulation_object(
        thor_env,
        _first_tool_arg(
            tool_args,
            "tool_receptacle",
            "target_receptacle",
            default="microwave",
        ),
        require_receptacle=True,
    )
    if not tool.success:
        return _failed_thor_action(tool.feedback, {"tool_resolution": tool})
    held_id = str(held.get("objectId", ""))
    actions = [
        {"action": "ToggleObjectOff", "objectId": tool.object_id, "forceAction": True},
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {
            "action": "PutObject",
            "objectId": held_id,
            "receptacleObjectId": tool.object_id,
            "forceAction": True,
            "placeStationary": True,
        },
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "ToggleObjectOn", "objectId": tool.object_id, "forceAction": True},
        {"action": "ToggleObjectOff", "objectId": tool.object_id, "forceAction": True},
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "PickupObject", "objectId": held_id, "forceAction": True},
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
    ]
    return _run_thor_macro(
        thor_env,
        actions,
        success_feedback="Heated the held object.",
        resolved={"held_object_id": held_id, "tool_resolution": tool},
    )


def _execute_cool(thor_env: Any, tool_args: dict[str, Any]) -> _ThorActionResult:
    held = _held_object(thor_env)
    if held is None:
        return _failed_thor_action("No object is currently held for cool.", {})
    tool = _resolve_manipulation_object(
        thor_env,
        _first_tool_arg(
            tool_args,
            "tool_receptacle",
            "target_receptacle",
            default="fridge",
        ),
        require_receptacle=True,
    )
    if not tool.success:
        return _failed_thor_action(tool.feedback, {"tool_resolution": tool})
    held_id = str(held.get("objectId", ""))
    actions = [
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {
            "action": "PutObject",
            "objectId": held_id,
            "receptacleObjectId": tool.object_id,
            "forceAction": True,
            "placeStationary": True,
        },
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "OpenObject", "objectId": tool.object_id, "forceAction": True},
        {"action": "PickupObject", "objectId": held_id, "forceAction": True},
        {"action": "CloseObject", "objectId": tool.object_id, "forceAction": True},
    ]
    return _run_thor_macro(
        thor_env,
        actions,
        success_feedback="Cooled the held object.",
        resolved={"held_object_id": held_id, "tool_resolution": tool},
    )


def _execute_clean(thor_env: Any, tool_args: dict[str, Any]) -> _ThorActionResult:
    held = _held_object(thor_env)
    if held is None:
        return _failed_thor_action("No object is currently held for clean.", {})
    sink = _resolve_manipulation_object(
        thor_env,
        _first_tool_arg(
            tool_args,
            "tool_receptacle",
            "target_receptacle",
            default="sinkbasin",
        ),
        require_receptacle=True,
    )
    if not sink.success:
        return _failed_thor_action(sink.feedback, {"tool_resolution": sink})
    faucet = _nearest_object_of_type(thor_env, "faucet", sink.object_id)
    if faucet is None:
        return _failed_thor_action("No faucet found near the sinkbasin.", {"tool_resolution": sink})
    faucet_id = str(faucet.get("objectId", ""))
    held_id = str(held.get("objectId", ""))
    actions = [
        {"action": "ToggleObjectOff", "objectId": faucet_id, "forceAction": True},
        {
            "action": "PutObject",
            "objectId": held_id,
            "receptacleObjectId": sink.object_id,
            "forceAction": True,
            "placeStationary": True,
        },
        {"action": "ToggleObjectOn", "objectId": faucet_id, "forceAction": True},
        {"action": "ToggleObjectOff", "objectId": faucet_id, "forceAction": True},
        {"action": "PickupObject", "objectId": held_id, "forceAction": True},
    ]
    result = _run_thor_macro(
        thor_env,
        actions,
        success_feedback="Cleaned the held object.",
        resolved={
            "held_object_id": held_id,
            "tool_resolution": sink,
            "faucet_object_id": faucet_id,
        },
    )
    return result


def _run_thor_macro(
    thor_env: Any,
    actions: list[dict[str, Any]],
    *,
    success_feedback: str,
    resolved: dict[str, Any],
) -> _ThorActionResult:
    backend_actions: list[str] = []
    state_actions = {
        "OpenObject": ("isOpen", True),
        "CloseObject": ("isOpen", False),
        "ToggleObjectOn": ("isToggled", True),
        "ToggleObjectOff": ("isToggled", False),
    }

    def failure(message: str) -> _ThorActionResult:
        return _ThorActionResult(
            success=False, feedback=message, backend_actions=backend_actions,
            resolved=_resolved_payload(resolved),
        )

    for action in actions:
        kind = str(action["action"])
        object_id = str(action["objectId"])
        expected = state_actions.get(kind)
        obj = _object_by_id(thor_env, object_id)
        if obj is None:
            return failure(f"{kind}: exact target disappeared.")
        if expected is not None and obj.get(expected[0]) is expected[1]:
            continue
        event = _thor_step(thor_env, action)
        backend_actions.append(kind)
        if not _event_success(event):
            return failure(f"{kind} failed: {_event_error(event) or 'Nothing happens.'}")
        obj = _object_by_id(thor_env, object_id)
        metadata = getattr(thor_env.last_event, "metadata", {})
        inventory = metadata.get("inventoryObjects")
        inventory_ids = (
            {item.get("objectId") for item in inventory}
            if isinstance(inventory, list) else None
        )
        valid = obj is not None
        if expected is not None:
            valid = valid and obj.get(expected[0]) is expected[1]
        elif kind == "PutObject":
            target_id = str(action["receptacleObjectId"])
            target = _object_by_id(thor_env, target_id)
            valid = (
                valid and inventory_ids == set() and obj.get("isPickedUp") is False
                and target_id in (obj.get("parentReceptacles") or [])
                and target is not None
                and object_id in (target.get("receptacleObjectIds") or [])
            )
        elif kind == "PickupObject":
            valid = valid and inventory_ids == {object_id} and obj.get("isPickedUp") is True
        if not valid:
            return failure(f"execution_state_uncertain: {kind} receipt contradicts external state.")
        # Native ALFWorld owns these predicates; the adapter never writes the sets.
        processed_set = None
        if kind == "ToggleObjectOn" and obj.get("objectType") == "Microwave":
            processed_set = "heated_objects"
        elif kind == "ToggleObjectOn" and obj.get("objectType") == "Faucet":
            processed_set = "cleaned_objects"
        elif kind == "CloseObject" and obj.get("objectType") == "Fridge":
            if resolved.get("held_object_id") in (obj.get("receptacleObjectIds") or []):
                processed_set = "cooled_objects"
        if processed_set is not None and resolved.get("held_object_id") not in getattr(
            thor_env, processed_set, ()
        ):
            return failure(f"execution_state_uncertain: native {processed_set} predicate not met.")
    return _ThorActionResult(
        success=True, feedback=success_feedback, backend_actions=backend_actions,
        resolved=_resolved_payload(resolved),
    )


def _resolve_manipulation_object(
    thor_env: Any,
    value: str,
    *,
    require_pickupable: bool = False,
    require_receptacle: bool = False,
    require_openable: bool = False,
    require_toggleable: bool = False,
    preferred_object_id: str | None = None,
) -> _ManipulationResolutionResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    matches = _objects_by_type(objects, value)
    if require_pickupable:
        matches = [obj for obj in matches if obj.get("pickupable") is True]
    if require_receptacle:
        matches = [
            obj for obj in matches if obj.get("receptacle") is True or obj.get("openable") is True
        ]
    if require_openable:
        matches = [obj for obj in matches if obj.get("openable") is True]
    if require_toggleable:
        matches = [obj for obj in matches if obj.get("toggleable") is True]
    if not matches:
        return _ManipulationResolutionResult(
            success=False,
            feedback=f"No THOR object matched {value}.",
            object_id=None,
            object_label=None,
            object_type=None,
        )
    if preferred_object_id:
        for obj in matches:
            if str(obj.get("objectId", "")) == preferred_object_id:
                target = obj
                break
        else:
            target = _choose_object_target(matches)
    else:
        target = _choose_object_target(matches)
    return _ManipulationResolutionResult(
        success=True,
        feedback=f"Resolved {value} to {_command_type_name(target)}.",
        object_id=str(target.get("objectId", "")) or None,
        object_label=_command_label_for_object(objects, target),
        object_type=_command_type_name(target),
    )


def _single_action_result(
    event: Any,
    *,
    success_feedback: str,
    failure_feedback: str,
    backend_action: str,
    resolved: dict[str, Any],
) -> _ThorActionResult:
    success = _event_success(event)
    return _ThorActionResult(
        success=success,
        feedback=(
            success_feedback if success else f"{failure_feedback} {_event_error(event)}".strip()
        ),
        backend_actions=[backend_action],
        resolved=_resolved_payload(resolved),
    )


def _failed_thor_action(feedback: str, resolved: dict[str, Any]) -> _ThorActionResult:
    return _ThorActionResult(
        success=False,
        feedback=feedback,
        backend_actions=[],
        resolved=_resolved_payload(resolved),
    )


def _resolved_payload(resolved: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for prefix, value in resolved.items():
        if isinstance(value, _ManipulationResolutionResult):
            payload[f"{prefix}_object_id"] = value.object_id
            payload[f"{prefix}_object_type"] = value.object_type
        else:
            payload[prefix] = value
    return payload


def _required_tool_arg(tool_args: dict[str, Any], key: str) -> str:
    value = tool_args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _first_tool_arg(
    tool_args: dict[str, Any],
    *keys: str,
    default: str | None = None,
) -> str:
    for key in keys:
        value = tool_args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if default is not None:
        return default
    joined = " or ".join(keys)
    raise ValueError(f"{joined} is required")


def _held_object(thor_env: Any) -> dict[str, Any] | None:
    metadata = getattr(thor_env.last_event, "metadata", {})
    inventory = metadata.get("inventoryObjects", []) if isinstance(metadata, dict) else []
    if isinstance(inventory, list) and inventory:
        held = inventory[0]
        if isinstance(held, dict):
            return held
    return None


def _inventory_object_ids(thor_env: Any) -> tuple[str, ...]:
    metadata = getattr(thor_env.last_event, "metadata", {})
    inventory = metadata.get("inventoryObjects", []) if isinstance(metadata, dict) else []
    if not isinstance(inventory, list):
        return ()
    return tuple(
        sorted(
            str(item.get("objectId", ""))
            for item in inventory
            if isinstance(item, dict) and str(item.get("objectId", ""))
        )
    )


def _inventory_labels(
    scene_index: SceneObjectIndex | None,
    inventory_object_ids: tuple[str, ...],
) -> list[str]:
    labels_by_id = (
        {
            object_ref.object_id: object_ref.canonical_label
            for object_ref in scene_index.by_canonical_label.values()
        }
        if scene_index is not None
        else {}
    )
    return [
        labels_by_id.get(
            object_id,
            _object_type_key(object_id.split("|", maxsplit=1)[0]) or "object",
        )
        for object_id in inventory_object_ids
    ]


def _inventory_text(thor_env: Any) -> str | None:
    held = _held_object(thor_env)
    if held is None:
        return "You are carrying nothing."
    object_type = _object_type_key(str(held.get("objectType") or held.get("objectId", "object")))
    return f"You are carrying: {object_type}."


def _object_by_id(thor_env: Any, object_id: str | None) -> dict[str, Any] | None:
    if not object_id:
        return None
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    for obj in objects:
        if isinstance(obj, dict) and str(obj.get("objectId", "")) == object_id:
            return obj
    return None


def _nearest_object_of_type(
    thor_env: Any,
    object_type: str,
    near_object_id: str | None,
) -> dict[str, Any] | None:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    matches = _objects_by_type(objects, object_type)
    if not matches:
        return None
    near = _object_by_id(thor_env, near_object_id)
    if near is None:
        return _choose_object_target(matches)
    return sorted(matches, key=lambda obj: _distance_sq(obj, near))[0]


def _distance_sq(a: dict[str, Any], b: dict[str, Any]) -> float:
    apos = a.get("position") if isinstance(a, dict) else None
    bpos = b.get("position") if isinstance(b, dict) else None
    if not isinstance(apos, dict) or not isinstance(bpos, dict):
        return 0.0
    try:
        return (
            (float(apos.get("x", 0.0)) - float(bpos.get("x", 0.0))) ** 2
            + (float(apos.get("y", 0.0)) - float(bpos.get("y", 0.0))) ** 2
            + (float(apos.get("z", 0.0)) - float(bpos.get("z", 0.0))) ** 2
        )
    except (TypeError, ValueError):
        return 0.0


def _clean_dirty_objects_in_receptacle(thor_env: Any, receptacle_id: str | None) -> None:
    receptacle = _object_by_id(thor_env, receptacle_id)
    object_ids = receptacle.get("receptacleObjectIds") if isinstance(receptacle, dict) else None
    if not isinstance(object_ids, list):
        return
    for object_id in object_ids:
        obj = _object_by_id(thor_env, str(object_id))
        if obj is None or not bool(obj.get("dirtyable")) or not bool(obj.get("isDirty")):
            continue
        _thor_step(thor_env, {"action": "CleanObject", "objectId": str(object_id)})


def _thor_step(thor_env: Any, action: dict[str, Any]) -> Any:
    return thor_env.step({key: value for key, value in action.items() if value is not None})


def _event_success(event: Any) -> bool:
    metadata = getattr(event, "metadata", {})
    return bool(metadata.get("lastActionSuccess")) if isinstance(metadata, dict) else False


def _event_action_status(event: Any) -> str | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("lastActionSuccess")
    if value is True:
        return "success"
    if value is False:
        return "failure"
    return None


def _event_error(event: Any) -> str:
    metadata = getattr(event, "metadata", {})
    if isinstance(metadata, dict):
        value = metadata.get("errorMessage")
        if isinstance(value, str):
            return value
    return ""


def _event_hash(event: Any) -> str:
    metadata = getattr(event, "metadata", None)
    try:
        encoded = json.dumps(
            metadata,
            allow_nan=False,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(metadata).encode("utf-8", errors="replace")
    frame = getattr(event, "frame", None)
    tobytes = getattr(frame, "tobytes", None)
    if callable(tobytes):
        encoded += tobytes()
    return hashlib.sha256(encoded).hexdigest()


def _navigation_candidates_hash(
    candidates: tuple[tuple[dict[str, Any], str], ...],
) -> str:
    encoded = json.dumps(
        [
            {
                "target_object_id": target_id,
                "requested_pose": (
                    asdict(pose) if (pose := _agent_pose_from_action(action)) else None
                ),
            }
            for action, target_id in candidates
        ],
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _empty_execution_trace_events(
    *,
    execution_kind: str,
    context_id: str,
    scene_generation: int,
    goal_generation: int,
    source_event_sequence: int,
    tool_call_id: str,
    classification: str,
    budget_limit: dict[str, int | float],
    backend_action_count: int = 0,
    candidate_count: int = 0,
    put_attempt_count: int = 0,
    budget_stop_reason: str | None = None,
    held_object_id: str | None = None,
    target_receptacle_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    locked_candidates_hash = _navigation_candidates_hash(())
    budget_used: dict[str, int | float] = {
        "candidates": candidate_count,
        "backend_actions": backend_action_count,
        "elapsed_ms": 0.0,
    }
    if execution_kind == "put":
        budget_used["put_attempts"] = put_attempt_count
    common = {
        "execution_kind": execution_kind,
        "context_kind": "navigation" if execution_kind == "navigation" else "pose",
        "tool_call_id": tool_call_id,
        "context_id": context_id,
        "scene_generation": scene_generation,
        "goal_generation": goal_generation,
        "source_event_sequence": source_event_sequence,
        "locked_candidates_hash": locked_candidates_hash,
        "held_object_id": held_object_id,
        "target_receptacle_id": target_receptacle_id,
        "attempt_id": None,
        "attempt_phase": "preflight",
        "budget_limit": dict(budget_limit),
        "budget_used": budget_used,
        "budget_stop_reason": budget_stop_reason,
        "requested_pose": None,
        "actual_pose": None,
        "raw_event_ref": None,
        "raw_event_hash": None,
    }
    return (
        {"event": "context_created", **common, "locked_candidates": []},
        {
            "event": "context_invalidated",
            **common,
            "invalidation_reason": classification,
        },
        {
            "event": "execution_terminal",
            **common,
            "classification": classification,
            "success": classification == "success",
        },
    )


def _pose_context_created_trace_event(context: PoseContext) -> dict[str, Any]:
    return {
        "event": "context_created",
        "execution_kind": "navigation",
        "context_kind": "pose",
        "tool_call_id": context.created_tool_call_id,
        "context_id": context.context_id,
        "scene_generation": context.scene_generation,
        "goal_generation": context.goal_generation,
        "source_event_sequence": context.source_event_sequence,
        "source_frame_hash": context.source_frame_hash,
        "anchor_object_id": context.anchor_object_id,
        "locked_candidates_hash": context.candidates_hash,
        "locked_candidates": [asdict(candidate) for candidate in context.locked_candidates],
        "actual_pose": asdict(context.current_actual_pose),
        "attempt_id": None,
        "attempt_phase": "navigation_success_pose_context",
        "budget_stop_reason": None,
    }


def _pose_context_invalidated_trace_event(
    context: PoseContext,
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "event": "context_invalidated",
        "execution_kind": "navigation",
        "context_kind": "pose",
        "tool_call_id": context.created_tool_call_id,
        "context_id": context.context_id,
        "scene_generation": context.scene_generation,
        "goal_generation": context.goal_generation,
        "source_event_sequence": context.source_event_sequence,
        "source_frame_hash": context.source_frame_hash,
        "anchor_object_id": context.anchor_object_id,
        "locked_candidates_hash": context.candidates_hash,
        "invalidation_reason": reason,
        "attempt_id": None,
        "attempt_phase": None,
        "budget_stop_reason": None,
    }


def _external_action_from_event(
    event: Any,
    *,
    event_sequence: int,
) -> ExternalActionResult:
    status = _event_action_status(event)
    if status is None:
        return ExternalActionResult(
            status="uncertain",
            raw_event_ref=None,
            raw_event_hash=None,
            detail="External action returned no authoritative status.",
            actual_agent_pose=_agent_pose_from_event(event),
        )
    return ExternalActionResult(
        status=status,
        raw_event_ref=f"event:{event_sequence}",
        raw_event_hash=_event_hash(event),
        detail=_event_error(event),
        actual_agent_pose=_agent_pose_from_event(event),
    )


def _empty_external_read(*, status: ReadStatus) -> ExternalRead:
    return ExternalRead(
        status=status,
        raw_event_ref=None,
        raw_event_hash=None,
        inventory_object_ids=(),
        held_object_id=None,
        exact_object_present=False,
        object_parent_ids=(),
        target_child_ids=(),
        actual_agent_pose=None,
        goal_summary={},
        exact_object_is_picked_up=None,
    )


def _external_read_from_event(
    *,
    thor_env: Any,
    event: Any,
    exact_object_id: str,
    exact_target_id: str,
    event_sequence: int,
) -> ExternalRead:
    if event is None:
        return _empty_external_read(status="missing")
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return _empty_external_read(status="error")
    objects = metadata.get("objects")
    inventory = metadata.get("inventoryObjects")
    if not isinstance(objects, list) or not isinstance(inventory, list):
        return _empty_external_read(status="missing")

    exact_object = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == exact_object_id
        ),
        None,
    )
    exact_target = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == exact_target_id
        ),
        None,
    )
    inventory_ids = tuple(
        sorted(
            str(item.get("objectId", ""))
            for item in inventory
            if isinstance(item, dict) and str(item.get("objectId", ""))
        )
    )
    held_object_id = inventory_ids[0] if inventory_ids else None
    pose = _agent_pose_from_event(event)
    status = (
        "ok"
        if exact_object is not None and exact_target is not None and pose is not None
        else "missing"
    )
    picked_up = exact_object.get("isPickedUp") if exact_object is not None else None
    exact_object_is_picked_up = picked_up if isinstance(picked_up, bool) else None
    try:
        met, total = thor_env.get_goal_conditions_met()
        goal_summary: dict[str, Any] = {"met": int(met), "total": int(total)}
    except Exception:
        goal_summary = {}
    return ExternalRead(
        status=status,
        raw_event_ref=f"event:{event_sequence}",
        raw_event_hash=_event_hash(event),
        inventory_object_ids=inventory_ids,
        held_object_id=held_object_id,
        exact_object_present=exact_object is not None,
        object_parent_ids=_string_tuple(
            exact_object.get("parentReceptacles") if exact_object is not None else None
        ),
        target_child_ids=_string_tuple(
            exact_target.get("receptacleObjectIds") if exact_target is not None else None
        ),
        actual_agent_pose=pose,
        goal_summary=goal_summary,
        exact_object_is_picked_up=exact_object_is_picked_up,
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str) and item)


def _teleport_to_visible_object(thor_env: Any, object_type: str) -> _NavigationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = _objects_by_type(objects, object_type)
    return _teleport_to_targets(thor_env, targets, target_name=object_type)


def _teleport_to_object_ids(
    thor_env: Any,
    object_ids: list[str | None],
    *,
    navigation_budget: Any | None = None,
    monotonic_ms: Any | None = None,
    context_id: str | None = None,
    scene_generation: int = 0,
    goal_generation: int = 0,
    source_event_sequence: int = 0,
    tool_call_id: str | None = None,
) -> _NavigationResult:
    target_ids = {str(item) for item in object_ids if isinstance(item, str) and item}
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = [
        obj
        for obj in objects
        if isinstance(obj, dict) and str(obj.get("objectId", "")) in target_ids
    ]
    target_name = ", ".join(sorted(target_ids)) if target_ids else "target"
    return _teleport_to_targets(
        thor_env,
        targets,
        target_name=target_name,
        navigation_budget=navigation_budget,
        monotonic_ms=monotonic_ms,
        context_id=context_id,
        scene_generation=scene_generation,
        goal_generation=goal_generation,
        source_event_sequence=source_event_sequence,
        tool_call_id=tool_call_id,
    )


def _teleport_to_targets(
    thor_env: Any,
    targets: list[dict[str, Any]],
    *,
    target_name: str,
    navigation_budget: Any | None = None,
    monotonic_ms: Any | None = None,
    context_id: str | None = None,
    scene_generation: int = 0,
    goal_generation: int = 0,
    source_event_sequence: int = 0,
    tool_call_id: str | None = None,
) -> _NavigationResult:
    budget = navigation_budget or SimpleNamespace(
        max_navigation_candidates=_DEFAULT_NAVIGATION_MAX_CANDIDATES,
        max_navigation_backend_actions=_DEFAULT_NAVIGATION_MAX_BACKEND_ACTIONS,
        max_navigation_elapsed_ms=_DEFAULT_NAVIGATION_MAX_ELAPSED_MS,
    )
    clock = monotonic_ms or (lambda: time.perf_counter() * 1000.0)
    started_ms = float(clock())
    trace_events: list[dict[str, Any]] = []
    attempted_count = 0
    backend_action_count = 0
    locked_candidates: tuple[tuple[dict[str, Any], str], ...] = ()
    locked_candidates_hash = _navigation_candidates_hash(locked_candidates)
    resolved_context_id = context_id or (
        f"navigation-{scene_generation}-{goal_generation}-{source_event_sequence}"
    )

    def budget_limit() -> dict[str, int | float]:
        return {
            "candidates": int(budget.max_navigation_candidates),
            "backend_actions": int(budget.max_navigation_backend_actions),
            "elapsed_ms": float(budget.max_navigation_elapsed_ms),
        }

    def budget_used() -> dict[str, int | float]:
        return {
            "candidates": attempted_count,
            "backend_actions": backend_action_count,
            "elapsed_ms": max(0.0, float(clock()) - started_ms),
        }

    def add_trace(event_name: str, **details: Any) -> None:
        payload: dict[str, Any] = {
            "event": event_name,
            "execution_kind": "navigation",
            "context_kind": "navigation",
            "tool_call_id": tool_call_id,
            "context_id": resolved_context_id,
            "scene_generation": scene_generation,
            "goal_generation": goal_generation,
            "source_event_sequence": source_event_sequence,
            "locked_candidates_hash": locked_candidates_hash,
            "attempt_id": None,
            "attempt_phase": None,
            "budget_limit": budget_limit(),
            "budget_used": budget_used(),
            "budget_stop_reason": None,
        }
        payload.update(details)
        trace_events.append(payload)

    def finish(
        *,
        success: bool,
        feedback: str,
        failure_reason: str | None,
        budget_stop_reason: str | None,
        event: Any | None,
        actual_pose: AgentPose | None,
        reachable: list[dict[str, float]],
    ) -> _NavigationResult:
        add_trace(
            "context_invalidated",
            invalidation_reason=(
                "navigation_completed" if success else budget_stop_reason or failure_reason
            ),
        )
        add_trace(
            "execution_terminal",
            classification="success" if success else failure_reason,
            success=success,
            budget_stop_reason=budget_stop_reason,
            actual_pose=asdict(actual_pose) if actual_pose is not None else None,
            raw_event_ref=(
                f"event:{source_event_sequence + backend_action_count}"
                if event is not None and backend_action_count > 0
                else None
            ),
            raw_event_hash=_event_hash(event) if event is not None else None,
        )
        return _NavigationResult(
            success=success,
            feedback=feedback,
            failure_reason=failure_reason,
            budget_stop_reason=budget_stop_reason,
            backend_action_count=backend_action_count,
            event=event,
            actual_pose=actual_pose,
            reachable=reachable,
            trace_events=tuple(trace_events),
            context_id=resolved_context_id,
            locked_candidates_hash=locked_candidates_hash,
            candidates_attempted=attempted_count,
        )

    if not targets:
        add_trace("context_created", locked_candidates=[])
        return finish(
            success=False,
            feedback=f"No {target_name} navigation target found in the scene.",
            failure_reason="harness_navigation_failure",
            budget_stop_reason=None,
            event=None,
            actual_pose=None,
            reachable=[],
        )

    metadata = getattr(thor_env.last_event, "metadata", {})
    state_read_started_ms = float(clock())
    add_trace(
        "state_read_started",
        attempt_phase="reachable_positions",
    )
    backend_action_count = 1
    try:
        reachable = _reachable_positions(thor_env)
    except Exception as exc:
        event = getattr(thor_env, "last_event", None)
        add_trace(
            "state_read_result",
            attempt_phase="reachable_positions",
            external_status="error",
            raw_event_ref=(
                f"event:{source_event_sequence + backend_action_count}"
                if event is not None
                else None
            ),
            raw_event_hash=_event_hash(event) if event is not None else None,
            state_read_elapsed_ms=max(0.0, float(clock()) - state_read_started_ms),
        )
        add_trace("context_created", locked_candidates=[])
        return finish(
            success=False,
            feedback=str(exc),
            failure_reason="execution_state_uncertain",
            budget_stop_reason=None,
            event=None,
            actual_pose=None,
            reachable=[],
        )
    reachable_event = getattr(thor_env, "last_event", None)
    add_trace(
        "state_read_result",
        attempt_phase="reachable_positions",
        external_status="success",
        raw_event_ref=f"event:{source_event_sequence + backend_action_count}",
        raw_event_hash=(_event_hash(reachable_event) if reachable_event is not None else None),
        state_read_elapsed_ms=max(0.0, float(clock()) - state_read_started_ms),
    )
    if not reachable:
        add_trace("context_created", locked_candidates=[])
        return finish(
            success=False,
            feedback="Navigation backend could not read reachable positions.",
            failure_reason="harness_navigation_failure",
            budget_stop_reason=None,
            event=None,
            actual_pose=None,
            reachable=[],
        )

    agent_y = _agent_height(metadata)
    locked_candidates = tuple(_teleport_candidates(targets, reachable, agent_y=agent_y))
    locked_candidates_hash = _navigation_candidates_hash(locked_candidates)
    add_trace(
        "context_created",
        anchor_object_ids=sorted({target_id for _action, target_id in locked_candidates}),
        locked_candidates=[
            {
                "target_object_id": target_id,
                "requested_pose": (
                    asdict(pose) if (pose := _agent_pose_from_action(action)) else None
                ),
            }
            for action, target_id in locked_candidates
        ],
    )
    for candidate_index, (action, target_id) in enumerate(locked_candidates, start=1):
        stop_reason = _navigation_budget_stop(
            budget=budget,
            attempted_count=attempted_count,
            backend_action_count=backend_action_count,
            elapsed_ms=max(0.0, float(clock()) - started_ms),
        )
        if stop_reason is not None:
            final_event = getattr(thor_env, "last_event", None)
            return finish(
                success=False,
                feedback=f"Navigation stopped at fixed budget: {stop_reason}.",
                failure_reason="harness_navigation_failure",
                budget_stop_reason=stop_reason,
                event=final_event,
                actual_pose=_agent_pose_from_event(final_event),
                reachable=reachable,
            )

        requested_pose = _agent_pose_from_action(action)
        before_pose = _agent_pose_from_event(getattr(thor_env, "last_event", None))
        attempted_count += 1
        attempt_id = f"{resolved_context_id}:attempt-{candidate_index:04d}"
        add_trace(
            "attempt_started",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            requested_pose=asdict(requested_pose) if requested_pose is not None else None,
            actual_pose=asdict(before_pose) if before_pose is not None else None,
            anchor_object_id=target_id,
        )
        move_started_ms = float(clock())
        add_trace(
            "move_started",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            requested_pose=asdict(requested_pose) if requested_pose is not None else None,
            anchor_object_id=target_id,
        )
        backend_action_count += 1
        try:
            event = thor_env.step(action)
        except Exception as exc:
            add_trace(
                "move_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                requested_pose=(asdict(requested_pose) if requested_pose is not None else None),
                actual_pose=None,
                external_status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                move_elapsed_ms=max(0.0, float(clock()) - move_started_ms),
            )
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=None,
                raw_event_hash=None,
            )
            return finish(
                success=False,
                feedback=str(exc),
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=None,
                actual_pose=None,
                reachable=reachable,
            )
        if event is None:
            add_trace(
                "move_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                requested_pose=(asdict(requested_pose) if requested_pose is not None else None),
                actual_pose=None,
                external_status="uncertain",
                raw_event_ref=None,
                raw_event_hash=None,
                move_elapsed_ms=max(0.0, float(clock()) - move_started_ms),
            )
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=None,
                raw_event_hash=None,
            )
            return finish(
                success=False,
                feedback="TeleportFull returned no event.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=None,
                actual_pose=None,
                reachable=reachable,
            )

        action_status = _event_action_status(event)
        actual_pose = _agent_pose_from_event(
            event,
            requested_pose=requested_pose,
            allow_partial_requested_fallback=True,
        )
        raw_event_ref = f"event:{source_event_sequence + backend_action_count}"
        raw_event_hash = _event_hash(event)
        add_trace(
            "move_result",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            requested_pose=asdict(requested_pose) if requested_pose is not None else None,
            actual_pose=asdict(actual_pose) if actual_pose is not None else None,
            external_status=action_status or "uncertain",
            raw_event_ref=raw_event_ref,
            raw_event_hash=raw_event_hash,
            move_elapsed_ms=max(0.0, float(clock()) - move_started_ms),
        )
        if action_status is None or requested_pose is None or actual_pose is None:
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
            )
            return finish(
                success=False,
                feedback="Could not prove TeleportFull return or actual pose.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )
        if action_status == "success" and not actual_pose.matches(requested_pose):
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
            )
            return finish(
                success=False,
                feedback="TeleportFull succeeded but actual pose did not match the request.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )
        if action_status == "failure":
            if before_pose is None or not actual_pose.matches(before_pose):
                add_trace(
                    "observation_read_result",
                    attempt_id=attempt_id,
                    attempt_phase="navigation_candidate",
                    observation_status="not_evaluated",
                    raw_event_ref=raw_event_ref,
                    raw_event_hash=raw_event_hash,
                )
                return finish(
                    success=False,
                    feedback="TeleportFull failed but actual pose changed or was unreadable.",
                    failure_reason="execution_state_uncertain",
                    budget_stop_reason=None,
                    event=event,
                    actual_pose=actual_pose,
                    reachable=reachable,
                )
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="not_evaluated",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
            )
            continue

        observation_started_ms = float(clock())
        observation = _exact_target_observation(event, target_id)
        if observation is None:
            add_trace(
                "observation_read_result",
                attempt_id=attempt_id,
                attempt_phase="navigation_candidate",
                observation_status="missing",
                raw_event_ref=raw_event_ref,
                raw_event_hash=raw_event_hash,
                state_read_elapsed_ms=max(0.0, float(clock()) - observation_started_ms),
            )
            return finish(
                success=False,
                feedback="The exact navigation target was missing from the final event.",
                failure_reason="execution_state_uncertain",
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )
        exact_visible, exact_detected, bbox_area = observation
        add_trace(
            "observation_read_result",
            attempt_id=attempt_id,
            attempt_phase="navigation_candidate",
            observation_status="ok",
            exact_target_visible=exact_visible,
            exact_target_detected=exact_detected,
            bbox_area=bbox_area,
            raw_event_ref=raw_event_ref,
            raw_event_hash=raw_event_hash,
            state_read_elapsed_ms=max(0.0, float(clock()) - observation_started_ms),
        )
        if exact_visible and exact_detected and bbox_area > 0:
            return finish(
                success=True,
                feedback="Navigation target passed the exact observation gate.",
                failure_reason=None,
                budget_stop_reason=None,
                event=event,
                actual_pose=actual_pose,
                reachable=reachable,
            )

    final_event = getattr(thor_env, "last_event", None)
    return finish(
        success=False,
        feedback=f"Navigation candidates were exhausted for {target_name}.",
        failure_reason="harness_navigation_failure",
        budget_stop_reason="candidates_exhausted",
        event=final_event,
        actual_pose=_agent_pose_from_event(final_event),
        reachable=reachable,
    )


def _find_object_location(thor_env: Any, object_type: str) -> _ObjectLocationResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    targets = [
        obj for obj in _objects_by_type(objects, object_type) if obj.get("pickupable") is True
    ]
    if not targets:
        return _ObjectLocationResult(
            success=False,
            feedback=(
                f"No movable {object_type} object found in the current scene. "
                "Use robot_go_to for places, furniture, appliances, and receptacles."
            ),
            object_label=None,
            source_receptacle=None,
            object_type=None,
        )

    target = _choose_object_target(targets)
    object_label = _command_label_for_object(objects, target)
    source = _source_receptacle_label(objects, target)
    object_type_name = _command_type_name(target)
    source_text = f" at {source}" if source else ""
    return _ObjectLocationResult(
        success=True,
        feedback=f"Found {object_label}{source_text}.",
        object_label=object_label,
        source_receptacle=source,
        object_type=object_type_name,
    )


def _resolve_navigation_target(
    thor_env: Any,
    target: str,
    *,
    scene_index: SceneObjectIndex | None = None,
) -> _TargetResolutionResult:
    metadata = getattr(thor_env.last_event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    target_obj: dict[str, Any] | None = None
    if scene_index is not None:
        indexed = _resolve_scene_object_ref(scene_index, target)
        if indexed is not None:
            target_obj = next(
                (
                    obj
                    for obj in objects
                    if isinstance(obj, dict) and str(obj.get("objectId", "")) == indexed.object_id
                ),
                indexed.metadata,
            )
    else:
        matches = _objects_by_type(objects, target)
        if matches:
            target_obj = _choose_object_target(matches)
    if target_obj is None:
        return _TargetResolutionResult(
            success=False,
            feedback=f"No {target} target found in the current scene.",
            resolved_kind=None,
            resolved_label=None,
            object_label=None,
            source_receptacle=None,
            object_type=None,
            object_id=None,
        )

    pickupable = target_obj.get("pickupable") is True
    if pickupable:
        resolved_kind = "movable_object"
    else:
        resolved_kind = (
            "toggle_object" if bool(target_obj.get("toggleable")) else "receptacle_or_fixture"
        )

    label = _command_label_for_object(objects, target_obj)
    object_type_name = _command_type_name(target_obj)
    source = _source_receptacle_label(objects, target_obj) if pickupable else None
    return _TargetResolutionResult(
        success=True,
        feedback=f"Resolved {target} to {label or object_type_name}.",
        resolved_kind=resolved_kind,
        resolved_label=label or object_type_name,
        object_label=label if pickupable else None,
        source_receptacle=source,
        object_type=object_type_name,
        object_id=str(target_obj.get("objectId", "")) or None,
    )


def _resolve_scene_object_ref(
    scene_index: SceneObjectIndex,
    target: str,
) -> SceneObjectRef | None:
    direct = scene_index.resolve(target)
    if direct is not None:
        return direct
    words = target.casefold().strip().split()
    instance = words[-1] if words and words[-1].isdigit() else None
    base = " ".join(words[:-1] if instance is not None else words)
    aliases = _OBJECT_TYPE_ALIASES.get(_object_query_key(base), ())
    for alias in aliases:
        candidate = f"{alias} {instance}" if instance is not None else alias
        resolved = scene_index.resolve(candidate)
        if resolved is not None:
            return resolved
    return None


def _choose_object_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        targets,
        key=lambda obj: (
            not bool(obj.get("visible")),
            str(obj.get("objectId", "")),
        ),
    )[0]


def _source_receptacle_label(
    objects: list[Any],
    target: dict[str, Any],
) -> str | None:
    object_by_id = {str(obj.get("objectId", "")): obj for obj in objects if isinstance(obj, dict)}
    parent_ids = target.get("parentReceptacles") or []
    if isinstance(parent_ids, list):
        for parent_id in parent_ids:
            parent = object_by_id.get(str(parent_id))
            if parent is not None:
                label = _command_label_for_object(objects, parent)
                if label:
                    return label

    target_id = str(target.get("objectId", ""))
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        recep_ids = obj.get("receptacleObjectIds") or []
        if isinstance(recep_ids, list) and target_id in {str(item) for item in recep_ids}:
            label = _command_label_for_object(objects, obj)
            if label:
                return label
    return None


def _command_label_for_object(objects: list[Any], target: dict[str, Any]) -> str | None:
    command_type = _command_type_name(target)
    if not command_type:
        return None
    same_type = sorted(
        [
            obj
            for obj in objects
            if isinstance(obj, dict) and _command_type_name(obj) == command_type
        ],
        key=lambda obj: str(obj.get("objectId", "")),
    )
    try:
        index = same_type.index(target) + 1
    except ValueError:
        index = 1
    return f"{command_type} {index}"


def _command_type_name(obj: dict[str, Any]) -> str:
    return _object_type_key(str(obj.get("objectType", "")))


def _objects_by_type(objects: list[Any], object_type: str) -> list[dict[str, Any]]:
    query_key = _object_query_key(object_type)
    full_query_key = _object_type_key(object_type)
    if not query_key and not full_query_key:
        return []
    typed_objects = [obj for obj in objects if isinstance(obj, dict)]

    exact_matches = [
        obj
        for obj in typed_objects
        if full_query_key and full_query_key in _exact_object_keys(objects, obj)
    ]
    if exact_matches:
        return exact_matches

    type_matches = [
        obj
        for obj in typed_objects
        if _object_query_key(str(obj.get("objectType", ""))) == query_key
    ]
    if type_matches:
        return type_matches

    alias_matches = _objects_by_alias(typed_objects, query_key)
    if alias_matches:
        return alias_matches

    suffix_type_keys = {
        _object_query_key(str(obj.get("objectType", "")))
        for obj in typed_objects
        if _object_query_key(str(obj.get("objectType", ""))).endswith(query_key)
        or query_key.endswith(_object_query_key(str(obj.get("objectType", ""))))
    }
    if len(suffix_type_keys) == 1:
        (matched_type,) = tuple(suffix_type_keys)
        return [
            obj
            for obj in typed_objects
            if _object_query_key(str(obj.get("objectType", ""))) == matched_type
        ]
    return []


def _exact_object_keys(objects: list[Any], obj: dict[str, Any]) -> set[str]:
    keys = {
        _object_type_key(str(obj.get("objectType", ""))),
        _object_type_key(str(obj.get("objectId", ""))),
    }
    object_id = str(obj.get("objectId", ""))
    if "|" in object_id:
        keys.add(_object_type_key(object_id.split("|", 1)[0]))
    label = _command_label_for_object(objects, obj)
    if label:
        keys.add(_object_type_key(label))
    return {key for key in keys if key}


def _objects_by_alias(
    objects: list[dict[str, Any]],
    query_key: str,
) -> list[dict[str, Any]]:
    alias_targets = _OBJECT_TYPE_ALIASES.get(query_key, ())
    if not alias_targets:
        return []
    alias_target_set = set(alias_targets)
    return [
        obj
        for obj in objects
        if _object_query_key(str(obj.get("objectType", ""))) in alias_target_set
    ]


def _ordered_navigation_sources(
    admissible_commands: tuple[str, ...],
    *,
    preferred_source: str | None,
) -> list[str]:
    sources: list[str] = []
    if preferred_source:
        sources.append(preferred_source)
    prefix = "go to "
    for command in admissible_commands:
        if not command.startswith(prefix):
            continue
        source = command[len(prefix) :].strip()
        if source:
            sources.append(source)
    output: list[str] = []
    seen: set[str] = set()
    for source in sources:
        key = _object_type_key(source)
        if key in seen:
            continue
        seen.add(key)
        output.append(source)
    return output


def _object_query_key(value: str) -> str:
    key = _object_type_key(value)
    while key and key[-1].isdigit():
        key = key[:-1]
    return key


def _object_type_key(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _observed_label_for_type(observation: str, object_type: str) -> str | None:
    key = _object_query_key(object_type)
    if not key:
        return None
    import re

    pattern = re.compile(r"\b([a-z]+)\s+(\d+)\b", re.IGNORECASE)
    for match in pattern.finditer(observation):
        label = f"{match.group(1).casefold()} {match.group(2)}"
        if _object_query_key(label) == key:
            return label
    return None


def _reachable_positions(thor_env: Any) -> list[dict[str, float]]:
    event = thor_env.step({"action": "GetReachablePositions"})
    if event is None:
        raise RuntimeError("GetReachablePositions returned no event")
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        raise RuntimeError("GetReachablePositions returned unreadable metadata")
    if metadata.get("lastActionSuccess") is not True:
        raise RuntimeError(
            _event_error(event) or "GetReachablePositions was rejected by the backend"
        )
    positions = metadata.get("reachablePositions")
    if not isinstance(positions, list):
        positions = metadata.get("actionReturn")
    if not isinstance(positions, list):
        raise RuntimeError("GetReachablePositions returned no position list")
    reachable: list[dict[str, float]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        try:
            reachable.append(
                {
                    "x": float(position["x"]),
                    "z": float(position["z"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return reachable


def _navigation_budget_stop(
    *,
    budget: Any,
    attempted_count: int,
    backend_action_count: int,
    elapsed_ms: float,
) -> str | None:
    if backend_action_count >= int(budget.max_navigation_backend_actions):
        return "max_navigation_backend_actions"
    if elapsed_ms >= float(budget.max_navigation_elapsed_ms):
        return "max_navigation_elapsed_ms"
    if attempted_count >= int(budget.max_navigation_candidates):
        return "max_navigation_candidates"
    return None


def _agent_pose_from_action(action: dict[str, Any]) -> AgentPose | None:
    try:
        return AgentPose(
            x=float(action["x"]),
            y=float(action["y"]),
            z=float(action["z"]),
            rotation=float(action["rotation"]),
            horizon=float(action["horizon"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _agent_pose_from_event(
    event: Any,
    *,
    requested_pose: AgentPose | None = None,
    allow_partial_requested_fallback: bool = False,
) -> AgentPose | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    agent = metadata.get("agent")
    if not isinstance(agent, dict):
        return None
    position = agent.get("position")
    rotation = agent.get("rotation")
    try:
        if not isinstance(position, dict) or not isinstance(rotation, dict):
            raise KeyError("agent pose mapping missing")
        return AgentPose(
            x=float(position["x"]),
            y=float(position["y"]),
            z=float(position["z"]),
            rotation=float(rotation["y"]),
            horizon=float(agent["cameraHorizon"]),
        )
    except (KeyError, TypeError, ValueError):
        if (
            allow_partial_requested_fallback
            and requested_pose is not None
            and isinstance(position, dict)
            and "y" in position
        ):
            try:
                return AgentPose(
                    x=requested_pose.x,
                    y=float(position["y"]),
                    z=requested_pose.z,
                    rotation=requested_pose.rotation,
                    horizon=requested_pose.horizon,
                )
            except (TypeError, ValueError):
                return None
        return None


def _teleport_action_from_pose(pose: AgentPose) -> dict[str, Any]:
    return {
        "action": "TeleportFull",
        "x": pose.x,
        "y": pose.y,
        "z": pose.z,
        "rotateOnTeleport": True,
        "rotation": pose.rotation,
        "horizon": pose.horizon,
    }


def _exact_target_observation(
    event: Any,
    object_id: str,
) -> tuple[bool, bool, float] | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    objects = metadata.get("objects")
    if not isinstance(objects, list):
        return None
    target = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == object_id
        ),
        None,
    )
    if target is None:
        return None
    detections = getattr(event, "instance_detections2D", None)
    box = detections.get(object_id) if isinstance(detections, dict) else None
    area = _bbox_area_score(box)
    return target.get("visible") is True, box is not None, area


def _local_pose_candidates(
    *,
    event: Any,
    anchor_object_id: str,
    reachable: list[dict[str, float]],
    current_pose: AgentPose,
) -> tuple[AgentPose, ...]:
    metadata = getattr(event, "metadata", None)
    objects = metadata.get("objects") if isinstance(metadata, dict) else None
    if not isinstance(objects, list):
        return ()
    target = next(
        (
            item
            for item in objects
            if isinstance(item, dict) and str(item.get("objectId", "")) == anchor_object_id
        ),
        None,
    )
    if target is None:
        return ()
    actions = _single_target_teleport_candidates(
        target,
        reachable,
        agent_y=current_pose.y,
    )
    poses: list[AgentPose] = []
    for action, _distance in actions:
        pose = _agent_pose_from_action(action)
        if pose is None or pose.matches(current_pose):
            continue
        if pose not in poses:
            poses.append(pose)
    poses.sort(
        key=lambda pose: (
            (pose.x - current_pose.x) ** 2 + (pose.z - current_pose.z) ** 2,
            abs((pose.rotation - current_pose.rotation + 180.0) % 360.0 - 180.0),
            abs(pose.horizon - current_pose.horizon),
            pose.x,
            pose.z,
        )
    )
    return tuple(poses)


def _agent_height(metadata: dict[str, Any]) -> float:
    try:
        return float(metadata["agent"]["position"]["y"])
    except (KeyError, TypeError, ValueError):
        return 0.9010564


def _teleport_candidates(
    targets: list[dict[str, Any]],
    reachable: list[dict[str, float]],
    *,
    agent_y: float,
) -> list[tuple[dict[str, Any], str]]:
    output: list[tuple[dict[str, Any], str, float]] = []
    for target in targets:
        object_id = str(target.get("objectId", ""))
        for action, distance in _single_target_teleport_candidates(
            target,
            reachable,
            agent_y=agent_y,
        ):
            output.append((action, object_id, distance))
    output.sort(key=lambda item: item[2])
    return [(action, object_id) for action, object_id, _ in output]


def _single_target_teleport_candidates(
    target: dict[str, Any],
    reachable: list[dict[str, float]],
    *,
    agent_y: float,
) -> list[tuple[dict[str, Any], float]]:
    target_position = target.get("position", {})
    target_x = float(target_position.get("x", 0.0))
    target_y = float(target_position.get("y", agent_y))
    target_z = float(target_position.get("z", 0.0))
    nearest = sorted(
        reachable,
        key=lambda point: (point["x"] - target_x) ** 2 + (point["z"] - target_z) ** 2,
    )[:12]
    actions: list[tuple[dict[str, Any], float]] = []
    seen: set[tuple[float, float, int, int]] = set()
    for point in nearest:
        distance = math.hypot(target_x - point["x"], target_z - point["z"])
        base_rotation = _quantized_rotation(
            target_x - point["x"],
            target_z - point["z"],
        )
        base_horizon = _quantized_horizon(target_y - agent_y, distance)
        rotations = _ordered_unique_ints(
            [
                base_rotation,
                base_rotation - 90,
                base_rotation + 90,
                base_rotation + 180,
                0,
                90,
                180,
                270,
            ],
            modulo=360,
        )
        horizons = _ordered_unique_ints(
            [
                base_horizon,
                base_horizon - 15,
                base_horizon + 15,
                0,
                15,
                30,
                45,
                60,
            ]
        )
        for rotation in rotations:
            for horizon in horizons:
                if horizon < -30 or horizon > 60:
                    continue
                key = (round(point["x"], 3), round(point["z"], 3), rotation, horizon)
                if key in seen:
                    continue
                seen.add(key)
                actions.append(
                    (
                        {
                            "action": "TeleportFull",
                            "x": point["x"],
                            "y": agent_y,
                            "z": point["z"],
                            "rotateOnTeleport": True,
                            "rotation": rotation,
                            "horizon": horizon,
                        },
                        distance,
                    )
                )
    return actions


def _quantized_rotation(dx: float, dz: float) -> int:
    if abs(dx) < 1e-6 and abs(dz) < 1e-6:
        return 0
    yaw = math.degrees(math.atan2(dx, dz))
    return int(round(yaw / 90.0) * 90) % 360


def _quantized_horizon(dy: float, horizontal_distance: float) -> int:
    pitch = math.degrees(math.atan2(dy, max(horizontal_distance, 1e-3)))
    # AI2-THOR horizon is positive when looking down. For targets below the
    # camera, dy is negative and the desired horizon should be positive.
    horizon = int(round((-pitch) / 15.0) * 15)
    return max(-30, min(60, horizon))


def _ordered_unique_ints(values: list[int], *, modulo: int | None = None) -> list[int]:
    seen: set[int] = set()
    output: list[int] = []
    for value in values:
        item = value % modulo if modulo else value
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _target_visibility_score(
    event: Any,
    object_id: str,
    target_ids: set[str] | None = None,
) -> tuple[bool, float]:
    metadata = getattr(event, "metadata", {})
    objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
    ids = target_ids or {object_id}
    target = next(
        (obj for obj in objects if isinstance(obj, dict) and obj.get("objectId") in ids),
        None,
    )
    visible = bool(target and target.get("visible"))
    score = 1.0 if visible else 0.0
    detections = getattr(event, "instance_detections2D", None)
    if isinstance(detections, dict):
        for target_id in ids:
            if target_id in detections:
                score += _bbox_area_score(detections[target_id])
                visible = True
                break
    return visible, score


def _bbox_area_score(box: Any) -> float:
    try:
        x1, y1, x2, y2 = [float(item) for item in box]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


class _AdapterOracleBackend:
    def __init__(self, adapter: AlfworldEnvAdapter) -> None:
        self._adapter = adapter

    def capture_event(self) -> ExternalEventRead:
        thor_env = self._adapter._resolve_thor_env()
        return _external_event_read(
            getattr(thor_env, "last_event", None),
            event_sequence=self._adapter._event_sequence,
            thor_env=thor_env,
        )

    def send(self, request: ExternalActionRequest) -> ExternalEventRead:
        thor_env = self._adapter._resolve_thor_env()
        event = thor_env.step(dict(request.payload))
        self._adapter._event_sequence += 1
        external_event = _external_event_read(
            event,
            event_sequence=self._adapter._event_sequence,
            thor_env=thor_env,
        )
        _persist_runtime_external_event(self._adapter, external_event)
        return external_event

    def close(self) -> CleanupResult:
        return _close_alfworld_env(self._adapter._env)


def _event_logical_scene(event: Any) -> str | None:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    scene_name = metadata.get("sceneName")
    if (
        not isinstance(scene_name, str)
        or not scene_name.startswith("FloorPlan")
        or not scene_name.endswith("_physics")
    ):
        return None
    return scene_name.removesuffix("_physics")


def _persist_runtime_external_event(
    adapter: AlfworldEnvAdapter,
    event: ExternalEventRead,
) -> None:
    if adapter._frame_dir is None or event.raw_event_ref is None:
        return
    artifact_root = adapter._frame_dir.parent
    destination = artifact_root / event.raw_event_ref
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = external_event_evidence_payload(event.raw_event_ref, event)
    destination.write_text(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _external_event_read(
    event: Any,
    *,
    event_sequence: int,
    thor_env: Any | None = None,
) -> ExternalEventRead:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return ExternalEventRead(
            status="malformed",
            returned_action=None,
            action_success=None,
            pose=None,
            world_sha256=None,
            visibility_sha256=None,
            frame_sha256=None,
            objects=None,
            reachable_payload=None,
            strict_visible_exact_ids=(),
            bbox_areas=(),
            raw_event_ref=None,
            raw_event_sha256=None,
        )
    try:
        raw_metadata_payload = _canonical_json_projection(metadata)
        if not isinstance(raw_metadata_payload, dict):
            raise ValueError("event metadata projection is unreadable")
        raw_frame_bytes = _frame_bytes(getattr(event, "frame", None))
    except (KeyError, TypeError, ValueError, OverflowError):
        return ExternalEventRead(
            status="malformed",
            returned_action=None,
            action_success=None,
            pose=None,
            world_sha256=None,
            visibility_sha256=None,
            frame_sha256=None,
            objects=None,
            reachable_payload=None,
            strict_visible_exact_ids=(),
            bbox_areas=(),
            raw_event_ref=f"events/{event_sequence:04d}-malformed.json",
            raw_event_sha256=_event_hash(event),
        )
    try:
        pose = _oracle_pose_from_event(event)
        world_payload = _event_world_payload(metadata)
        world_sha256 = _portable_state_fingerprint(world_payload)
        control_payload = _alfworld_control_payload(thor_env)
        control_sha256 = _portable_state_fingerprint(control_payload)
        frame_sha256 = _frame_sha256(raw_frame_bytes)
        bbox_areas = _event_bbox_areas(event)
        visible_ids = {
            str(item["objectId"])
            for item in metadata.get("objects", [])
            if isinstance(item, dict)
            and isinstance(item.get("objectId"), str)
            and item.get("visible") is True
        }
        area_ids = {object_id for object_id, area in bbox_areas if area > 0}
        strict_visible = tuple(sorted(visible_ids & area_ids))
        visibility_sha256 = _portable_state_fingerprint(
            {"strict_visible_exact_ids": strict_visible, "bbox_areas": bbox_areas}
        )
        objects = _scene_scan_inputs(metadata.get("objects"))
        action = metadata.get("lastAction")
        returned_action = action if isinstance(action, str) and action else None
        action_success = metadata.get("lastActionSuccess")
        if not isinstance(action_success, bool):
            action_success = None
        reachable_payload = None
        if returned_action == "GetReachablePositions":
            reachable = metadata.get("actionReturn")
            if not isinstance(reachable, list):
                reachable = metadata.get("reachablePositions")
            if isinstance(reachable, list):
                reachable_payload = json.dumps(
                    reachable,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
        return ExternalEventRead(
            status="ok",
            returned_action=returned_action,
            action_success=action_success,
            pose=pose,
            world_sha256=world_sha256,
            visibility_sha256=visibility_sha256,
            frame_sha256=frame_sha256,
            objects=objects,
            reachable_payload=reachable_payload,
            strict_visible_exact_ids=strict_visible,
            bbox_areas=bbox_areas,
            raw_event_ref=f"events/{event_sequence:04d}-{returned_action or 'capture'}.json",
            raw_event_sha256=_raw_event_projection_sha256(
                raw_metadata_payload,
                raw_frame_bytes,
            ),
            control_sha256=control_sha256,
            world_payload=world_payload,
            control_payload=control_payload,
            raw_metadata_payload=raw_metadata_payload,
            raw_frame_bytes=raw_frame_bytes,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return ExternalEventRead(
            status="malformed",
            returned_action=None,
            action_success=None,
            pose=None,
            world_sha256=None,
            visibility_sha256=None,
            frame_sha256=_frame_sha256(raw_frame_bytes),
            objects=None,
            reachable_payload=None,
            strict_visible_exact_ids=(),
            bbox_areas=(),
            raw_event_ref=f"events/{event_sequence:04d}-malformed.json",
            raw_event_sha256=_raw_event_projection_sha256(
                raw_metadata_payload,
                raw_frame_bytes,
            ),
            raw_metadata_payload=raw_metadata_payload,
            raw_frame_bytes=raw_frame_bytes,
        )


def _oracle_pose_from_event(event: Any) -> OraclePose | None:
    pose = _agent_pose_from_event(event)
    if pose is None:
        return None
    return OraclePose(
        x=pose.x,
        y=pose.y,
        z=pose.z,
        rotation=pose.rotation,
        horizon=pose.horizon,
    )


def _scene_scan_inputs(value: Any) -> tuple[SceneObjectScanInput, ...] | None:
    if not isinstance(value, list):
        return None
    objects: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            return None
        object_id = item.get("objectId")
        object_type = item.get("objectType")
        position = item.get("position")
        if (
            not isinstance(object_id, str)
            or not object_id
            or not isinstance(object_type, str)
            or not object_type
            or not isinstance(position, dict)
        ):
            return None
        objects[object_id] = item

    def closed_ancestors(object_id: str) -> tuple[str, ...]:
        found: set[str] = set()
        pending = list(_string_tuple(objects[object_id].get("parentReceptacles")))
        visited: set[str] = set()
        while pending:
            parent_id = pending.pop()
            if parent_id in visited:
                continue
            visited.add(parent_id)
            parent = objects.get(parent_id)
            if parent is None:
                raise ValueError(f"unknown parent receptacle: {parent_id}")
            if parent.get("openable") is True and parent.get("isOpen") is False:
                found.add(parent_id)
            pending.extend(_string_tuple(parent.get("parentReceptacles")))
        return tuple(sorted(found))

    result: list[SceneObjectScanInput] = []
    for object_id in sorted(objects):
        item = objects[object_id]
        receptacle = item.get("receptacle")
        if not isinstance(receptacle, bool):
            receptacle = None
        position = item["position"]
        parent_ids = _string_tuple(item.get("parentReceptacles"))
        child_ids = _string_tuple(item.get("receptacleObjectIds"))
        freshness = _portable_state_fingerprint(
            {
                "object_id": object_id,
                "position": position,
                "rotation": item.get("rotation"),
                "parent_receptacle_ids": parent_ids,
                "receptacle_object_ids": child_ids,
            }
        )
        result.append(
            SceneObjectScanInput(
                exact_object_id=object_id,
                object_type=str(item["objectType"]),
                receptacle=receptacle,
                position=(
                    float(position["x"]),
                    float(position["y"]),
                    float(position["z"]),
                ),
                parent_receptacle_ids=parent_ids,
                receptacle_object_ids=child_ids,
                is_picked_up=item.get("isPickedUp") is True,
                closed_ancestor_exact_ids=closed_ancestors(object_id),
                pose_freshness_sha256=freshness,
            )
        )
    return tuple(result)


def _alfworld_control_payload(thor_env: Any | None) -> dict[str, Any]:
    task = getattr(thor_env, "task", None) if thor_env is not None else None
    traj = getattr(task, "traj", None)
    pddl_params = traj.get("pddl_params") if isinstance(traj, dict) else None
    goal_satisfied, goal_conditions = _view_invariant_goal_state(thor_env, task)
    payload = {
        "task_present": task is not None,
        "task_type": getattr(task, "task_type", None),
        "task_pddl_params": pddl_params,
        "step_num": getattr(task, "step_num", None),
        "goal_idx": getattr(task, "goal_idx", None),
        "finished": getattr(task, "finished", None),
        "goal_finished": getattr(task, "goal_finished", None),
        "num_subgoals": getattr(task, "num_subgoals", None),
        "goal_satisfied": goal_satisfied,
        "goal_conditions_met": goal_conditions,
        "goal_evaluation_visibility": "all_objects_visible",
        "cleaned_objects": _control_object_ids(thor_env, "cleaned_objects"),
        "cooled_objects": _control_object_ids(thor_env, "cooled_objects"),
        "heated_objects": _control_object_ids(thor_env, "heated_objects"),
    }
    return _canonical_json_projection(payload)


def _view_invariant_goal_state(
    thor_env: Any | None,
    task: Any,
) -> tuple[bool | None, Any]:
    if thor_env is None:
        return None, None
    goal_event = getattr(thor_env, "last_event", None)
    metadata = getattr(goal_event, "metadata", None)
    if isinstance(metadata, dict):
        normalized_metadata = dict(metadata)
        normalized_objects = []
        for item in metadata.get("objects", []):
            if not isinstance(item, dict):
                normalized_objects.append(item)
                continue
            normalized_item = dict(item)
            normalized_item["visible"] = True
            normalized_item.pop("distance", None)
            normalized_objects.append(normalized_item)
        normalized_metadata["objects"] = normalized_objects
        goal_event = SimpleNamespace(metadata=normalized_metadata)

    if task is not None:
        goal_reader = getattr(task, "goal_satisfied", None)
        conditions_reader = getattr(task, "goal_conditions_met", None)
        if not callable(goal_reader) or not callable(conditions_reader):
            raise ValueError("ALFWorld task goal evaluators are unavailable")
        try:
            satisfied = bool(goal_reader(goal_event))
            conditions = conditions_reader(goal_event)
        except Exception as exc:
            raise ValueError("ALFWorld task goal state is unreadable") from exc
    else:
        satisfied_reader = getattr(thor_env, "get_goal_satisfied", None)
        conditions_reader = getattr(thor_env, "get_goal_conditions_met", None)
        if not callable(satisfied_reader) or not callable(conditions_reader):
            raise ValueError("ALFWorld goal readers are unavailable")
        try:
            satisfied = bool(satisfied_reader())
            conditions = conditions_reader()
        except Exception as exc:
            raise ValueError("ALFWorld goal state is unreadable") from exc
    if not isinstance(conditions, list | tuple) or len(conditions) != 2:
        raise ValueError("ALFWorld goal conditions are unreadable")
    return satisfied, list(conditions)


def _control_object_ids(owner: Any | None, name: str) -> list[str] | None:
    if owner is None or not hasattr(owner, name):
        return None
    value = getattr(owner, name)
    if not isinstance(value, set | list | tuple):
        raise ValueError(f"ALFWorld control field {name} is unreadable")
    return sorted({str(item) for item in value})


def _canonical_json_projection(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
    )


def _event_world_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    objects = metadata.get("objects")
    if not isinstance(objects, list):
        raise ValueError("event objects are unreadable")
    held_geometry_fields = {
        "objectBounds",
        "position",
        "rotation",
    }
    normalized_objects = []
    for item in objects:
        if not isinstance(item, dict) or not isinstance(item.get("objectId"), str):
            raise ValueError("event object is unreadable")
        normalized_objects.append(
            {
                key: value
                for key, value in item.items()
                if key not in {"visible", "distance"}
                and not (item.get("isPickedUp") is True and key in held_geometry_fields)
            }
        )
    normalized_objects.sort(key=lambda item: str(item["objectId"]))
    extras = {
        key: value
        for key, value in metadata.items()
        if key
        not in {
            "actionReturn",
            "agent",
            "cameraPosition",
            "colorBounds",
            "colors",
            "currentTime",
            "errorCode",
            "errorMessage",
            "lastAction",
            "lastActionSuccess",
            "objects",
            "hand",
            "reachablePositions",
        }
    }
    return _canonical_json_projection({"objects": normalized_objects, "metadata": extras})


def _event_world_sha256(metadata: dict[str, Any]) -> str:
    return _portable_state_fingerprint(_event_world_payload(metadata))


def _event_bbox_areas(event: Any) -> tuple[tuple[str, float], ...]:
    detections = getattr(event, "instance_detections2D", None)
    if not isinstance(detections, dict):
        return ()
    result = []
    for object_id, box in detections.items():
        if not isinstance(object_id, str):
            raise ValueError("detection object ID is unreadable")
        area = _bbox_area_score(box)
        if not math.isfinite(area):
            raise ValueError("detection bbox area must be finite")
        if area > 0:
            result.append((object_id, area))
    return tuple(sorted(result))


def _frame_sha256(frame: Any) -> str | None:
    value = _frame_bytes(frame)
    return hashlib.sha256(value).hexdigest() if value is not None else None


def _frame_bytes(frame: Any) -> bytes | None:
    if frame is None:
        return None
    tobytes = getattr(frame, "tobytes", None)
    if callable(tobytes):
        value = tobytes()
        return value if isinstance(value, bytes) else bytes(value)
    if isinstance(frame, bytes | bytearray | memoryview):
        return bytes(frame)
    return None


def _raw_event_projection_sha256(
    metadata_payload: dict[str, Any],
    frame_bytes: bytes | None,
) -> str:
    encoded = json.dumps(
        metadata_payload,
        allow_nan=False,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded + (frame_bytes or b"")).hexdigest()


def _string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError("object containment IDs are unreadable")
    return tuple(sorted(set(value)))


def _portable_state_fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _close_alfworld_env(env: Any) -> CleanupResult:
    for name in ("close", "stop"):
        closer = getattr(env, name, None)
        if not callable(closer):
            continue
        try:
            closer()
        except Exception:
            return CleanupResult(status="failed", evidence_ref=None)
        return CleanupResult(status="succeeded", evidence_ref=None)
    return CleanupResult(status="unverified", evidence_ref=None)


def _latest_thor_frame(env: Any) -> Any | None:
    pending = [env]
    seen: set[int] = set()
    while pending:
        item = pending.pop(0)
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        event = getattr(item, "last_event", None)
        frame = getattr(event, "frame", None)
        if frame is not None:
            return frame
        envs = getattr(item, "envs", None)
        if isinstance(envs, list | tuple) and envs:
            pending.append(envs[0])
        for attr in ("env", "controller"):
            nested = getattr(item, attr, None)
            if nested is not None:
                pending.append(nested)
    return None


def _unsupported_task_type(selection_entry: TrialSelectionEntry | None) -> str | None:
    if selection_entry is None:
        return None
    try:
        identity = json.loads(selection_entry.goal_identity)
    except (TypeError, json.JSONDecodeError):
        return None
    task_type = identity.get("task_type") if isinstance(identity, dict) else None
    if task_type == "pick_and_place_with_movable_recep":
        return task_type
    return None
