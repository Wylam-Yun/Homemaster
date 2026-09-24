"""Live V3.4 approval demo on a REAL ALFWorld trial (interactive).

Starts: THOR (pinned Mug trial) + permission gate (store/checker/executor)
+ Web Console (real WS + real protocol). Then runs TWO scripted tool calls
through the REAL executor:

  1. take the mug  -> approval card pops in the browser -> you approve
     -> the robot really takes the mug in THOR.
  2. put it on the desk -> second card -> you approve -> really placed.

There is NO model in this loop: the calls are fixed by the trial, and YOU
play the approver in the browser. That is the whole point: every approval
and every physical effect is real; only the call authoring is scripted.

Usage (hkust4):
  DISPLAY=:99 ALFWORLD_DATA=<main>/.runtime/alfworld/data \\
  PYTHONPATH=<worktree>/src:<formal-site> \\
  <alfworld-venv>/bin/python scripts/v34_alfworld_demo.py \\
      --db /tmp/v34demo.sqlite3 --port 18401 --ready-file /tmp/v34demo-ready.json
  # then open the printed URL and approve the cards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_MAIN = Path("/data1/haodong2/weilin/red_bird/Homemaster")
WORKTREE = Path("/data1/haodong2/weilin/red_bird/Homemaster-v34-permissions")
sys.path.insert(0, str(WORKTREE / "src"))

TRIAL_A = (
    REPO_MAIN / ".runtime" / "alfworld" / "data" / "json_2.1.1" / "valid_unseen"
    / "pick_and_place_simple-Mug-None-Desk-308"
    / "trial_T20190908_125200_737896" / "traj_data.json"
)


def _log(message: str) -> None:
    print(message, flush=True)


async def _amain(args: argparse.Namespace) -> int:
    import uvicorn

    from homemaster.agent.messages import ToolCall
    from homemaster.application.session import SessionManager
    from homemaster.benchmarking.alfworld.env_adapter import (
        AlfworldEnvAdapter,
        build_alfworld_batch_env,
    )
    from homemaster.benchmarking.alfworld.permission_adapter import (
        AlfworldPermissionAdapter,
        ThorBackendView,
    )
    from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
    from homemaster.events.bus import EventBus
    from homemaster.events.runtime_events import RuntimeEvent
    from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
    from homemaster.permissions.store import PermissionStore
    from homemaster.tools import ToolExecutionContext
    from homemaster.tools.base import FunctionTool, ToolRegistry, ToolResult
    from homemaster.tools.contracts import PermissionSubject
    from homemaster.tools.executor import ToolExecutor
    from homemaster.web.app import create_web_app
    from homemaster.web.confirmations import WebConfirmationHandler

    _log("demo: building THOR env ...")
    config = AlfworldBenchmarkConfig(
        alfworld_root=REPO_MAIN / ".runtime" / "alfworld",
        alfworld_config=REPO_MAIN / ".runtime" / "alfworld" / "configs" / "base_config.yaml",
        trace_root=Path(args.db).parent / "demo-trace",
        data_root=REPO_MAIN / ".runtime" / "alfworld" / "data",
        env_type="AlfredThorEnv",
        split="valid_unseen",
        seed=7,
    )
    env = build_alfworld_batch_env(config)
    env.json_file_list = [str(TRIAL_A)]
    env_adapter = AlfworldEnvAdapter(env=env, episode_prefix="v34demo", seed=7)
    _log("demo: resetting THOR (one pinned trial) ...")
    reset = env_adapter.reset()
    if not reset.ready:
        _log(f"demo: THOR reset failed: {reset.classification}")
        return 2
    _log("demo: THOR ready")

    subtask = SimpleNamespace(object="Mug", parent="Desk", toggle=None, mrecep=None)
    backend = ThorBackendView(env_adapter, subtask=subtask)
    adapter = AlfworldPermissionAdapter(backend=backend)

    def _boom(arguments: Any, context: Any) -> ToolResult:
        raise AssertionError("physical tools must run through the adapter")

    tool = FunctionTool(
        name="robot_manipulate", description="Take and place the mug.",
        input_schema={"type": "object"}, execute=_boom,
        physical=True, physical_adapter=adapter,
    )
    registry = ToolRegistry()
    registry.register(tool)
    store = PermissionStore.open(Path(args.db))
    checker = PermissionChecker(PermissionSettingsConfig(), store=store)
    handler = WebConfirmationHandler(timeout_s=600.0)
    handler.bind_store(store)
    web_subject = PermissionSubject(
        subject_id="web-local-operator",
        channel="web",
        tenant_id="local",
    )

    class _App:
        def __init__(self) -> None:
            self.event_bus = EventBus()
            self.session_manager = SessionManager()
            self.started = False

        async def start(self) -> None:
            self.started = True

        async def aclose(self) -> None:
            return None

        async def run(self, request: Any) -> Any:
            raise RuntimeError(
                "demo server has no model; approve the scripted cards in the browser"
            )

        def cancel(self, session_id: str) -> bool:
            return False

    application = _App()
    executor = ToolExecutor(
        registry, permission_checker=checker,
        confirmation_handler=handler, permission_store=store,
    )
    app = create_web_app(
        application=application, confirmation_handler=handler, permission_store=store,
    )
    hub = app.state.event_hub
    run_registry = app.state.run_registry

    runtime = await application.session_manager.open_or_resume()
    session_id = runtime.session.session_id
    Path(args.ready_file).write_text(json.dumps({"session_id": session_id}))
    _log(f"demo: session {session_id}")

    ctx = ToolExecutionContext(
        WORKTREE, metadata={"session_id": session_id, "run_id": "demo-run-1"})

    async def drive() -> None:
        await _wait_http(args.port)
        _log("demo: server up; waiting for your browser ...")
        if not await _wait_subscriber(hub, session_id, 600.0):
            _log("demo: no browser arrived; giving up")
            return
        calls = [
            {"action": "take", "object": "mug"},
            {"action": "put", "object": "mug", "target_receptacle": "desk 1"},
        ]
        for index, call in enumerate(calls):
            for attempt in range(3):
                if await drive_attempt(index, call, attempt):
                    break
            else:
                _log(f"demo: call {index + 1} denied 3 times; moving on")
        _log("demo: both calls done; server stays up for inspection")

    async def drive_attempt(index: int, call: dict[str, Any], attempt: int) -> bool:
        web_request_id = f"demo-request-{index + 1}-try-{attempt + 1}"
        run_id = f"demo-run-{index + 1}-try-{attempt + 1}"
        box: dict[str, Any] = {}

        async def run_factory(
            call: dict[str, Any] = call,
            run_id: str = run_id,
            state: dict[str, Any] = box,
        ) -> object:
            await application.event_bus.aemit(RuntimeEvent(
                type="runtime.turn_started", session_id=session_id,
                run_id=run_id, turn_index=0, payload={}))
            try:
                state["result"] = await executor.execute(
                    ToolCall(id="1", name="robot_manipulate", arguments=call),
                    ToolExecutionContext(WORKTREE, metadata={
                        "session_id": session_id,
                        "run_id": run_id,
                        "turn_index": 0,
                        "tool_call_id": "1",
                        "permission_subject": web_subject,
                        "run_context": SimpleNamespace(event_sink=application.event_bus),
                    }),
                )
            except BaseException as exc:  # noqa: BLE001
                state["error"] = exc
                raise
            finally:
                await application.event_bus.aemit(RuntimeEvent(
                    type="runtime.turn_completed", session_id=session_id,
                    run_id=run_id, turn_index=0, payload={}))
            return state["result"]

        acceptance = await run_registry.accept(session_id, web_request_id, run_factory)
        if not acceptance.created:
            _log("demo: request not accepted; aborting")
            return True
        while "result" not in box and "error" not in box:
            await asyncio.sleep(0.2)
        if "error" in box:
            _log(f"demo: call {index + 1} raised {box['error']!r}")
            return True
        result = box["result"]
        status = result.metadata.get("status", "")
        _log(f"demo: call {index + 1} done: error={result.is_error} "
             f"status={status} {result.output[:100]}")
        if result.is_error and status == "permission_denied":
            _log(f"demo: call {index + 1} denied/cancelled; re-prompting")
            return False
        return True

    async def _wait_http(port: int) -> None:
        import urllib.request

        def _probe() -> bool:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/sessions", timeout=2) as resp:
                    return resp.status == 200
            except Exception:  # noqa: BLE001
                return False

        while True:
            done = await asyncio.to_thread(_probe)
            if done:
                return
            await asyncio.sleep(0.3)

    async def _wait_subscriber(hub: Any, session: str, timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        while loop.time() < deadline:
            if await hub.has_subscriber(session):
                return True
            await asyncio.sleep(0.5)
        return False

    drive_task = asyncio.create_task(drive())
    config_uv = uvicorn.Config(app=app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config_uv)
    await server.serve()
    if not drive_task.done():
        drive_task.cancel()
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3.4 live ALFWorld approval demo")
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--ready-file", required=True)
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
