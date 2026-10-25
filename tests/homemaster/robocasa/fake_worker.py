"""Self-contained fake RoboCasa worker for protocol/client tests.

Speaks homemaster-robocasa-v1 over stdin/stdout with scripted deterministic
responses. Pure stdlib; runnable under any interpreter.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

PROTOCOL = "homemaster-robocasa-v1"
OPS = {
    "start",
    "reconfigure",
    "observe",
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
    "back_project",
    "world_map",
    "check_success",
    "close",
}

# 1x1 red PNG.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(request_id, *, status="ok", result=None, code=0, attempted=False, error=None):
    emit(
        {
            "protocol": PROTOCOL,
            "request_id": request_id,
            "status": status,
            "external_return_code": code,
            "backend_attempted": attempted,
            "result": dict(result or {}),
            "error": error,
        }
    )


def main() -> int:
    response("ready", status="ready", result={"worker_version": "fake"})
    state = {
        "task_name": "OpenDrawer",
        "split": "target",
        "seed": 1,
        "task_language": "Open the left drawer.",
        "success": False,
        "task_progress": {"drawer_open": 0.0},
        "proprio": {"robot0_eef_pos": [0, 0, 1]},
        "step_index": 0,
        "invalid_action_count": 0,
    }
    started = False
    succeeded_after = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    skills = 0
    workdir = Path(".")

    def image(path: Path) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_PNG)
        return {
            "camera": "agentview",
            "path": str(path.resolve()),
            "size": len(_PNG),
            "sha256": hashlib.sha256(_PNG).hexdigest(),
            "mime_type": "image/png",
        }

    for raw in sys.stdin.buffer:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            continue
        rid = frame.get("request_id") or "unknown"
        op = frame.get("operation")
        payload = frame.get("payload") or {}
        if op not in OPS:
            response(rid, status="error", code=64, error={"code": "unsupported_operation"})
            continue
        if op == "start":
            started = True
            workdir = Path(payload["workdir"])
            workdir.mkdir(parents=True, exist_ok=True)
            state.update(
                task_name=payload["task_name"],
                split=payload["split"],
                seed=payload["seed"],
                task_language=f"Task: {payload['task_name']}",
            )
            response(rid, result={"task_language": state["task_language"], "state": state})
        elif op == "reconfigure":
            if not started:
                response(rid, status="error", code=1, error={"code": "not_started"})
                continue
            state.update(
                task_name=payload["task_name"],
                seed=payload["seed"],
                task_language=f"Task: {payload['task_name']}",
                success=False,
                task_progress={"drawer_open": 0.0},
                step_index=0,
                invalid_action_count=0,
            )
            skills = 0
            response(
                rid,
                result={
                    "task_language": state["task_language"],
                    "reconfigured": True,
                    "state": dict(state),
                },
            )
        elif op == "observe":
            state["step_index"] += 1
            response(
                rid,
                result={
                    "state": dict(state),
                    "images": [image(workdir / "artifacts" / "agentview.png")],
                },
            )
        elif op in {
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
        }:
            state["step_index"] += 1
            skills += 1
            if skills >= succeeded_after:
                state["success"] = True
                state["task_progress"] = {"drawer_open": 1.0}
            response(
                rid,
                attempted=True,
                result={
                    "primitive": op,
                    "result": {"status": "ok", "chunks": skills},
                    "errored": False,
                    "step_index": state["step_index"],
                    "state": dict(state),
                },
            )
        elif op == "check_success":
            response(
                rid,
                result={
                    "success": state["success"],
                    "task_progress": state["task_progress"],
                    "state": dict(state),
                },
            )
        elif op == "back_project":
            response(rid, result={"result": {"points": []}, "state": dict(state)})
        elif op == "world_map":
            response(rid, result={"result": {"clusters": []}, "state": dict(state)})
        elif op == "close":
            response(rid, status="closed", result={"closed": True})
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
