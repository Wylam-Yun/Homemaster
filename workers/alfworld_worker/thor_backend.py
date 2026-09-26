"""Real ALFWorld/AI2-THOR backend owned by the isolated worker."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any


class ThorBackend:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self._env: Any | None = None
        self._state: dict[str, Any] | None = None
        self._state_sequence = 0
        self._scene_generation = 1
        self._goal_generation = 0
        self._frame_dir = Path(str(payload.get("frame_dir", "."))).resolve()
        self._trial_identity: dict[str, Any] = {}
        self._scene_objects: list[dict[str, Any]] = []
        # These IDs come from the selected ALFRED trial plan and are checked
        # against the Oracle controller's stable identity map before use.
        self._goal_object_ids: dict[str, str] = {}
        self._grounded_object_ids: dict[tuple[str, str, bool], str] = {}

    def reset(self) -> dict[str, Any]:
        import yaml
        from alfworld.agents.environment import get_environment

        config_path = Path(str(self._payload["config_path"])).resolve(strict=True)
        data_root = Path(str(self._payload["data_root"])).resolve(strict=True)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("ALFWorld config must be an object")
        config = _replace_data_root(config, data_root)
        split = str(self._payload.get("split", "valid_seen"))
        train_eval = {
            "train": "train",
            "valid_seen": "eval_in_distribution",
            "valid_unseen": "eval_out_of_distribution",
        }.get(split)
        if train_eval is None:
            raise ValueError(f"unsupported split: {split}")
        env_factory = get_environment("AlfredThorEnv")
        environment = env_factory(config, train_eval=train_eval)
        trial_file = Path(str(self._payload["trial_path"])).resolve(strict=True)
        trial_path = str(trial_file)
        self._trial_identity = _trial_identity(trial_file, str(self._payload["trial_id"]))
        self._goal_object_ids = _plan_object_ids(
            json.loads(trial_file.read_text(encoding="utf-8"))
        )
        self._grounded_object_ids.clear()
        if hasattr(environment, "json_file_list"):
            environment.json_file_list = [trial_path]
        if hasattr(environment, "num_games"):
            environment.num_games = 1
        self._env = environment.init_env(batch_size=1)
        # init_env calls get_env_paths(), which replaces json_file_list. Pin the
        # selected trial again on both wrapper layers before the first reset.
        _pin_trial(environment, trial_path)
        _pin_trial(self._env, trial_path)
        if hasattr(self._env, "seed"):
            self._env.seed(int(self._payload.get("seed", 42)))
        observations, info = self._env.reset()
        actual_gamefile = _first_text(
            info.get("extra.gamefile") if isinstance(info, dict) else None
        )
        if actual_gamefile:
            actual_trial = (Path(actual_gamefile) / "traj_data.json").resolve()
            if actual_trial != trial_file:
                raise RuntimeError(
                    f"worker loaded unexpected trial: expected {trial_file}, got {actual_trial}"
                )
        self._state_sequence = 1
        self._goal_generation = 1
        self._state = self._make_state(observations, info, command=None)
        actual_scene = self._state.get("logical_scene")
        if actual_scene != self._trial_identity["expected_logical_scene"]:
            raise RuntimeError(
                "worker loaded unexpected logical scene: "
                f"expected {self._trial_identity['expected_logical_scene']}, got {actual_scene}"
            )
        return self._result()

    def set_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Advance the ALFWorld goal while retaining the loaded THOR scene."""

        if self._env is None or self._state is None:
            raise RuntimeError("backend has not been reset")
        trial_id = payload.get("trial_id")
        if not isinstance(trial_id, str) or not trial_id:
            raise ValueError("set_task requires trial_id")
        trial_root = (Path(str(self._payload["data_root"])) / "json_2.1.1").resolve(
            strict=True
        )
        trial_file = (trial_root / trial_id).resolve(strict=True)
        try:
            trial_file.relative_to(trial_root)
        except ValueError as exc:
            raise ValueError("set_task trial escapes data root") from exc
        trial_data = json.loads(trial_file.read_text(encoding="utf-8"))
        if not isinstance(trial_data, dict):
            raise ValueError("set_task trial must be an object")
        identity = _trial_identity(trial_file, trial_id)
        for key in (
            "trial_sha256",
            "expected_logical_scene",
            "goal_identity",
            "goal_fingerprint",
        ):
            expected = payload.get(key)
            if not isinstance(expected, str) or expected != identity[key]:
                raise ValueError(f"set_task identity mismatch for {key}")
        current_scene = self._state.get("logical_scene")
        if current_scene != identity["expected_logical_scene"]:
            raise ValueError(
                "set_task scene mismatch: "
                f"expected {identity['expected_logical_scene']}, got {current_scene}"
            )

        before_world_sha256 = _state_world_sha256(self._state)
        frame_dir = payload.get("frame_dir")
        if isinstance(frame_dir, str) and frame_dir:
            self._frame_dir = Path(frame_dir).resolve()
            self._frame_dir.mkdir(parents=True, exist_ok=True)
        thor_env = self._env.envs[0].env
        import alfworld.agents

        args = SimpleNamespace(
            reward_config=str(Path(alfworld.agents.__path__[0]) / "config" / "rewards.json")
        )
        thor_env.set_task(trial_data, args, reward_type="dense")
        self._goal_object_ids = _plan_object_ids(trial_data)
        self._grounded_object_ids.clear()
        task_desc = _task_description(trial_data)
        conditions = thor_env.get_goal_conditions_met()
        if not isinstance(conditions, (tuple, list)) or len(conditions) != 2:
            raise ValueError("set_task goal condition state is unreadable")
        goal_rate = _goal_rate(conditions)
        won = bool(thor_env.get_goal_satisfied())
        self._trial_identity = identity
        self._payload["trial_id"] = trial_id
        self._goal_generation += 1
        self._state_sequence += 1
        self._state = self._make_state(
            [task_desc],
            {
                "task": task_desc,
                "won": [won],
                "goal_condition_success_rate": [goal_rate, 1.0],
                "admissible_commands": [
                    self._env.envs[0].controller.get_admissible_commands()
                ],
            },
            command=None,
            reward=0.0,
            done=False,
        )
        after_world_sha256 = _state_world_sha256(self._state)
        return {
            **self._result(),
            "before_world_sha256": before_world_sha256,
            "after_world_sha256": after_world_sha256,
            "benchmark_control_action_count": 1,
        }

    def observe(self) -> dict[str, Any]:
        if self._state is None:
            raise RuntimeError("backend has not been reset")
        return self._result()

    def act(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._env is None or self._state is None:
            raise RuntimeError("backend has not been reset")
        kind = str(payload.get("kind", ""))
        tool_name = str(payload.get("tool_name", "robot_go_to"))
        tool_args = dict(payload.get("tool_args") or {})
        if kind == "go_to":
            target = str(payload.get("target", "")).strip()
            object_ref = self._find_object(target, action="go_to")
            if object_ref is not None and not object_ref.get("receptacle"):
                return self._navigate_object(
                    object_ref=object_ref,
                    target=target,
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
            command = self._resolve_command("go_to", target, {})
            action = "navigate"
        elif kind == "manipulate":
            action = str(payload.get("action", "")).strip().lower()
            object_label = str(tool_args.get("object") or "").strip()
            target = str(
                tool_args.get("target_receptacle")
                or tool_args.get("target")
                or object_label
            ).strip()
            command = self._resolve_command(action, target, tool_args)
            if action in {"take", "put", "open", "close", "use"}:
                return self._direct_manipulation(
                    action=action,
                    tool_args=tool_args,
                    command=command,
                    tool_name=tool_name,
                )
        else:
            raise ValueError("unsupported action kind")
        before = self._state
        observations, reward, done, info = self._env.step([command])
        self._state_sequence += 1
        self._state = self._make_state(
            observations, info, command=command, reward=reward, done=done
        )
        feedback = _first_text(observations)
        success = (
            not _info_bool(info, "invalid", default=False)
            and "nothing happens" not in feedback.lower()
        )
        result = {
            "state": self._state,
            "state_sequence": self._state_sequence,
            "step": _step(tool_name, tool_args, command, success, self._state, action, before),
        }
        result["external_return_code"] = 0 if success else 1
        return result

    def _direct_manipulation(
        self,
        *,
        action: str,
        tool_args: dict[str, Any],
        command: str,
        tool_name: str,
    ) -> dict[str, Any]:
        thor_env = self._env.envs[0].env
        before = dict(self._state or {})
        object_ref = self._find_object(
            str(tool_args.get("object") or ""), action=action, movable=True
        )
        target_ref = self._find_object(
            str(tool_args.get("target_receptacle") or tool_args.get("target") or ""),
            action=action,
            receptacle=True,
        )
        if action == "put":
            target_ref = self._current_receptacle() or target_ref
        if action == "use":
            target_ref = object_ref
        navigation_actions = 0
        action_ref = target_ref if action in {"open", "close", "use"} else object_ref
        if action_ref is not None:
            navigation_actions = self._ensure_visible(action_ref)
        if action == "take":
            if object_ref is None:
                raise ValueError("take target object is not present in the scene")
            thor_action = {
                "action": "PickupObject",
                "objectId": object_ref["objectId"],
                "forceAction": True,
            }
        elif action == "put":
            inventory = getattr(thor_env.last_event, "metadata", {}).get("inventoryObjects", [])
            held = inventory[0] if inventory else object_ref
            if not isinstance(held, dict) or target_ref is None:
                raise ValueError("put requires an inventory object and target receptacle")
            thor_action = {
                "action": "PutObject",
                "objectId": held.get("objectId"),
                "receptacleObjectId": target_ref["objectId"],
                "forceAction": True,
            }
        elif action == "use":
            if target_ref is None:
                raise ValueError(f"{action} target object is not present in the scene")
            thor_action = {
                "action": "ToggleObjectOn",
                "objectId": target_ref["objectId"],
                "forceAction": True,
            }
        else:
            if target_ref is None:
                raise ValueError(f"{action} target receptacle is not present in the scene")
            thor_action = {
                "action": "OpenObject" if action == "open" else "CloseObject",
                "objectId": target_ref["objectId"],
                "forceAction": True,
            }
        event = thor_env.step(thor_action)
        metadata = getattr(event, "metadata", {})
        success = bool(metadata.get("lastActionSuccess"))
        feedback = (
            f"Executed {command}."
            if success
            else str(metadata.get("errorMessage") or "Nothing happens.")
        )
        self._state_sequence += 1
        info = {
            "won": [bool(thor_env.get_goal_satisfied())],
            "goal_condition_success_rate": [
                _goal_rate(thor_env.get_goal_conditions_met())
            ],
            "admissible_commands": [self._env.envs[0].controller.get_admissible_commands()],
        }
        self._state = self._make_state(
            [feedback], info, command=command, reward=0.0, done=False
        )
        step = _step(
            tool_name,
            tool_args,
            command,
            success,
            self._state,
            action,
            before,
            backend_action_count=1 + navigation_actions,
        )
        result = {
            "state": self._state,
            "state_sequence": self._state_sequence,
            "step": step,
            "external_return_code": 0 if success else 1,
        }
        return result

    def _find_object(
        self,
        label: str,
        *,
        action: str = "",
        movable: bool = False,
        receptacle: bool = False,
    ) -> dict[str, Any] | None:
        wanted = label.strip().lower()
        if not wanted:
            return None
        controller = self._oracle_controller()
        if controller is None:
            return None
        entries = getattr(controller, "receptacles" if receptacle else "objects", {})
        if not isinstance(entries, dict):
            return None

        cache_key = (action, wanted, receptacle)
        grounded_object_ids = getattr(self, "_grounded_object_ids", None)
        if not isinstance(grounded_object_ids, dict):
            grounded_object_ids = {}
            self._grounded_object_ids = grounded_object_ids
        cached_id = grounded_object_ids.get(cache_key)
        goal_object_ids = getattr(self, "_goal_object_ids", {})
        if not isinstance(goal_object_ids, dict):
            goal_object_ids = {}
        if cached_id:
            cached = self._controller_entry(entries, cached_id)
            if cached is not None:
                return self._fresh_object(cached)

        expected_key = f"{action}_receptacle" if receptacle else action
        expected_id = goal_object_ids.get(expected_key)
        if expected_id:
            expected = self._controller_entry(entries, expected_id)
            if expected is not None and _controller_label_matches(expected, wanted):
                grounded_object_ids[cache_key] = expected_id
                return self._fresh_object(expected)
            # Oracle's object registry only contains objects observed in a
            # controller frame. A trial plan ID is already a stable ALFRED
            # identity; accept it only when the current THOR metadata contains
            # that exact ID and its label matches the requested target.
            planned = self._metadata_object(expected_id)
            if planned is not None and _metadata_label_matches(planned, wanted):
                grounded_object_ids[cache_key] = expected_id
                return planned

        if action == "go_to":
            planned_ids = {
                value for value in goal_object_ids.values() if isinstance(value, str)
            }
            planned = []
            for object_id in sorted(planned_ids):
                stable = self._controller_entry(entries, object_id)
                planned.append(stable if stable is not None else self._metadata_object(object_id))
            planned = [
                item
                for item in planned
                if item is not None
                and (
                    _controller_label_matches(item, wanted)
                    or _metadata_label_matches(item, wanted)
                )
            ]
            if len(planned) == 1:
                object_id = planned[0].get("object_id") or planned[0].get("objectId")
                if isinstance(object_id, str):
                    grounded_object_ids[cache_key] = object_id
                    return self._fresh_object(planned[0])

        candidates = [
            value
            for value in entries.values()
            if isinstance(value, dict) and _controller_label_matches(value, wanted)
        ]
        if len(candidates) != 1:
            if len(candidates) > 1:
                raise ValueError(
                    f"ambiguous controller grounding for {label!r}: "
                    f"{len(candidates)} stable objects"
                )
            return None
        selected = candidates[0]
        object_id = selected.get("object_id")
        if not isinstance(object_id, str) or not object_id:
            return None
            grounded_object_ids[cache_key] = object_id
        return self._fresh_object(selected)

    def _oracle_controller(self) -> Any | None:
        try:
            return self._env.envs[0].controller
        except (AttributeError, IndexError, TypeError):
            return None

    def _ensure_visible(self, object_ref: dict[str, Any]) -> int:
        """Use the Oracle's cached camera pose before an off-screen action."""

        if not self._payload.get("allow_offscreen_object_navigation", False):
            return 0
        current = self._metadata_object(str(object_ref.get("objectId") or ""))
        if isinstance(current, dict) and current.get("visible") is True:
            return 0
        navigation_actions = self._teleport_to_object_visibility(object_ref)
        if navigation_actions:
            return navigation_actions
        location = object_ref.get("loc") or object_ref.get("locs")
        controller = self._oracle_controller()
        if not isinstance(location, dict) and isinstance(object_ref.get("parentReceptacles"), list):
            receptacles = getattr(controller, "receptacles", {}) if controller else {}
            for parent_id in object_ref["parentReceptacles"]:
                parent = receptacles.get(parent_id) if isinstance(receptacles, dict) else None
                if isinstance(parent, dict) and isinstance(parent.get("locs"), dict):
                    location = parent["locs"]
                    break
        if not isinstance(location, dict):
            return 0
        thor_env = self._env.envs[0].env
        event = controller.navigate(location) if controller is not None else thor_env.step(location)
        metadata = getattr(event, "metadata", {})
        if isinstance(metadata.get("objects"), list):
            self._scene_objects = [item for item in metadata["objects"] if isinstance(item, dict)]
        if not metadata.get("lastActionSuccess"):
            raise ValueError(
                "Oracle navigation to target failed: "
                f"{metadata.get('errorMessage') or 'unknown error'}"
            )
        return 1

    def _controller_entry(self, entries: dict[str, Any], object_id: str) -> dict[str, Any] | None:
        value = entries.get(object_id)
        return value if isinstance(value, dict) else None

    def _fresh_object(self, stable: dict[str, Any]) -> dict[str, Any]:
        object_id = stable.get("object_id")
        if isinstance(object_id, str):
            for item in self._scene_objects:
                if isinstance(item, dict) and item.get("objectId") == object_id:
                    return {**stable, **item, "objectId": object_id}
            if self._state:
                for item in self._state.get("objects", []):
                    if isinstance(item, dict) and item.get("objectId") == object_id:
                        return {**stable, **item, "objectId": object_id}
        return {
            **stable,
            "objectId": object_id,
            "objectType": stable.get("object_type"),
            "name": stable.get("num_id"),
        }

    def _navigate_object(
        self,
        *,
        object_ref: dict[str, Any],
        target: str,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any]:
        thor_env = self._env.envs[0].env
        controller = self._oracle_controller()
        navigation_actions = 0
        if self._payload.get("allow_offscreen_object_navigation", False):
            navigation_actions = self._teleport_to_object_visibility(object_ref)
        if navigation_actions:
            command = f"go to {target}"
            before = self._state
            self._state_sequence += 1
            self._state = self._make_state(
                [f"Arrived at {target}."],
                {
                    "won": [bool(thor_env.get_goal_satisfied())],
                    "goal_condition_success_rate": [
                        _goal_rate(thor_env.get_goal_conditions_met())
                    ],
                    "admissible_commands": [
                        self._env.envs[0].controller.get_admissible_commands()
                    ],
                },
                command=command,
                reward=0.0,
                done=False,
            )
            return {
                "state": self._state,
                "state_sequence": self._state_sequence,
                "step": _step(
                    tool_name,
                    tool_args,
                    command,
                    True,
                    self._state,
                    "navigate",
                    before,
                    backend_action_count=navigation_actions,
                ),
                "external_return_code": 0,
            }
        location = object_ref.get("loc") or object_ref.get("locs")
        if not isinstance(location, dict):
            parent_ids = object_ref.get("parentReceptacles")
            if isinstance(parent_ids, list) and controller is not None:
                receptacles = getattr(controller, "receptacles", {})
                for parent_id in parent_ids:
                    parent = receptacles.get(parent_id) if isinstance(receptacles, dict) else None
                    if isinstance(parent, dict) and isinstance(parent.get("locs"), dict):
                        location = parent["locs"]
                        break
        if not isinstance(location, dict):
            command = self._resolve_command("go_to", target, {})
            return self._navigate_with_command(command, tool_name, tool_args)
        event = controller.navigate(location) if controller is not None else thor_env.step(location)
        metadata = getattr(event, "metadata", {})
        success = bool(metadata.get("lastActionSuccess"))
        if isinstance(metadata.get("objects"), list):
            self._scene_objects = [item for item in metadata["objects"] if isinstance(item, dict)]
        command = f"go to {target}"
        before = self._state
        self._state_sequence += 1
        feedback = "Arrived at target." if success else str(
            metadata.get("errorMessage") or "Nothing happens."
        )
        self._state = self._make_state(
            [feedback],
            {
                "won": [bool(thor_env.get_goal_satisfied())],
                "goal_condition_success_rate": [_goal_rate(thor_env.get_goal_conditions_met())],
                "admissible_commands": [self._env.envs[0].controller.get_admissible_commands()],
            },
            command=command,
            reward=0.0,
            done=False,
        )
        return {
            "state": self._state,
            "state_sequence": self._state_sequence,
            "step": _step(
                tool_name,
                tool_args,
                command,
                success,
                self._state,
                "navigate",
                before,
            ),
            "external_return_code": 0 if success else 1,
        }

    def _teleport_to_object_visibility(self, object_ref: dict[str, Any]) -> int:
        """Freeze deterministic THOR poses and stop at the first visible target."""

        thor_env = self._env.envs[0].env
        object_id = str(object_ref.get("objectId") or "")
        current = self._metadata_object(object_id)
        if not object_id or not isinstance(current, dict):
            return 0
        if current.get("visible") is True:
            return 0
        metadata = getattr(thor_env.last_event, "metadata", {})
        agent = metadata.get("agent", {}) if isinstance(metadata, dict) else {}
        position = agent.get("position", {}) if isinstance(agent, dict) else {}
        target_position = current.get("position", {})
        try:
            agent_y = float(position["y"])
            target_x = float(target_position["x"])
            target_y = float(target_position.get("y", agent_y))
            target_z = float(target_position["z"])
        except (KeyError, TypeError, ValueError):
            return 0
        reachable_event = thor_env.step({"action": "GetReachablePositions"})
        reachable_metadata = getattr(reachable_event, "metadata", {})
        if reachable_metadata.get("lastActionSuccess") is not True:
            raise ValueError("GetReachablePositions was rejected by THOR")
        reachable = reachable_metadata.get("reachablePositions")
        if not isinstance(reachable, list):
            raise ValueError("GetReachablePositions returned no positions")
        points = []
        for item in reachable:
            if not isinstance(item, dict):
                continue
            try:
                points.append((float(item["x"]), float(item["z"])))
            except (KeyError, TypeError, ValueError):
                continue
        points.sort(
            key=lambda point: (
                (point[0] - target_x) ** 2 + (point[1] - target_z) ** 2,
                point[0],
                point[1],
            )
        )
        points = points[:12]
        actions = []
        for point in points:
            base_rotation = _quantized_rotation(
                target_x - point[0], target_z - point[1]
            )
            base_horizon = _quantized_horizon(
                target_y - agent_y,
                math.hypot(target_x - point[0], target_z - point[1]),
            )
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
                [base_horizon, base_horizon - 15, base_horizon + 15, 0, 15, 30, 45, 60]
            )
            for rotation in rotations:
                for horizon in horizons:
                    if -30 <= horizon <= 60:
                        actions.append(
                            {
                                "action": "TeleportFull",
                                "x": point[0],
                                "y": agent_y,
                                "z": point[1],
                                "rotateOnTeleport": True,
                                "rotation": rotation,
                                "horizon": horizon,
                            }
                        )
        for index, action in enumerate(actions, start=2):
            event = thor_env.step(action)
            event_metadata = getattr(event, "metadata", {})
            if event_metadata.get("lastActionSuccess") is not True:
                continue
            objects = event_metadata.get("objects")
            if isinstance(objects, list):
                self._scene_objects = [item for item in objects if isinstance(item, dict)]
            target = self._metadata_object(object_id)
            if isinstance(target, dict) and target.get("visible") is True:
                return index
        raise ValueError(f"Oracle navigation could not make target visible: {object_id}")

    def _navigate_with_command(
        self, command: str, tool_name: str, tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        before = self._state
        observations, reward, done, info = self._env.step([command])
        self._state_sequence += 1
        self._state = self._make_state(
            observations, info, command=command, reward=reward, done=done
        )
        feedback = _first_text(observations)
        success = (
            not _info_bool(info, "invalid", default=False)
            and "nothing happens" not in feedback.lower()
        )
        return {
            "state": self._state,
            "state_sequence": self._state_sequence,
            "step": _step(
                tool_name,
                tool_args,
                command,
                success,
                self._state,
                "navigate",
                before,
            ),
            "external_return_code": 0 if success else 1,
        }

    def _metadata_object(self, object_id: str) -> dict[str, Any] | None:
        for item in self._scene_objects:
            if isinstance(item, dict) and item.get("objectId") == object_id:
                return item
        if self._state:
            for item in self._state.get("objects", []):
                if isinstance(item, dict) and item.get("objectId") == object_id:
                    return item
        return None

    def _current_receptacle(self) -> dict[str, Any] | None:
        try:
            controller = self._env.envs[0].controller
            current = str(controller.curr_recep)
            for value in controller.receptacles.values():
                if value.get("num_id") == current:
                    return {
                        "objectId": value.get("object_id"),
                        "objectType": current,
                        "receptacle": True,
                    }
        except (AttributeError, KeyError):
            return None
        return None

    def _resolve_command(
        self, action: str, target: str, tool_args: dict[str, Any]
    ) -> str:
        commands = self._state.get("admissible_commands", []) if self._state else []
        commands = [item for item in commands if isinstance(item, str)]
        lowered_target = target.lower()
        object_label = str(tool_args.get("object") or "").strip().lower()
        source_label = str(tool_args.get("source_receptacle") or "").strip().lower()
        if action == "go_to":
            candidates = [item for item in commands if item.lower().startswith("go to ")]
            for command in candidates:
                if lowered_target and lowered_target in command.lower():
                    return command
            parent_type = self._parent_type_for_object(lowered_target)
            if parent_type:
                parent_command = self._parent_command_for_object(lowered_target, parent_type)
                if parent_command:
                    return parent_command
        elif action == "take":
            candidates = [item for item in commands if item.lower().startswith("take ")]
            for command in candidates:
                value = command.lower()
                if object_label and object_label in value and (
                    not source_label or source_label in value
                ):
                    return command
        elif action == "put":
            candidates = [item for item in commands if item.lower().startswith(("move ", "put "))]
            for command in candidates:
                value = command.lower()
                if object_label and object_label in value and lowered_target in value:
                    return command
        if action == "go_to":
            return f"go to {target}"
        if action == "take" and object_label:
            object_name = self._numbered_label(object_label)
            source_name = source_label or self._numbered_parent_label(object_label)
            if object_name and source_name:
                return f"take {object_name} from {source_name}"
        if action == "put" and object_label and lowered_target:
            object_name = self._numbered_label(object_label)
            target_name = self._numbered_label(lowered_target)
            if object_name and target_name:
                return f"move {object_name} to {target_name}"
        return _manipulation_command(
            action, target, object_label=str(tool_args.get("object") or "")
        )

    def _parent_type_for_object(self, target: str) -> str | None:
        if self._state is None:
            return None
        for item in self._state.get("objects", []):
            if not isinstance(item, dict):
                continue
            labels = {
                str(item.get("objectType") or "").lower(),
                str(item.get("name") or "").lower(),
            }
            if target not in labels and not any(target in label for label in labels if label):
                continue
            parent_ids = item.get("parentReceptacles")
            if not isinstance(parent_ids, list):
                return None
            for parent_id in parent_ids:
                for parent in self._state.get("objects", []):
                    if isinstance(parent, dict) and parent.get("objectId") == parent_id:
                        return str(parent.get("objectType") or parent.get("name") or "").lower()
        return None

    def _parent_command_for_object(self, target: str, parent_type: str) -> str | None:
        if self._state is None:
            return None
        parent_id = None
        for item in self._state.get("objects", []):
            if not isinstance(item, dict):
                continue
            labels = {
                str(item.get("objectType") or "").lower(),
                str(item.get("name") or "").lower(),
            }
            if target in labels or any(target in label for label in labels if label):
                parents = item.get("parentReceptacles")
                if isinstance(parents, list) and parents:
                    parent_id = parents[0]
                    break
        if not isinstance(parent_id, str):
            return None
        typed_parents = [
            item
            for item in self._state.get("objects", [])
            if isinstance(item, dict)
            and str(item.get("objectType") or "").lower() == parent_type
        ]
        try:
            ordinal = next(
                index for index, item in enumerate(typed_parents)
                if item.get("objectId") == parent_id
            )
        except StopIteration:
            return None
        commands = [
            item
            for item in self._state.get("admissible_commands", [])
            if isinstance(item, str)
            and item.lower().startswith(f"go to {parent_type} ")
        ]
        if ordinal < len(commands):
            return commands[ordinal]
        return f"go to {parent_type} {ordinal + 1}"

    def _numbered_label(self, target: str) -> str | None:
        if self._state is None:
            return None
        matches = [
            item
            for item in self._state.get("objects", [])
            if isinstance(item, dict)
            and (
                str(item.get("objectType") or "").lower() == target
                or target in str(item.get("name") or "").lower()
            )
        ]
        if not matches:
            return None
        object_type = str(matches[0].get("objectType") or target).lower()
        ordinal = next(
            (index for index, item in enumerate(matches) if item is matches[0]),
            0,
        )
        return f"{object_type} {ordinal + 1}"

    def _numbered_parent_label(self, target: str) -> str:
        parent_type = self._parent_type_for_object(target) or ""
        if not parent_type:
            return ""
        command = self._parent_command_for_object(target, parent_type) or ""
        return command.removeprefix("go to ")

    def close(self) -> dict[str, Any]:
        close = getattr(self._env, "close", None)
        if callable(close):
            close()
        self._env = None
        return {"closed": True, "cleanup_status": "succeeded"}

    def identity(self) -> dict[str, Any]:
        import importlib.util
        import sys

        spec = importlib.util.find_spec("alfworld")
        return {
            "worker_version": "3.5.0",
            "python_executable": sys.executable,
            "alfworld_origin": str(Path(spec.origin).resolve()) if spec and spec.origin else "",
            "ai2thor_version": _module_version("ai2thor"),
            "allow_offscreen_object_navigation": bool(
                self._payload.get("allow_offscreen_object_navigation", False)
            ),
            "capabilities": ["reset", "set_task", "observe", "act", "close"],
            "state": self._state or _empty_state(),
            "state_sequence": self._state_sequence,
            "scene_generation": self._scene_generation,
            "goal_generation": self._goal_generation,
            **self._trial_identity,
        }

    def _result(self) -> dict[str, Any]:
        return {
            "state": self._state or _empty_state(),
            "state_sequence": self._state_sequence,
            "scene_generation": self._scene_generation,
            "goal_generation": self._goal_generation,
            "logical_scene": (
                self._state.get("logical_scene")
                if isinstance(self._state, dict)
                else None
            ),
            **self._trial_identity,
        }

    def _make_state(
        self,
        observations: Any,
        info: Any,
        *,
        command: str | None,
        reward: Any = 0.0,
        done: Any = False,
    ) -> dict[str, Any]:
        observation = _first_text(observations)
        task = _first_text(info.get("task") if isinstance(info, dict) else None) or observation
        won = _info_bool(info, "won", default=False)
        frame_artifact = self._write_frame()
        event = self._last_event()
        metadata = getattr(event, "metadata", {}) if event is not None else {}
        objects = metadata.get("objects", []) if isinstance(metadata, dict) else []
        if isinstance(objects, list) and objects:
            self._scene_objects = [item for item in objects if isinstance(item, dict)]
        agent_pose = metadata.get("agent", {}) if isinstance(metadata, dict) else {}
        logical_scene = metadata.get("sceneName") if isinstance(metadata, dict) else None
        if isinstance(logical_scene, str) and logical_scene.endswith("_physics"):
            logical_scene = logical_scene.removesuffix("_physics")
        return {
            "episode_id": str(self._payload.get("trial_id", "alfworld-episode")),
            "task": task,
            "logical_scene": logical_scene if isinstance(logical_scene, str) else None,
            "observation": observation,
            "inventory": [
                item.get("objectType") or item.get("name")
                for item in objects
                if isinstance(item, dict) and item.get("isPickedUp")
            ],
            "last_command": command,
            "last_feedback": observation,
            "reward": _first_number(reward),
            "done": _first_bool(done),
            "won": won,
            "goal_condition_success_rate": _info_float(
                info, "goal_condition_success_rate", default=1.0 if won else 0.0
            ),
            "frame_path": frame_artifact.get("path") if frame_artifact else None,
            "frame_artifact": frame_artifact,
            "objects": objects if isinstance(objects, list) else [],
            "agent_pose": agent_pose if isinstance(agent_pose, dict) else {},
            "step_index": self._state_sequence - 1,
            "invalid_action_count": 0,
            "admissible_commands": _first_text_list(
                info.get("admissible_commands") if isinstance(info, dict) else None
            ),
        }

    def _write_frame(self) -> dict[str, str | int] | None:
        try:
            event = self._last_event()
            frame = getattr(event, "frame", None)
            if frame is None:
                return None
            from PIL import Image

            self._frame_dir.mkdir(parents=True, exist_ok=True)
            path = self._frame_dir / f"frame-{self._state_sequence:06d}.png"
            Image.fromarray(frame).save(path)
            content = path.read_bytes()
            import hashlib

            return {
                "path": str(path),
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mime_type": "image/png",
            }
        except Exception:
            return None

    def _last_event(self) -> Any | None:
        try:
            return self._env.envs[0].env.last_event
        except Exception:
            return None


def _step(
    tool_name: str,
    tool_args: dict[str, Any],
    command: str,
    success: bool,
    state: dict[str, Any],
    action: str,
    before: dict[str, Any],
    backend_action_count: int = 1,
) -> dict[str, Any]:
    target = str(tool_args.get("target") or tool_args.get("target_receptacle") or "") or None
    obj = str(tool_args.get("object") or "") or None
    error = None if success else "harness_operation_failure"
    return {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "translated_command": command,
        "success": success,
        "state": state,
        "execution_feedback": {
            "success": success,
            "action": action,
            "object": obj,
            "target": target,
            "inventory": None,
            "inventory_status": "not_applicable",
            "object_state": None,
            "object_state_status": "not_applicable",
            "target_state": "visible" if success and target else None,
            "target_state_status": "ok" if success and target else "not_applicable",
            "state_changed": state.get("step_index", 0) > before.get("step_index", 0),
            "state_read_status": "ok",
            "error": error,
            "terminal": not success,
            "classification": "harness_operation_failure" if not success else None,
            "score_eligible": success,
            "detail_code": error,
        },
        "feedback": state.get("last_feedback"),
        "backend_action_count": backend_action_count,
        "trace_events": [],
    }


def _manipulation_command(action: str, target: str, *, object_label: str = "") -> str:
    if action not in {"take", "put", "open", "close", "use", "slice", "heat", "cool", "clean"}:
        raise ValueError(f"unsupported manipulation action: {action}")
    if action == "put" and object_label and target != object_label:
        return f"move {object_label} to {target}"
    return f"{action} {target}".strip()


def _replace_data_root(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _replace_data_root(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_data_root(item, root) for item in value]
    if isinstance(value, str):
        for marker in ("${ALFWORLD_DATA}", "$ALFWORLD_DATA"):
            if value == marker:
                return str(root)
            if value.startswith(marker + "/"):
                return str(root / value[len(marker) + 1 :])
    return value


def _pin_trial(environment: Any, trial_path: str) -> None:
    if hasattr(environment, "json_file_list"):
        environment.json_file_list = [trial_path]
    if hasattr(environment, "num_games"):
        environment.num_games = 1


def _trial_identity(trial_path: Path, trial_id: str) -> dict[str, str]:
    trial_bytes = trial_path.read_bytes()
    payload = json.loads(trial_bytes)
    if not isinstance(payload, dict):
        raise ValueError("trial JSON must be an object")
    scene = payload.get("scene")
    pddl_params = payload.get("pddl_params")
    task_type = payload.get("task_type")
    if not isinstance(scene, dict) or not isinstance(scene.get("floor_plan"), str):
        raise ValueError("trial logical scene is unreadable")
    if not isinstance(task_type, str) or not task_type or not isinstance(pddl_params, dict):
        raise ValueError("trial goal identity is unreadable")
    goal = {
        key: pddl_params.get(key)
        for key in (
            "object_target",
            "parent_target",
            "toggle_target",
            "mrecep_target",
            "object_sliced",
        )
    }
    if not isinstance(goal["object_target"], str) or not goal["object_target"]:
        raise ValueError("trial goal object identity is unreadable")
    goal_identity = json.dumps(
        {"pddl_params": goal, "task_type": task_type},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    trial_sha256 = hashlib.sha256(trial_bytes).hexdigest()
    canonical = json.dumps(
        {"goal_identity": goal_identity, "trial_id": trial_id, "trial_sha256": trial_sha256},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "trial_id": trial_id,
        "trial_sha256": trial_sha256,
        "expected_logical_scene": scene["floor_plan"],
        "goal_identity": goal_identity,
        "goal_fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _plan_object_ids(trial_data: dict[str, Any]) -> dict[str, str]:
    """Extract stable object IDs from the selected ALFRED high-level plan."""

    plan = trial_data.get("plan")
    high_pddl = plan.get("high_pddl") if isinstance(plan, dict) else None
    if not isinstance(high_pddl, list):
        return {}
    result: dict[str, str] = {}
    action_keys = {
        "PickupObject": "take",
        "ToggleObject": "use",
        "OpenObject": "open",
        "CloseObject": "close",
    }
    for item in high_pddl:
        if not isinstance(item, dict):
            continue
        discrete = item.get("discrete_action")
        planner = item.get("planner_action")
        if not isinstance(discrete, dict) or not isinstance(planner, dict):
            continue
        action = discrete.get("action")
        key = action_keys.get(action)
        if key and isinstance(planner.get("objectId"), str):
            result.setdefault(key, planner["objectId"])
        if action == "PutObject" and isinstance(planner.get("receptacleObjectId"), str):
            result.setdefault("put_receptacle", planner["receptacleObjectId"])
    return result


def _controller_label_matches(value: dict[str, Any], wanted: str) -> bool:
    labels = {
        str(value.get("num_id") or "").strip().lower(),
        str(value.get("object_type") or "").strip().lower(),
        str(value.get("object_id") or "").split("|", 1)[0].strip().lower(),
    }
    return wanted in labels or any(
        label and wanted in label for label in labels if label
    )


def _metadata_label_matches(value: dict[str, Any], wanted: str) -> bool:
    labels = {
        str(value.get("objectType") or "").strip().lower(),
        str(value.get("name") or "").strip().lower(),
    }
    return wanted in labels or any(
        label and wanted in label for label in labels if label
    )


def _quantized_rotation(dx: float, dz: float) -> int:
    if abs(dx) < 1e-6 and abs(dz) < 1e-6:
        return 0
    return int(round(math.degrees(math.atan2(dx, dz)) / 90.0) * 90) % 360


def _quantized_horizon(dy: float, horizontal_distance: float) -> int:
    pitch = math.degrees(math.atan2(dy, max(horizontal_distance, 1e-3)))
    return max(-30, min(60, int(round((-pitch) / 15.0) * 15)))


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


def _first_text(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value:
        return _first_text(value[0])
    return value if isinstance(value, str) else ""


def _first_text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)) and value:
        if all(isinstance(item, str) for item in value):
            return list(value)
        value = value[0]
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str)]


def _first_number(value: Any) -> float:
    if isinstance(value, (list, tuple)) and value:
        return _first_number(value[0])
    return float(value) if isinstance(value, (int, float)) else 0.0


def _first_bool(value: Any) -> bool:
    if isinstance(value, (list, tuple)) and value:
        return _first_bool(value[0])
    return bool(value)


def _info_bool(info: Any, key: str, *, default: bool) -> bool:
    value = info.get(key) if isinstance(info, dict) else None
    return _first_bool(value) if value is not None else default


def _info_float(info: Any, key: str, *, default: float) -> float:
    value = info.get(key) if isinstance(info, dict) else None
    return _first_number(value) if value is not None else default


def _goal_rate(value: Any) -> float:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return 0.0
    try:
        denominator = float(value[1])
        return float(value[0]) / denominator if denominator else 0.0
    except (TypeError, ValueError):
        return 0.0


def _state_world_sha256(state: dict[str, Any]) -> str:
    """Hash scene objects and agent pose while ignoring goal-local state."""

    payload = {
        "objects": state.get("objects", []),
        "agent_pose": state.get("agent_pose", {}),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _task_description(trial_data: dict[str, Any]) -> str:
    from alfworld.agents.utils.misc import get_templated_task_desc

    description = get_templated_task_desc(trial_data)
    if not isinstance(description, str) or not description.strip():
        raise ValueError("set_task task description is unreadable")
    return description


def _empty_state() -> dict[str, Any]:
    return {
        "episode_id": "uninitialized",
        "task": "",
        "observation": "",
        "inventory": None,
        "last_command": None,
        "last_feedback": None,
        "reward": 0.0,
        "done": False,
        "won": False,
        "goal_condition_success_rate": 0.0,
        "frame_path": None,
        "step_index": 0,
        "invalid_action_count": 0,
        "admissible_commands": [],
    }


def _module_version(name: str) -> str:
    try:
        module = __import__(name)
        return str(getattr(module, "__version__", "unknown"))
    except Exception:
        return "unknown"


__all__ = ["ThorBackend"]
