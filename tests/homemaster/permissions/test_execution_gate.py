"""Execution-gate tests: one prepared call, bindings, retries, revocation.

A fake device world stands in for hardware: every assertion about side
effects reads the world directly, never the adapter's own report.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from conftest import FakeClock, decide

from homemaster.agent.messages import ToolCall
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.permissions.models import ResourceKey
from homemaster.permissions.store import PermissionStore
from homemaster.tools import ToolExecutionContext
from homemaster.tools.base import FunctionTool, ToolRegistry, ToolResult
from homemaster.tools.executor import AllowAllPermissionChecker, ToolExecutor


def _iso(clock: FakeClock, offset_s: float = 0) -> str:
    moment = clock() + timedelta(seconds=offset_s)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


class FakeWorld:
    def __init__(self) -> None:
        self.robot_area = "living"
        self.objects = {"cup-a": "bedroom", "cup-b": "bedroom"}
        self.held: str | None = None
        self.backend_calls: list[str] = []
        self.observes: list[str] = []
        self.released: list[str] = []
        self.prepare_calls = 0
        self.on_execute: Any = None


class FakeAdapter:
    """Minimal contract implementation over the fake world."""

    def __init__(self, world: FakeWorld, clock: FakeClock) -> None:
        self.world = world
        self.clock = clock
        self.behaviors: dict[str, list[str]] = {}
        self.last_outcome: dict[str, str] = {}
        self.evidence = 0

    def _base(self, context: Any, summary: str):
        from homemaster.permissions.models import PreparedPhysicalRequest

        suffix = uuid4().hex[:8]
        metadata = context.metadata
        return {
            "request_id": f"request-{suffix}",
            "approval_id": f"approval-{suffix}",
            "environment_id": "home",
            "session_id": str(metadata.get("session_id", "")),
            "run_id": str(metadata.get("run_id", "")),
            "intent_id": f"intent-{suffix}",
            "intent_summary": summary,
            "revision": 1,
            "target_snapshot_revision": f"snap-{suffix}",
            "created_at": _iso(self.clock),
            "deadline_at": _iso(self.clock, 300),
        }, PreparedPhysicalRequest

    def _requirement(self, suffix: str, kind: str, rid: str, action: str,
                     display: str, location: str, label: str, step_id: str):
        from homemaster.permissions.models import Requirement

        return Requirement(
            item_id=f"item-{step_id}-{suffix}",
            key=ResourceKey(
                environment_id="home",
                resource_kind=kind,  # type: ignore[arg-type]
                resource_id=rid,
                action=action,
            ),
            display_name=display,
            location=location,
            action_label=label,
            step_ids=(f"step-{step_id}-{suffix}",),
        )

    def _step(self, suffix: str, step_id: str, item_ids: tuple[str, ...],
              binding: str, summary: str):
        from homemaster.permissions.models import PreparedStep

        return PreparedStep(
            step_id=f"step-{step_id}-{suffix}",
            binding_ref=binding,
            required_item_ids=item_ids,
            summary=summary,
        )

    async def prepare(self, call: Any, context: Any):
        from homemaster.permissions.models import PreparedPhysicalRequest

        self.world.prepare_calls += 1
        op = call.get("op")
        base, _ = self._base(context, str(op))
        suffix = base["request_id"].split("-", 1)[1]
        requirements: list = []
        steps: list = []
        if op == "navigate":
            target = call["area"]
            if self.world.robot_area != target:
                requirements.append(
                    self._requirement(suffix, "area", target, "enter",
                                      target, target, "进入", "enter")
                )
                steps.append(
                    self._step(suffix, "enter",
                               (requirements[0].item_id,), f"nav:{target}",
                               f"进入{target}")
                )
        elif op == "manipulate":
            action, obj = call["action"], call["object"]
            requirements.append(
                self._requirement(suffix, "object", obj, action, obj,
                                  self.world.objects[obj], action, "act")
            )
            steps.append(
                self._step(suffix, "act", (requirements[0].item_id,),
                           f"{action}:{obj}@{self.world.objects[obj]}",
                           f"{action}{obj}")
            )
        elif op == "combo":
            area, action, obj = call["area"], call["action"], call["object"]
            if self.world.robot_area != area:
                requirements.append(
                    self._requirement(suffix, "area", area, "enter",
                                      area, area, "进入", "enter")
                )
                steps.append(
                    self._step(suffix, "enter",
                               (requirements[0].item_id,), f"nav:{area}",
                               f"进入{area}")
                )
            requirements.append(
                self._requirement(suffix, "object", obj, action, obj,
                                  self.world.objects[obj], action, "act")
            )
            steps.append(
                self._step(suffix, "act", (requirements[-1].item_id,),
                           f"{action}:{obj}@{self.world.objects[obj]}",
                           f"{action}{obj}")
            )
        return PreparedPhysicalRequest(
            **base,
            requirements=tuple(requirements),
            steps=tuple(steps),
        )

    async def execute(self, binding_ref: str, context: Any) -> ToolResult:
        del context
        self.world.backend_calls.append(binding_ref)
        if self.world.on_execute is not None:
            self.world.on_execute(binding_ref)
        queue = self.behaviors.get(binding_ref)
        mode = queue.pop(0) if queue else "ok"
        if mode == "timeout":
            raise TimeoutError("injected backend timeout")
        if mode == "no_effect":
            self.last_outcome[binding_ref] = "no_effect_failure"
            return ToolResult("device busy", True,
                              {"status": "busy", "backend_attempted": True})
        kind, target = binding_ref.split(":", 1)
        if kind == "nav":
            self.world.robot_area = target
            text = f"at {target}"
        else:
            obj = target.split("@")[0]
            self.world.held = obj
            text = f"{kind} {obj}"
        self.last_outcome[binding_ref] = mode
        return ToolResult(text, False, {"status": "ok", "backend_attempted": True})

    async def observe(self, binding_ref: str, context: Any):
        from homemaster.permissions.models import ExecutionObservation

        del context
        self.world.observes.append(binding_ref)
        self.evidence += 1
        raw = self.last_outcome.get(binding_ref, "unknown")
        outcome = {"ok": "succeeded", "no_effect": "no_effect_failure"}.get(raw, raw)
        return ExecutionObservation(
            outcome=outcome,  # type: ignore[arg-type]
            backend_code="ok",
            binding_ref=binding_ref,
            observed_resource_id=None,
            current_area_id=self.world.robot_area,
            evidence_ref=f"ev-{self.evidence}",
        )

    async def release(self, request_id: str) -> None:
        self.world.released.append(request_id)


def _boom(arguments: Any, context: Any) -> ToolResult:
    raise AssertionError("physical tools must run through the adapter")


class _Handler:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.calls: list = []
        self.hook: Any = None

    async def confirm(self, tool: Any, arguments: Any, context: Any, decision: Any):
        self.calls.append((tool.name, decision))
        if self.hook is not None:
            self.hook()
        return self.answer


def _harness(tmp_path: Path, world: FakeWorld, clock: FakeClock,
             handler: Any = None, store: PermissionStore | None = None,
             checker: Any = None):
    adapter = FakeAdapter(world, clock)
    tool = FunctionTool(
        name="home_act",
        description="Fake household action.",
        input_schema={"type": "object"},
        execute=_boom,
        physical=True,
        physical_adapter=adapter,
    )
    registry = ToolRegistry()
    registry.register(tool)
    own_store = store or PermissionStore.open(tmp_path / "gate.sqlite3", clock=clock)
    own_checker = checker or PermissionChecker(
        PermissionSettingsConfig(), store=own_store, clock=clock
    )
    executor = ToolExecutor(
        registry,
        permission_checker=own_checker,
        confirmation_handler=handler,
        permission_store=own_store,
    )
    context = ToolExecutionContext(
        tmp_path, metadata={"session_id": "session-g", "run_id": "run-g"}
    )
    return adapter, executor, context, own_store


def _statuses(store: PermissionStore) -> list:
    conn = sqlite3.connect(str(store.path))
    try:
        return [row[0] for row in conn.execute("SELECT status FROM permission_requests")]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_reject_starts_nothing(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    _, executor, context, store = _harness(tmp_path, world, clock, _Handler(False))
    try:
        result = await _await_call(executor, context, {"op": "combo", "area": "bedroom",
                                                 "action": "pick_up", "object": "cup-a"})
        assert result.metadata["status"] == "permission_denied"
        assert world.robot_area == "living"
        assert world.held is None
        assert world.backend_calls == []
        assert _statuses(store) == ["blocked"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_no_handler_starts_nothing(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    _, executor, context, store = _harness(tmp_path, world, clock, None)
    try:
        result = await _await_call(executor, context, {"op": "combo", "area": "bedroom",
                                                 "action": "pick_up", "object": "cup-a"})
        assert result.metadata["status"] == "approval_channel_unavailable"
        assert world.backend_calls == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_nav_allowed_then_pick_rejected(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    handler = _Handler(True)
    _, executor, context, store = _harness(tmp_path, world, clock, handler)
    try:
        first = await _await_call(executor, context, {"op": "navigate", "area": "bedroom"})
        assert first.is_error is False
        assert world.robot_area == "bedroom"
        handler.answer = False
        second = await _await_call(executor, context, {"op": "manipulate",
                                                 "action": "pick_up", "object": "cup-a"})
        assert second.metadata["status"] == "permission_denied"
        assert world.held is None
        assert world.robot_area == "bedroom"
        assert len(handler.calls) == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_grant_skips_card_and_executes(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    handler = _Handler(True)
    adapter, executor, context, store = _harness(tmp_path, world, clock, handler)
    try:
        prepared = await adapter.prepare({"op": "navigate", "area": "bedroom"}, context)
        store.create_request(prepared)
        decide(store, prepared,
               {item.item_id: "allow_always" for item in prepared.requirements},
               "sub-pre-grant")
        result = await _await_call(executor, context, {"op": "navigate", "area": "bedroom"})
        assert result.is_error is False
        assert world.robot_area == "bedroom"
        assert handler.calls == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_target_changed_aborts_before_side_effects(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    handler = _Handler(True)
    _, executor, context, store = _harness(tmp_path, world, clock, handler)
    handler.hook = lambda: world.objects.__setitem__("cup-a", "living")
    try:
        result = await _await_call(executor, context, {"op": "combo", "area": "bedroom",
                                                 "action": "pick_up", "object": "cup-a"})
        assert result.metadata["status"] == "target_changed"
        assert world.backend_calls == []
        assert world.held is None
        assert _statuses(store) == ["cancelled"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_retry_on_no_effect_then_success(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    adapter, executor, context, store = _harness(
        tmp_path, world, clock, _Handler(True))
    adapter.behaviors["pick_up:cup-a@bedroom"] = ["no_effect", "ok"]
    try:
        result = await _await_call(executor, context, {"op": "combo", "area": "bedroom",
                                                 "action": "pick_up", "object": "cup-a"})
        assert result.is_error is False
        assert world.held == "cup-a"
        assert world.backend_calls.count("pick_up:cup-a@bedroom") == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unknown_outcome_does_not_retry(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    adapter, executor, context, store = _harness(
        tmp_path, world, clock, _Handler(True))
    adapter.behaviors["pick_up:cup-a@bedroom"] = ["timeout"]
    try:
        result = await _await_call(executor, context, {"op": "combo", "area": "bedroom",
                                                 "action": "pick_up", "object": "cup-a"})
        assert result.metadata["status"] == "outcome_unknown"
        assert world.backend_calls.count("pick_up:cup-a@bedroom") == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_revoke_before_second_step_stops(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    handler = _Handler(True)
    adapter, executor, context, store = _harness(tmp_path, world, clock, handler)
    try:
        prepared = await adapter.prepare({"op": "combo", "area": "bedroom",
                                            "action": "pick_up", "object": "cup-a"}, context)
        store.create_request(prepared)
        resolution = decide(store, prepared,
                            {item.item_id: "allow_always"
                             for item in prepared.requirements},
                            "sub-revoke-setup")
        grants = dict(zip(
            [item.item_id for item in prepared.requirements],
            resolution.persisted_grant_ids,
            strict=True,
        ))
        pick_grant = grants[[i.item_id for i in prepared.requirements
                             if i.key.action == "pick_up"][0]]

        def revoke_pick(binding: str) -> None:
            if binding.startswith("nav:"):
                store.revoke(pick_grant, "rev-pick", 1, "tester")

        world.on_execute = revoke_pick
        result = await _await_call(executor, context, {"op": "combo", "area": "bedroom",
                                                 "action": "pick_up", "object": "cup-a"})
        assert result.metadata["status"] == "permission_denied"
        assert world.robot_area == "bedroom"
        assert world.held is None
        assert handler.calls == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_once_consumed_after_success(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    handler = _Handler(True)
    _, executor, context, store = _harness(tmp_path, world, clock, handler)
    try:
        call = {"op": "manipulate", "action": "pick_up", "object": "cup-b"}
        first = await _await_call(executor, context, call)
        assert first.is_error is False
        second = await _await_call(executor, context, call)
        assert second.is_error is False
        assert len(handler.calls) == 2
    finally:
        store.close()


@pytest.mark.asyncio
async def test_suppressed_second_ask_in_same_run(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    handler = _Handler(False)
    _, executor, context, store = _harness(tmp_path, world, clock, handler)
    try:
        call = {"op": "manipulate", "action": "pick_up", "object": "cup-b"}
        first = await _await_call(executor, context, call)
        assert first.metadata["status"] == "permission_denied"
        second = await _await_call(executor, context, call)
        assert second.metadata["status"] == "permission_denied"
        assert len(handler.calls) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_stay_put_needs_no_request(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    world.robot_area = "bedroom"
    _, executor, context, store = _harness(
        tmp_path, world, clock, _Handler(True))
    try:
        result = await _await_call(executor, context, {"op": "navigate", "area": "bedroom"})
        assert result.is_error is False
        assert world.backend_calls == []
        assert _statuses(store) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_physical_without_store_is_configuration_error(tmp_path: Path) -> None:
    world, clock = FakeWorld(), FakeClock()
    adapter = FakeAdapter(world, clock)
    tool = FunctionTool(
        name="home_act",
        description="Fake household action.",
        input_schema={"type": "object"},
        execute=_boom,
        physical=True,
        physical_adapter=adapter,
    )
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(registry, permission_checker=AllowAllPermissionChecker())
    context = ToolExecutionContext(tmp_path, metadata={"run_id": "r", "session_id": "s"})
    result = await executor.execute(
        ToolCall(id="1", name="home_act", arguments={"op": "navigate", "area": "x"}),
        context,
    )
    assert result.metadata["status"] == "permission_configuration_error"
    assert world.prepare_calls == 0


async def _await_call(executor: ToolExecutor, context: Any, call: dict) -> ToolResult:
    return await executor.execute(
        ToolCall(id="1", name="home_act", arguments=call), context
    )
