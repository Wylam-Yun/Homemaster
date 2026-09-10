"""Lightweight V3.4 acceptance server: real HTTP/WS around a file store.

Serves the browser approval protocol (submit/cancel/grants/revoke +
WebSocket event fanout) without the full home application bundle, so the
Task 9 verifier can drive it as an external process and kill/restart it
against the same database file.

Seed mode (``--seed``) additionally runs scripted ``confirm()`` waits:
after a browser subscribes to the seeded session, the server emits a
``runtime.turn_started`` + live ``confirm()`` for a prepared request, so a
real pending card appears over a real WebSocket. A second request follows
the first resolution for the revoke-then-re-request round.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from homemaster.application.session import SessionManager  # noqa: E402
from homemaster.events.bus import EventBus  # noqa: E402
from homemaster.events.runtime_events import RuntimeEvent  # noqa: E402
from homemaster.permissions.models import (  # noqa: E402
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
)
from homemaster.permissions.store import PermissionStore  # noqa: E402
from homemaster.tools.contracts import PermissionSubject  # noqa: E402
from homemaster.web.app import create_web_app  # noqa: E402
from homemaster.web.confirmations import WebConfirmationHandler  # noqa: E402


def _iso(offset_s: float = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_s)).isoformat().replace(
        "+00:00", "Z"
    )


def _seed_request(tag: str, env: str = "home-test") -> PreparedPhysicalRequest:
    suffix = f"{tag}-{uuid4().hex[:6]}"
    enter = Requirement(
        item_id=f"item-enter-{suffix}",
        key=ResourceKey(environment_id=env, resource_kind="area",
                        resource_id="bedroom", action="enter"),
        display_name="卧室", location="卧室", action_label="进入",
        step_ids=(f"step-enter-{suffix}",),
    )
    pick = Requirement(
        item_id=f"item-pick-{suffix}",
        key=ResourceKey(environment_id=env, resource_kind="object",
                        resource_id="cup-a", action="pick_up"),
        display_name="白色杯子", location="卧室床头柜", action_label="拿取",
        step_ids=(f"step-pick-{suffix}",),
    )
    return PreparedPhysicalRequest(
        request_id=f"request-{suffix}", approval_id=f"approval-{suffix}",
        environment_id=env, session_id=f"session-{suffix}", run_id=f"run-{suffix}",
        intent_id=f"intent-{suffix}", intent_summary="去卧室拿杯子",
        revision=1, requirements=(enter, pick),
        steps=(
            PreparedStep(step_id=f"step-enter-{suffix}",
                         binding_ref=f"nav-{suffix}",
                         required_item_ids=(enter.item_id,), summary="进入卧室"),
            PreparedStep(step_id=f"step-pick-{suffix}",
                         binding_ref=f"take-{suffix}",
                         required_item_ids=(pick.item_id,), summary="拿取杯子"),
        ),
        target_snapshot_revision=f"snap-{suffix}",
        created_at=_iso(), deadline_at=_iso(300),
    )


class _FakeApplication:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def aclose(self) -> None:
        return None


def _seed_context(session_id: str, run_id: str, sink: Any) -> SimpleNamespace:
    return SimpleNamespace(metadata={
        "session_id": session_id,
        "run_id": run_id,
        "turn_index": 0,
        "tool_call_id": "call-seed",
        "permission_subject": PermissionSubject(
            "web-local-operator", "web", tenant_id="local", capabilities=()
        ),
        "run_context": SimpleNamespace(event_sink=sink),
    })


class _BusSink:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def aemit(self, event: RuntimeEvent) -> None:
        await self._bus.aemit(event)


async def _wait_subscriber(hub: Any, session_id: str, timeout_s: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if await hub.has_subscriber(session_id):
            return True
        await asyncio.sleep(0.2)
    return False


async def _run_seed_flow(
    *,
    hub: Any,
    registry: Any,
    handler: WebConfirmationHandler,
    store: PermissionStore,
    bus: EventBus,
    session_id: str,
    ready_file: Path,
    log: Any,
    port: int,
    signal_file: Path | None,
) -> None:
    sink = _BusSink(bus)

    async def run_one(tag: str, web_request_id: str, run_id: str) -> Any:
        request = _seed_request(tag)
        store.create_request(request)
        missing = [item.item_id for item in request.requirements]
        box: dict[str, Any] = {}

        async def run_factory() -> object:
            await bus.aemit(RuntimeEvent(
                type="runtime.turn_started", session_id=session_id, run_id=run_id,
                turn_index=0, payload={},
            ))
            try:
                box["resolution"] = await handler.confirm(
                    request, missing, _seed_context(session_id, run_id, sink)
                )
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
                raise
            finally:
                await bus.aemit(RuntimeEvent(
                    type="runtime.turn_completed", session_id=session_id,
                    run_id=run_id, turn_index=0, payload={},
                ))
            return box["resolution"]

        acceptance = await registry.accept(session_id, web_request_id, run_factory)
        if not acceptance.created:
            raise RuntimeError("seed request was not accepted")
        while "resolution" not in box and "error" not in box:
            await asyncio.sleep(0.1)
        if "error" in box:
            raise box["error"]
        return box["resolution"]

    await asyncio.to_thread(_wait_http_ready, port)
    ready_file.write_text(json.dumps({"session_id": session_id}), encoding="utf-8")
    print(f"seed session ready: {session_id}", file=log, flush=True)
    if not await _wait_subscriber(hub, session_id, 90.0):
        print("seed: no subscriber arrived; giving up", file=log, flush=True)
        return
    resolution = await run_one("first", "web-request-1", "seed-run-1")
    print(f"seed first resolved: {resolution.request_status}", file=log, flush=True)
    if signal_file is not None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 180.0
        while loop.time() < deadline and not signal_file.exists():
            await asyncio.sleep(0.5)
        if not signal_file.exists():
            print("seed: signal file never arrived; skipping second request",
                  file=log, flush=True)
            return
    else:
        await asyncio.sleep(1.0)
    await run_one("second", "web-request-2", "seed-run-2")
    print("seed second requested", file=log, flush=True)


def _wait_http_ready(port: int, timeout_s: float = 30.0) -> None:
    """Block until the server answers. Runs in a worker thread: the call is
    synchronous and must never execute on the event loop (self-deadlock)."""
    import time
    import urllib.request

    deadline = time.time() + timeout_s
    url = f"http://127.0.0.1:{port}/api/sessions"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)
    raise RuntimeError(f"server on {port} never became ready")


async def _amain(args: argparse.Namespace) -> int:
    import uvicorn

    db_path = Path(args.db)
    store = PermissionStore.open(db_path)
    handler = WebConfirmationHandler(timeout_s=180.0)
    handler.bind_store(store)
    application = _FakeApplication()
    app = create_web_app(
        application=application,
        confirmation_handler=handler,
        permission_store=store,
    )
    hub = app.state.event_hub
    registry = app.state.run_registry
    seed_task: asyncio.Task | None = None
    if args.seed:
        runtime = await application.session_manager.open_or_resume()
        session_id = runtime.session.session_id
        seed_task = asyncio.create_task(_run_seed_flow(
            hub=hub, registry=registry, handler=handler, store=store,
            bus=application.event_bus, session_id=session_id,
            ready_file=Path(args.ready_file), log=sys.stderr, port=args.port,
            signal_file=Path(args.signal_file) if args.signal_file else None,
        ))
    config = uvicorn.Config(app=app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
    if seed_task is not None and not seed_task.done():
        seed_task.cancel()
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3.4 acceptance server")
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--ready-file", default="")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--signal-file", default="")
    args = parser.parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
