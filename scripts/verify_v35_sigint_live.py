#!/usr/bin/env python3
"""Real worker SIGINT acceptance for action and close boundaries."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from homemaster.alfworld.worker_client import AlfworldWorkerClient


def _descendants(root_pid: int) -> dict[str, dict[str, str]]:
    output = subprocess.run(
        ["ps", "-eo", "pid=,ppid=,args="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    processes: dict[int, tuple[int, str]] = {}
    for line in output:
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        processes[pid] = (ppid, fields[2])
    result: dict[str, dict[str, str]] = {}
    pending = [root_pid]
    while pending:
        parent = pending.pop()
        for pid, (ppid, command) in processes.items():
            if ppid != parent or str(pid) in result:
                continue
            result[str(pid)] = {"ppid": str(ppid), "args": command}
            pending.append(pid)
    return result


def _paths() -> dict[str, Path]:
    repo = Path(os.environ.get("HOMEMASTER_LIVE_REPO", Path.cwd())).resolve()
    asset_root = Path(
        os.environ.get("HOMEMASTER_LIVE_ALFWORLD_ROOT", str(repo / ".runtime" / "alfworld"))
    ).resolve()
    return {
        "repo": repo,
        "asset_root": asset_root,
        "python": Path(
            os.environ.get(
                "HOMEMASTER_LIVE_ALFWORLD_PYTHON",
                str(repo / ".runtime" / "alfworld-venv" / "bin" / "python"),
            )
        ),
        "manifest": Path(
            os.environ.get(
                "HOMEMASTER_LIVE_TRIAL_MANIFEST",
                str(repo / ".runtime" / "single_mug_manifest.json"),
            )
        ),
    }


def _start(paths: dict[str, Path], root: Path, suffix: str) -> AlfworldWorkerClient:
    asset_root = paths["asset_root"]
    log_path = root / f"{suffix}.stderr.log"
    return AlfworldWorkerClient.start(
        python_executable=paths["python"],
        asset_root=asset_root,
        data_root=asset_root / "data",
        config_path=asset_root / "configs" / "base_config.yaml",
        trial_manifest=paths["manifest"],
        trial_index=0,
        env_type="AlfredThorEnv",
        split="valid_unseen",
        seed=42,
        allow_offscreen_object_navigation=False,
        display=os.environ.get("HOMEMASTER_LIVE_DISPLAY", ":99"),
        frame_dir=root / f"{suffix}-frames",
        log_path=log_path,
        startup_timeout_s=180,
        request_timeout_s=180,
    )


def _schedule_sigint(delay_s: float) -> tuple[threading.Timer, threading.Event]:
    fired = threading.Event()

    def send() -> None:
        fired.set()
        os.kill(os.getpid(), signal.SIGINT)

    timer = threading.Timer(delay_s, send)
    timer.daemon = True
    timer.start()
    return timer, fired


def _run_close(root: Path, paths: dict[str, Path]) -> dict[str, Any]:
    client = _start(paths, root, "close")
    worker_pid = client.worker_pid
    before = _descendants(worker_pid)
    timer, signal_fired = _schedule_sigint(0.2)
    close_error: str | None = None
    receipt: Any = None
    try:
        receipt = client.close()
    except BaseException as exc:  # preserve evidence before surfacing a failed gate
        close_error = f"{type(exc).__name__}: {exc}"
        if client.worker_exit_code is None:
            client._process.kill()
            client._process.wait(timeout=10)
    finally:
        timer.cancel()
        timer.join(timeout=2)
    after = _descendants(worker_pid)
    stderr_path = root / "close.stderr.log"
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    return {
        "worker_pid": worker_pid,
        "descendants_before": before,
        "close_interrupted": signal_fired.is_set(),
        "close_receipt": (
            {
                "operation": receipt.operation,
                "external_return_code": receipt.external_return_code,
                "backend_attempted": receipt.backend_attempted,
                "state": receipt.state,
                "evidence_ref": receipt.evidence_ref,
            }
            if receipt is not None
            else None
        ),
        "close_sigint_count": client.close_sigint_count,
        "close_error": close_error,
        "worker_exit_code": client.worker_exit_code,
        "descendants_after": after,
        "stderr_path": str(stderr_path),
        "stderr": stderr,
    }


def _run_action(root: Path, paths: dict[str, Path]) -> dict[str, Any]:
    client = _start(paths, root, "action")
    worker_pid = client.worker_pid
    before = _descendants(worker_pid)
    timer, signal_fired = _schedule_sigint(0.05)
    action_error: str | None = None
    action_completed = False
    try:
        client.go_to_target("Mug", tool_name="robot_go_to", tool_args={"target": "Mug"})
        action_completed = True
    except KeyboardInterrupt:
        action_error = "KeyboardInterrupt"
    except BaseException as exc:
        action_error = f"{type(exc).__name__}: {exc}"
    finally:
        timer.cancel()
        timer.join(timeout=2)
    close_error: str | None = None
    receipt: Any = None
    try:
        receipt = client.close()
    except BaseException as exc:
        close_error = f"{type(exc).__name__}: {exc}"
        if client.worker_exit_code is None:
            client._process.kill()
            client._process.wait(timeout=10)
    after = _descendants(worker_pid)
    stderr_path = root / "action.stderr.log"
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace") if stderr_path.exists() else ""
    return {
        "worker_pid": worker_pid,
        "descendants_before": before,
        "action_interrupted": signal_fired.is_set(),
        "action_completed": action_completed,
        "action_error": action_error,
        "close_receipt": (
            {
                "operation": receipt.operation,
                "external_return_code": receipt.external_return_code,
                "backend_attempted": receipt.backend_attempted,
                "state": receipt.state,
                "evidence_ref": receipt.evidence_ref,
            }
            if receipt is not None
            else None
        ),
        "close_sigint_count": client.close_sigint_count,
        "close_error": close_error,
        "worker_exit_code": client.worker_exit_code,
        "descendants_after": after,
        "stderr_path": str(stderr_path),
        "stderr": stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = _paths()
    root = args.output.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    results = {"action": _run_action(root, paths), "close": _run_close(root, paths)}
    payload = {"schema": "homemaster-v35-phase5-sigint-v3", "results": results}
    args.output.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    action = results["action"]
    close = results["close"]
    passed = (
        action["action_interrupted"]
        and action["close_receipt"] is not None
        and action["close_receipt"]["external_return_code"] == 0
        and action["worker_exit_code"] == 0
        and not action["descendants_after"]
        and close["close_interrupted"]
        and close["close_receipt"] is not None
        and close["close_receipt"]["external_return_code"] == 0
        and close["close_sigint_count"] >= 1
        and close["worker_exit_code"] == 0
        and not close["descendants_after"]
        and "Traceback" not in action["stderr"]
        and "Traceback" not in close["stderr"]
    )
    print(json.dumps({"status": "PASS" if passed else "FAIL", "output": str(args.output)}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
