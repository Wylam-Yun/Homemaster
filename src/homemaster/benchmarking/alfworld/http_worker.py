"""Dependency-free loopback HTTP host for an ALFWorld adapter."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

_PROTOCOL = "homemaster-alfworld-http-v1"
_TOKEN_ENV = "HOMEMASTER_ALFWORLD_HTTP_TOKEN"
_READY_FD_ENV = "HOMEMASTER_ALFWORLD_READY_FD"
_MAX_JSON_BODY = 65_536


def _strict_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected exactly 'true' or 'false'")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--config-path", type=Path, required=True)
    parser.add_argument("--trial-manifest", type=Path, required=True)
    parser.add_argument("--trial-index", type=int, required=True)
    parser.add_argument("--env-type", choices=("AlfredThorEnv",), required=True)
    parser.add_argument(
        "--split",
        choices=("train", "valid_seen", "valid_unseen"),
        required=True,
    )
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--allow-offscreen-object-navigation",
        type=_strict_bool,
        required=True,
    )
    parser.add_argument("--frame-dir", type=Path, required=True)
    return parser


def serve(args: argparse.Namespace) -> int:
    ready_fd = int(os.environ[_READY_FD_ENV])
    token = os.environ[_TOKEN_ENV]
    with contextlib.redirect_stdout(sys.stderr):
        import ai2thor
        import alfworld
        import alfworld.agents.environment as environment_module

        from homemaster.benchmarking.alfworld.env_adapter import (
            AlfworldEnvAdapter,
            build_alfworld_batch_env_with_first_trial,
        )
        from homemaster.benchmarking.alfworld.trial_selection import (
            load_trial_selection_manifest,
        )
        from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig

        trial_root = args.data_root / "json_2.1.1"
        manifest = load_trial_selection_manifest(
            args.trial_manifest,
            trial_root=trial_root,
        )
        if args.trial_index < 0 or args.trial_index >= len(manifest.entries):
            raise ValueError(
                f"trial index {args.trial_index} exceeds {len(manifest.entries)} entries"
            )
        selection = manifest.entries[args.trial_index]
        config = AlfworldBenchmarkConfig(
            alfworld_root=args.asset_root,
            alfworld_config=args.config_path,
            trace_root=args.frame_dir.parent,
            data_root=args.data_root,
            use_installed_alfworld=True,
            env_type=args.env_type,
            split=args.split,
            episodes=1,
            seed=args.seed,
            trial_manifest=args.trial_manifest,
        )
        env = build_alfworld_batch_env_with_first_trial(
            config,
            first_trial_path=trial_root / selection.trial_id,
        )
        adapter = AlfworldEnvAdapter(
            env=env,
            episode_prefix=args.split,
            seed=args.seed,
            frame_dir=args.frame_dir,
            require_v18_reset=True,
            allow_offscreen_object_navigation=args.allow_offscreen_object_navigation,
        )
        reset_result = adapter.reset(selection_entry=selection)
        if not reset_result.ready:
            raise RuntimeError(
                "ALFWorld reset did not become ready: "
                f"{reset_result.setup_failure or reset_result.setup_trigger}"
            )

    state_sequence = 1
    response_cache: dict[str, dict[str, Any]] = {}
    operation_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "HomeMasterAlfworld/1"

        def log_message(self, format: str, *values: object) -> None:
            print(format % values, file=sys.stderr, flush=True)

        def _authorized(self) -> bool:
            return self.headers.get("Authorization") == f"Bearer {token}"

        def _send_json(self, status: int, payload: object) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _guard(self) -> bool:
            if self._authorized():
                return True
            self._send_json(401, {"ok": False, "error": "unauthorized"})
            return False

        def _read_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > _MAX_JSON_BODY:
                raise ValueError("request body exceeds the protocol limit")
            value = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(value, dict):
                raise ValueError("request body must be a JSON object")
            return value

        def do_GET(self) -> None:
            if not self._guard():
                return
            with contextlib.redirect_stdout(sys.stderr), operation_lock:
                if self.path == "/v1/health":
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "protocol": _PROTOCOL,
                            "allow_offscreen_object_navigation": (
                                args.allow_offscreen_object_navigation
                            ),
                            "state_sequence": state_sequence,
                            "worker_pid": os.getpid(),
                            "python_executable": sys.executable,
                            "alfworld_origin": str(Path(alfworld.__file__).resolve()),
                            "alfworld_environment_origin": str(
                                Path(environment_module.__file__).resolve()
                            ),
                            "alfworld_environment_sha256": hashlib.sha256(
                                Path(environment_module.__file__).read_bytes()
                            ).hexdigest(),
                            "ai2thor_version": ai2thor.__version__,
                        },
                    )
                    return
                if self.path == "/v1/state":
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "state_sequence": state_sequence,
                            "result": asdict(adapter.current_state),
                        },
                    )
                    return
                if self.path == "/v1/screenshot":
                    frame_path = adapter.current_state.frame_path
                    if not frame_path:
                        self._send_json(500, {"ok": False, "error": "missing_frame"})
                        return
                    content = Path(frame_path).read_bytes()
                    digest = hashlib.sha256(content).hexdigest()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(content)))
                    self.send_header("X-Content-SHA256", digest)
                    self.send_header("X-State-Sequence", str(state_sequence))
                    self.end_headers()
                    self.wfile.write(content)
                    return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:
            nonlocal state_sequence
            if not self._guard():
                return
            try:
                payload = self._read_body()
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            request_id = payload.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                self._send_json(400, {"ok": False, "error": "missing_request_id"})
                return
            with contextlib.redirect_stdout(sys.stderr), operation_lock:
                cached = response_cache.get(request_id)
                if cached is not None:
                    self._send_json(200, cached)
                    return
                if self.path == "/v1/go-to":
                    target = payload.get("target")
                    tool_args = payload.get("tool_args")
                    if not isinstance(target, str) or not target:
                        self._send_json(400, {"ok": False, "error": "missing_target"})
                        return
                    if not isinstance(tool_args, dict):
                        self._send_json(400, {"ok": False, "error": "invalid_tool_args"})
                        return
                    result = adapter.go_to_target(
                        target,
                        tool_name="robot_go_to",
                        tool_args=tool_args,
                    )
                    state_sequence += 1
                    envelope = _step_envelope(request_id, state_sequence, result)
                    response_cache[request_id] = envelope
                    self._send_json(200, envelope)
                    return
                if self.path == "/v1/manipulate":
                    action = payload.get("action")
                    tool_args = payload.get("tool_args")
                    if not isinstance(action, str) or not action:
                        self._send_json(400, {"ok": False, "error": "missing_action"})
                        return
                    if not isinstance(tool_args, dict):
                        self._send_json(400, {"ok": False, "error": "invalid_tool_args"})
                        return
                    result = adapter.manipulate_with_thor(
                        action=action,
                        tool_name="robot_manipulate",
                        tool_args=tool_args,
                    )
                    state_sequence += 1
                    envelope = _step_envelope(request_id, state_sequence, result)
                    response_cache[request_id] = envelope
                    self._send_json(200, envelope)
                    return
                if self.path == "/v1/close":
                    cleanup = adapter.close()
                    envelope = {
                        "ok": cleanup.status == "succeeded",
                        "request_id": request_id,
                        "cleanup": asdict(cleanup),
                    }
                    response_cache[request_id] = envelope
                    self._send_json(200 if envelope["ok"] else 500, envelope)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
            self._send_json(404, {"ok": False, "error": "not_found"})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    ready = {
        "type": "ready",
        "protocol": _PROTOCOL,
        "host": "127.0.0.1",
        "port": server.server_port,
        "pid": os.getpid(),
        "trial_id": selection.trial_id,
        "allow_offscreen_object_navigation": args.allow_offscreen_object_navigation,
        "state": asdict(adapter.current_state),
    }
    os.write(ready_fd, (_json_bytes(ready) + b"\n"))
    os.close(ready_fd)
    server.serve_forever(poll_interval=0.05)
    server.server_close()
    return 0


def _step_envelope(request_id: str, state_sequence: int, result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "backend_attempted": result.backend_action_count > 0,
        "state_sequence": state_sequence,
        "result": asdict(result),
    }


def main() -> int:
    return serve(_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
