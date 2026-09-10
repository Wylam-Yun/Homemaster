"""Shared harness for Task 5 web approval tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx

from homemaster.application.session import SessionManager
from homemaster.events.bus import EventBus
from homemaster.permissions.models import (
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
)
from homemaster.permissions.store import PermissionStore
from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler


class FakeClock:
    def __init__(self, start: str = "2026-09-10T01:00:00Z") -> None:
        text = start.strip()
        candidate = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
        self._now = datetime.fromisoformat(candidate)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> datetime:
        self._now = self._now + timedelta(seconds=seconds)
        return self._now


def _iso(clock: FakeClock, offset_s: float = 0) -> str:
    moment = clock() + timedelta(seconds=offset_s)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EventSink:
    def __init__(self) -> None:
        self.events: list = []

    async def aemit(self, event) -> None:
        self.events.append(event)


class FakeApplication:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.session_manager = SessionManager()
        self.started = False
        self.close_count = 0

    async def start(self) -> None:
        self.started = True

    async def aclose(self) -> None:
        self.close_count += 1


def make_request(
    clock: FakeClock,
    tag: str,
    *,
    combo: bool = False,
) -> PreparedPhysicalRequest:
    suffix = f"{tag}-{uuid4().hex[:6]}"

    def item(item_id: str, kind: str, rid: str, action: str, display: str,
             location: str, label: str, step_id: str) -> Requirement:
        return Requirement(
            item_id=item_id,
            key=ResourceKey(
                environment_id="home",
                resource_kind=kind,  # type: ignore[arg-type]
                resource_id=rid,
                action=action,
            ),
            display_name=display,
            location=location,
            action_label=label,
            step_ids=(step_id,),
        )

    def step(step_id: str, item_id: str, binding: str, summary: str) -> PreparedStep:
        return PreparedStep(
            step_id=step_id,
            binding_ref=binding,
            required_item_ids=(item_id,),
            summary=summary,
        )

    if not combo:
        pick = item(f"item-pick-{suffix}", "object", "cup-a", "pick_up",
                    "白色杯子", "卧室床头柜", "拿取", f"step-1-{suffix}")
        requirements = (pick,)
        steps = (step(pick.step_ids[0], pick.item_id, "pick:cup-a", "拿取杯子"),)
        summary = "拿杯子"
    else:
        enter = item(f"item-enter-{suffix}", "area", "bedroom", "enter",
                     "卧室", "卧室", "进入", f"step-enter-{suffix}")
        pick = item(f"item-pick-{suffix}", "object", "cup-b", "pick_up",
                    "蓝色杯子", "卧室书桌", "拿取", f"step-pick-{suffix}")
        requirements = (enter, pick)
        steps = (
            step(enter.step_ids[0], enter.item_id, "nav:bedroom", "进入卧室"),
            step(pick.step_ids[0], pick.item_id, "pick:cup-b", "拿取杯子"),
        )
        summary = "去卧室拿杯子"
    return PreparedPhysicalRequest(
        request_id=f"request-{suffix}",
        approval_id=f"approval-{suffix}",
        environment_id="home",
        session_id=f"session-{suffix}",
        run_id=f"run-{suffix}",
        intent_id=f"intent-{suffix}",
        intent_summary=summary,
        revision=1,
        requirements=requirements,
        steps=steps,
        target_snapshot_revision=f"snap-{suffix}",
        created_at=_iso(clock),
        deadline_at=_iso(clock, 300),
    )


def make_context(
    tmp_path: Path, sink: EventSink | None = None, session_id: str = "session-01"
) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "session_id": session_id,
            "run_id": "run-01",
            "turn_index": 2,
            "tool_call_id": "call-01",
            "permission_subject": PermissionSubject(
                "web-operator", "web", tenant_id="local", capabilities=()
            ),
            "run_context": SimpleNamespace(event_sink=sink),
        },
    )


def build_app(
    store: PermissionStore,
    handler: WebConfirmationHandler | None = None,
    application: Any | None = None,
):
    handler = handler or WebConfirmationHandler(store=store, timeout_s=5)
    app = create_web_app(
        application=application or FakeApplication(),
        confirmation_handler=handler,
        permission_store=store,
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )
    return client, handler, app


def submission_body(
    request: PreparedPhysicalRequest,
    choices: dict[str, str],
    submission_id: str = "sub-001",
) -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "submission_id": submission_id,
        "request_revision": request.revision,
        "decisions": [
            {"item_id": item_id, "choice": choice}
            for item_id, choice in choices.items()
        ],
    }


async def wait_pending(handler: WebConfirmationHandler, count: int = 1) -> None:
    for _ in range(500):
        if handler.pending_count >= count:
            return
        await asyncio.sleep(0.005)
    raise AssertionError("approval was not registered")
