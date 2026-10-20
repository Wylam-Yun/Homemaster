"""RoboCasa worker backend: RPent env/VLA servers + primitives behind NDJSON ops.

This module runs inside the RPent ``.venv-robocasa`` interpreter.  Heavy
imports (robosuite, RPent robots modules, numpy) are deferred to ``start`` so
the protocol loop stays importable in a bare interpreter.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    """Convert numpy / arbitrary primitive results into JSON-safe values."""
    try:
        import numpy as np
    except Exception:  # pragma: no cover - numpy always present in worker env
        np = None
    if np is not None:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class RPentBackend:
    """Own env_server/vla_server daemons and RoboCasaPrimitives for one worker."""

    def __init__(self) -> None:
        self._daemons: dict[str, Any] = {}
        self._env_client: Any | None = None
        self._vla_client: Any | None = None
        self._primitives: Any | None = None
        self._env_state: Any | None = None
        self._rpent_root: Path | None = None
        self._workdir: Path | None = None
        self._task: tuple[str, str, int] | None = None
        self._hi_res = False
        self._cuda_device: str | None = None
        self._invalid_count = 0
        self._closed = False

    # -- lifecycle ------------------------------------------------------

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Spawn env_server + vla_server and build primitives for one cell."""
        self._rpent_root = Path(payload["rpent_root"]).resolve()
        if str(self._rpent_root) not in sys.path:
            sys.path.insert(0, str(self._rpent_root))
        self._workdir = Path(payload["workdir"]).resolve()
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._task = (str(payload["task_name"]), str(payload["split"]), int(payload["seed"]))
        self._cuda_device = (
            None if payload.get("cuda_device") is None else str(payload["cuda_device"])
        )
        self._hi_res = bool(payload.get("hi_res"))
        self._invalid_count = 0
        # Per-skill-call 3-camera clips live under workdir/videos (read by
        # RLDXSkill at construction, inside this process).
        os.environ["RLDX_VIDEO_DIR"] = str(self._workdir / "videos")
        try:
            self._init_runtime(components=None)
            self._build_primitives()
        except Exception:
            # A partial bring-up (env spawned, vla failed, primitives raise)
            # must not orphan live servers — stop whatever did start.
            self._stop_daemons()
            raise
        return {
            "task_language": self._task_language(),
            "meta": self._meta(),
            "state": self._state_payload(),
        }

    def reconfigure(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Restart env_server for a new cell; keep vla_server warm."""
        self._require_ready()
        task = (str(payload["task_name"]), str(payload["split"]), int(payload["seed"]))
        if task == self._task:
            return {
                "task_language": self._task_language(),
                "reconfigured": False,
                "state": self._state_payload(),
            }
        env_daemon = self._daemons.pop("env_server", None)
        episode_video = self._flush_episode_video()
        if env_daemon is not None:
            env_daemon.stop()
        if payload.get("workdir"):
            self._workdir = Path(payload["workdir"]).resolve()
            self._workdir.mkdir(parents=True, exist_ok=True)
        self._task = task
        self._invalid_count = 0
        os.environ["RLDX_VIDEO_DIR"] = str(self._workdir / "videos")
        try:
            self._init_runtime(components={"env"})
            assert self._vla_client is not None
            self._reset_vla_session()
            self._build_primitives()
        except Exception:
            # The new env daemon (if any) dies with us; the warm vla_server
            # stays — the client will kill the worker on this error anyway.
            new_env = self._daemons.pop("env_server", None)
            if new_env is not None:
                try:
                    new_env.stop()
                except Exception:
                    pass
            self._env_client = None
            self._primitives = None
            raise
        result: dict[str, Any] = {
            "task_language": self._task_language(),
            "reconfigured": True,
            "state": self._state_payload(),
        }
        if episode_video:
            result["episode_video"] = episode_video
        return result

    def close(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        episode_video = self._flush_episode_video()
        self._stop_daemons()
        self._closed = True
        result: dict[str, Any] = {"closed": True}
        if episode_video:
            result["episode_video"] = episode_video
        return result

    def _stop_daemons(self) -> None:
        for name in reversed(list(self._daemons)):
            try:
                self._daemons[name].stop()
            except Exception:
                pass
        self._daemons.clear()

    def _flush_episode_video(self) -> str | None:
        """Write the buffered frames to ``episode.mp4`` under the workdir.

        Returns the artifact path, or None when nothing was recorded.
        Video is best-effort evidence — a failure must not break cell
        teardown.
        """
        if self._primitives is None or self._env_state is None:
            return None
        try:
            frames = self._primitives.stop_recording()
            if not frames:
                return None
            name = self._env_state.save(
                "episode.mp4", frames, step=None, fps=20
            )
            return str(self._workdir / name) if name else None
        except Exception:
            return None

    # -- observation ----------------------------------------------------

    def observe(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_ready()
        cameras = payload.get("cameras") or ["agentview", "wrist"]
        if not isinstance(cameras, list) or not all(isinstance(c, str) for c in cameras):
            raise ValueError("cameras must be a list of camera names")
        images = [self._render_image(camera) for camera in cameras]
        state = self._state_payload()
        return {
            "state": state,
            "step_index": state["step_index"],
            "images": images,
        }

    def check_success(self, payload: dict[str, Any]) -> dict[str, Any]:
        del payload
        self._require_ready()
        state = self._state_payload()
        return {
            "success": bool(self._env_client.check_success()),
            "task_progress": _jsonable(self._primitives.task_progress()),
            "step_index": state["step_index"],
            "state": state,
        }

    # -- primitives -----------------------------------------------------

    _RLDX_KWARGS = {
        "base_clip",
        "max_chunks",
        "use_prompt",
        "prompt",
        "force_reset",
        "n_action_steps",
        "settle_patience",
        "settle_eps",
    }

    def skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: payload[k] for k in self._RLDX_KWARGS if k in payload}
        return self._primitive("rldx_skill", payload, kwargs=kwargs)

    def arm(self, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: payload[k] for k in self._RLDX_KWARGS if k in payload}
        return self._primitive("rldx_arm", payload, kwargs=kwargs)

    def navigate(self, payload: dict[str, Any]) -> dict[str, Any]:
        xy = payload.get("xy") or [payload.get("x"), payload.get("y")]
        if not (isinstance(xy, list) and len(xy) == 2):
            raise ValueError("navigate requires xy=[x, y]")
        kwargs = {k: payload[k] for k in ("tol", "max_steps", "gripper") if k in payload}
        return self._primitive("navigate_to", payload, args=([float(xy[0]), float(xy[1])],),
                             kwargs=kwargs)

    def move_to(self, payload: dict[str, Any]) -> dict[str, Any]:
        xyz = self._vec(payload, "xyz", 3)
        kwargs = {k: payload[k] for k in ("gripper", "step_clip", "max_steps", "tol")
                  if k in payload}
        return self._primitive("move_to", payload, args=(xyz,), kwargs=kwargs)

    def move_delta(self, payload: dict[str, Any]) -> dict[str, Any]:
        dxyz = self._vec(payload, "dxyz", 3)
        kwargs = {k: payload[k] for k in ("gripper", "step_clip", "max_steps")
                  if k in payload}
        return self._primitive("move_delta", payload, args=(dxyz,), kwargs=kwargs)

    def rotate_pitch(self, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: payload[k] for k in ("target_pitch", "gripper", "n") if k in payload}
        return self._primitive("rotate_pitch", payload, kwargs=kwargs)

    def set_gripper(self, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: payload[k] for k in ("gripper", "steps") if k in payload}
        return self._primitive("set_gripper", payload, kwargs=kwargs)

    def release(self, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: payload[k] for k in ("steps",) if k in payload}
        return self._primitive("release", payload, kwargs=kwargs)

    def scripted_grasp(self, payload: dict[str, Any]) -> dict[str, Any]:
        xyz = self._vec(payload, "xyz", 3)
        kwargs = {k: payload[k] for k in ("approach_z", "grasp_z_offset", "step_clip")
                  if k in payload}
        return self._primitive("scripted_grasp", payload, args=(xyz,), kwargs=kwargs)

    def move_base(self, payload: dict[str, Any]) -> dict[str, Any]:
        kwargs = {k: payload[k] for k in ("forward", "lateral", "turn", "steps", "gripper")
                  if k in payload}
        return self._primitive("move_base", payload, kwargs=kwargs)

    # -- perception tools (EnvState artifact based) ---------------------

    def back_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_ready()
        from robots.robocasa.tools import back_project_batch

        pixels = payload.get("pixels")
        if not isinstance(pixels, list) or not pixels:
            raise ValueError("back_project requires non-empty pixels [[row, col], ...]")
        result = back_project_batch(
            pixels,
            step=payload.get("step"),
            camera=str(payload.get("camera") or "agentview"),
            state=self._env_state,
        )
        return {"result": _jsonable(result), "state": self._state_payload()}

    def world_map(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._require_ready()
        from robots.robocasa.tools import query_world_map

        result = query_world_map(
            z_min=float(payload.get("z_min", 0.85)),
            z_max=float(payload.get("z_max", 0.95)),
            x_range=payload.get("x_range"),
            y_range=payload.get("y_range"),
            camera=str(payload.get("camera") or "agentview"),
            min_cluster_size=int(payload.get("min_cluster_size", 10)),
            state=self._env_state,
        )
        return {"result": _jsonable(result), "state": self._state_payload()}

    # -- internals ------------------------------------------------------

    def _init_runtime(self, *, components: set[str] | None) -> None:
        import argparse

        from robots.robocasa.robot_spec import _init_runtime
        from rpent.dashboard.events import NullDashboardEventSink

        args = argparse.Namespace(
            task_name=self._task[0],
            split=self._task[1],
            seed=self._task[2],
            env_endpoint=None,
            vla_endpoint=None,
            vla_model_path=os.environ.get("RLDX_MODEL_PATH"),
            cuda_device=self._cuda_device,
            hi_res=self._hi_res,
            # NOTE: deliberately no ``memory_profile`` attribute — _init_runtime
            # would otherwise call TaskMemory.load and hit the HF hub.
        )
        daemons, kwargs = _init_runtime(
            args, self._workdir, NullDashboardEventSink(), components
        )
        for daemon in daemons:
            self._daemons[daemon.name] = daemon
        if "env_client" in kwargs:
            self._env_client = kwargs["env_client"]
        if "vla_client" in kwargs:
            self._vla_client = kwargs["vla_client"]

    def _reset_vla_session(self) -> None:
        """Reset the VLA episode state, recovering from idle-swept sessions.

        ``vla_server`` sweeps RPC sessions idle longer than
        ``session_timeout_s`` (3600 s default). The idle clock starts at the
        last sessioned call — a cell whose tail ran only non-VLA ops plus
        the inter-cell memory-finalization gap can push the session past the
        sweep, so ``reset_session`` lands on a reclaimed id. Re-register the
        client's own session id (``session.register`` re-adds it) and reset
        again; a freshly registered session has empty policy state, so the
        semantics match a normal reset.
        """
        assert self._vla_client is not None
        try:
            self._vla_client.reset_session()
        except Exception as exc:
            if "session not found" not in str(exc):
                raise
            # ``RoboCasaVLAClient._client`` is the private RpcClient that owns
            # the session id; ``session.register`` is a builtin facade method.
            self._vla_client._client.call("session.register")
            self._vla_client.reset_session()

    def _build_primitives(self) -> None:
        from robots.robocasa.primitives import RoboCasaPrimitives
        from robots.robocasa.tools import dump_state
        from rpent.session.base import EnvState

        self._env_state = EnvState(self._workdir)
        # allow_reset=False: matched-scene evaluation forbids episode restart.
        self._primitives = RoboCasaPrimitives(
            self._env_client,
            str(self._workdir),
            self._hi_res,
            self._vla_client,
            allow_reset=False,
        )
        # Record every env.step to the frame buffer — flushed to
        # ``episode.mp4`` on reconfigure/close (mirrors RPent's Toolkit).
        self._primitives.start_recording()
        # Step-0 record: perception tools (back_project/world_map) read the
        # latest step's world-map artifacts — without this they error until
        # the first mutating op, and the trace has no initial observation.
        dump_state(self._primitives, self._env_state, log={"command": "init"})

    def _primitive(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_ready()
        del payload
        from robots.robocasa.tools import dump_state

        result = getattr(self._primitives, name)(*args, **(kwargs or {}))
        record = dump_state(
            self._primitives,
            self._env_state,
            log={"command": name, "result": _jsonable(result)},
        )
        errored = isinstance(result, dict) and "error" in result
        if errored:
            self._invalid_count += 1
        return {
            "primitive": name,
            "result": _jsonable(result),
            "errored": errored,
            "step_index": int(getattr(record, "step_idx", 0) or 0),
            "state": self._state_payload(),
        }

    def _render_image(self, camera: str) -> dict[str, Any]:
        import numpy as np
        from PIL import Image

        frame = self._env_client.render_camera(
            camera_name=camera, height=256, width=256, depth=False
        )
        image = Image.fromarray(np.asarray(frame, dtype=np.uint8))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        content = buffer.getvalue()
        artifacts = self._workdir / "artifacts" / f"{camera}.png"
        artifacts.parent.mkdir(parents=True, exist_ok=True)
        artifacts.write_bytes(content)
        return {
            "camera": camera,
            "path": str(artifacts),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "mime_type": "image/png",
        }

    def _state_payload(self) -> dict[str, Any]:
        sd = self._primitives.current_state_dict()
        return {
            "task_name": self._task[0],
            "split": self._task[1],
            "seed": self._task[2],
            "task_language": sd.get("task_language", ""),
            "success": bool(sd.get("success")),
            "task_progress": _jsonable(sd.get("task_progress")),
            "proprio": _jsonable(sd.get("state")),
            "step_index": int(self._env_state.latest_step or 0),
            "invalid_action_count": self._invalid_count,
        }

    def _task_language(self) -> str:
        obs = self._env_client.current_raw_obs or {}
        return str(obs.get("language") or self._env_client.get_task_language() or "")

    def _meta(self) -> dict[str, Any]:
        return {
            "task_name": self._task[0],
            "split": self._task[1],
            "seed": self._task[2],
            "daemons": sorted(self._daemons),
        }

    def _vec(self, payload: dict[str, Any], key: str, size: int) -> list[float]:
        value = payload.get(key)
        if not (isinstance(value, list) and len(value) == size):
            raise ValueError(f"{key} must be a list of {size} numbers")
        return [float(v) for v in value]

    def _require_ready(self) -> None:
        if self._closed:
            raise RuntimeError("backend is closed")
        if self._primitives is None or self._env_client is None:
            raise RuntimeError("backend has not been started")


__all__ = ["RPentBackend"]
