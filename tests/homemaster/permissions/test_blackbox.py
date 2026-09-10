"""External blackbox tests: permission core against a subprocess device.

Every assertion about physical effects reads the device process directly
(``read``), never the adapter's own report. The device is an external test
system, not hardware: it runs in its own process, speaks stdlib JSONL, and
can be killed and restarted mid-suite.

Id policy (mirrors the production executor contract): request/item/step
ids are unique per prepare (uuid); requirement keys and binding refs are
content-derived, so the approval-wait revalidation sees identical
signatures for the same call against unchanged state.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from conftest import FakeClock, decide

from homemaster.agent.messages import ToolCall
from homemaster.permissions import PermissionChecker, PermissionSettingsConfig
from homemaster.permissions.models import (
    ApprovalCancelled,
    ApprovalSubmission,
    ExecutionObservation,
    ItemDecision,
    PreparedPhysicalRequest,
    PreparedStep,
    Requirement,
    ResourceKey,
    TargetUnresolved,
)
from homemaster.permissions.store import MAX_ATTEMPTS_DEFAULT, PermissionStore
from homemaster.tools import ToolExecutionContext
from homemaster.tools.base import FunctionTool, ToolRegistry, ToolResult
from homemaster.tools.executor import ToolExecutor

DEVICE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "permissions" / "device_process.py"
)
ENV_ID = "home-test"

DISPLAY = {"cup-a": "白色杯子", "cup-b": "蓝色杯子"}
AREA_DISPLAY = {"living": "客厅", "bedroom": "卧室", "kitchen": "厨房"}
ACTION_LABELS = {"pick_up": "拿取", "place": "放置", "clean": "清洗", "enter": "进入"}


class DeviceProcess:
    """Owner of one device subprocess. Tests read it directly."""

    def __init__(self) -> None:
        self._next_id = 0
        self._proc = subprocess.Popen(
            [sys.executable, str(DEVICE_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.alive = True

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.alive or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("device process is gone")
        self._next_id += 1
        payload = {"id": self._next_id, **payload}
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except BrokenPipeError as exc:
            self.alive = False
            raise RuntimeError("device process is gone") from exc
        line = self._proc.stdout.readline()
        if not line:
            self.alive = False
            raise RuntimeError("device process is gone")
        response = json.loads(line)
        assert response["id"] == self._next_id
        return response

    def read(self) -> dict[str, Any]:
        response = self._send({"cmd": "read"})
        assert response["ok"] is True, response
        return response["state"]

    def fail_next(self) -> None:
        response = self._send({"cmd": "fail_next", "fail": True})
        assert response["ok"] is True, response

    def kill(self) -> None:
        self.alive = False
        self._proc.kill()
        self._proc.wait(timeout=10)

    def close(self) -> bytes:
        stderr = b""
        if self.alive:
            try:
                self._send({"cmd": "shutdown"})
            except RuntimeError:
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
            self.alive = False
        if self._proc.stderr is not None:
            stderr = self._proc.stderr.read() or b""
        return stderr


class HomeDeviceAdapter:
    """Task-1 contract over the subprocess device. Read-only prepare."""

    def __init__(self, device: DeviceProcess) -> None:
        self.device = device
        self.exec_attempts: dict[str, int] = {}
        self.last_receipt: dict[str, dict[str, Any]] = {}
        self.released: list[str] = []
        self.on_execute: Any = None

    def _read(self) -> dict[str, Any]:
        return self.device.read()

    async def prepare(self, call: Any, context: Any) -> PreparedPhysicalRequest:
        del context
        args = dict(call) if isinstance(call, Mapping) else {}
        op = args.get("op")
        state = self._read()
        if op == "enter":
            return self._prepare_enter(args, state)
        if op in {"take", "clean", "place"}:
            return self._prepare_object(op, args, state)
        if op == "combo":
            return self._prepare_combo(args, state)
        raise TargetUnresolved(f"unknown blackbox op {op!r}")

    def _ids(self) -> tuple[str, str, str, str]:
        suffix = uuid4().hex[:12]
        return (
            f"request-{suffix}",
            f"approval-{suffix}",
            f"intent-{suffix}",
            suffix,
        )

    def _prepare_enter(self, args: dict, state: dict) -> PreparedPhysicalRequest:
        area = args.get("area")
        if area not in AREA_DISPLAY:
            raise TargetUnresolved(f"unknown area {area!r}")
        request_id, approval_id, intent_id, suffix = self._ids()
        if state["area"] == area:
            return PreparedPhysicalRequest(
                request_id=request_id, approval_id=approval_id,
                environment_id=ENV_ID, session_id="bb-session", run_id="bb-run",
                intent_id=intent_id, intent_summary=f"进入{AREA_DISPLAY[area]}",
                revision=1, requirements=(), steps=(),
                target_snapshot_revision="bb-snap",
                created_at=_iso_now(), deadline_at=_iso_now(300),
            )
        item = Requirement(
            item_id=f"item-enter-{area}-{suffix}",
            key=ResourceKey(environment_id=ENV_ID, resource_kind="area",
                            resource_id=area, action="enter"),
            display_name=AREA_DISPLAY[area], location=AREA_DISPLAY[area],
            action_label="进入", step_ids=(f"step-enter-{area}-{suffix}",),
        )
        step = PreparedStep(
            step_id=f"step-enter-{area}-{suffix}", binding_ref=f"move:{area}",
            required_item_ids=(item.item_id,), summary=f"进入{AREA_DISPLAY[area]}",
        )
        return PreparedPhysicalRequest(
            request_id=request_id, approval_id=approval_id,
            environment_id=ENV_ID, session_id="bb-session", run_id="bb-run",
            intent_id=intent_id, intent_summary=f"进入{AREA_DISPLAY[area]}",
            revision=1, requirements=(item,), steps=(step,),
            target_snapshot_revision="bb-snap",
            created_at=_iso_now(), deadline_at=_iso_now(300),
        )

    def _prepare_object(self, op: str, args: dict, state: dict) -> PreparedPhysicalRequest:
        name = args.get("object")
        obj = state["objects"].get(name) if isinstance(name, str) else None
        if obj is None:
            raise TargetUnresolved(f"unknown object {name!r}")
        action = {"take": "pick_up", "clean": "clean", "place": "place"}[op]
        spot = str(args.get("spot") or obj["spot"])
        area = state["area"]
        request_id, approval_id, intent_id, suffix = self._ids()
        step_ids: list[str] = []
        if not obj["held"] and obj["area"] != area:
            step_ids.append(f"step-nav-{name}-{suffix}")
        step_ids.append(f"step-{op}-{name}-{suffix}")
        item = Requirement(
            item_id=f"item-{op}-{name}-{suffix}",
            key=ResourceKey(environment_id=ENV_ID, resource_kind="object",
                            resource_id=name, action=action),
            display_name=DISPLAY[name], location=f"{AREA_DISPLAY[area]} · {obj['spot']}",
            action_label=ACTION_LABELS[action], step_ids=tuple(step_ids),
        )
        steps: list[PreparedStep] = []
        if not obj["held"] and obj["area"] != area:
            steps.append(PreparedStep(
                step_id=step_ids[0], binding_ref=f"move:{obj['area']}",
                required_item_ids=(item.item_id,), summary=f"前往{obj['area']}",
            ))
        op_binding = (
            f"place:{name}@{spot}" if op == "place" else f"{op}:{name}@{area}"
        )
        steps.append(PreparedStep(
            step_id=step_ids[-1], binding_ref=op_binding,
            required_item_ids=(item.item_id,), summary=f"{op}{name}",
        ))
        item = item.model_copy(update={"step_ids": tuple(step_ids)})
        return PreparedPhysicalRequest(
            request_id=request_id, approval_id=approval_id,
            environment_id=ENV_ID, session_id="bb-session", run_id="bb-run",
            intent_id=intent_id,
            intent_summary=f"{ACTION_LABELS[action]}{DISPLAY[name]}",
            revision=1, requirements=(item,), steps=tuple(steps),
            target_snapshot_revision="bb-snap",
            created_at=_iso_now(), deadline_at=_iso_now(300),
        )

    def _prepare_combo(self, args: dict, state: dict) -> PreparedPhysicalRequest:
        area = args.get("area")
        name = args.get("object")
        action = args.get("action")
        if area not in AREA_DISPLAY or action != "pick_up":
            raise TargetUnresolved(f"unsupported combo {args!r}")
        obj = state["objects"].get(name) if isinstance(name, str) else None
        if obj is None:
            raise TargetUnresolved(f"unknown object {name!r}")
        if state["area"] == area:
            raise TargetUnresolved("combo needs no navigation here; split the calls")
        request_id, approval_id, intent_id, suffix = self._ids()
        enter = Requirement(
            item_id=f"item-enter-{area}-{suffix}",
            key=ResourceKey(environment_id=ENV_ID, resource_kind="area",
                            resource_id=area, action="enter"),
            display_name=AREA_DISPLAY[area], location=AREA_DISPLAY[area],
            action_label="进入", step_ids=(f"step-enter-{area}-{suffix}",),
        )
        pick = Requirement(
            item_id=f"item-pick-{name}-{suffix}",
            key=ResourceKey(environment_id=ENV_ID, resource_kind="object",
                            resource_id=name, action="pick_up"),
            display_name=DISPLAY[name],
            location=f"{AREA_DISPLAY[obj['area']]} · {obj['spot']}",
            action_label="拿取", step_ids=(f"step-pick-{name}-{suffix}",),
        )
        steps = (
            PreparedStep(
                step_id=f"step-enter-{area}-{suffix}", binding_ref=f"move:{area}",
                required_item_ids=(enter.item_id,), summary=f"进入{AREA_DISPLAY[area]}",
            ),
            PreparedStep(
                step_id=f"step-pick-{name}-{suffix}",
                binding_ref=f"take:{name}@{obj['area']}",
                required_item_ids=(pick.item_id,), summary=f"拿取{name}",
            ),
        )
        return PreparedPhysicalRequest(
            request_id=request_id, approval_id=approval_id,
            environment_id=ENV_ID, session_id="bb-session", run_id="bb-run",
            intent_id=intent_id,
            intent_summary=f"进入{AREA_DISPLAY[area]}并拿取{DISPLAY[name]}",
            revision=1, requirements=(enter, pick), steps=steps,
            target_snapshot_revision="bb-snap",
            created_at=_iso_now(), deadline_at=_iso_now(300),
        )

    async def execute(self, binding_ref: str, context: Any) -> ToolResult:
        del context
        if self.on_execute is not None:
            self.on_execute(binding_ref)
        self.exec_attempts[binding_ref] = self.exec_attempts.get(binding_ref, 0) + 1
        kind, _, rest = binding_ref.partition(":")
        if kind == "move":
            response = self.device._send({"cmd": "move", "area": rest})
        elif kind == "take":
            response = self.device._send({"cmd": "pick_up", "object": rest.split("@")[0]})
        elif kind == "place":
            parts = rest.split("@")
            response = self.device._send(
                {"cmd": "place", "object": parts[0],
                 "spot": parts[1] if len(parts) > 1 else "floor"}
            )
        elif kind == "clean":
            response = self.device._send({"cmd": "clean", "object": rest.split("@")[0]})
        else:
            raise TargetUnresolved(f"unknown binding {binding_ref!r}")
        self.last_receipt[binding_ref] = response
        if response["ok"] is True:
            return ToolResult(f"device {kind} ok", False,
                              {"status": "ok", "backend_attempted": True,
                               "backend_code": response["code"]})
        return ToolResult(f"device {kind} failed: {response['code']}", True,
                          {"status": "device-failed", "backend_attempted": True,
                           "backend_code": response["code"]})

    async def observe(self, binding_ref: str, context: Any) -> ExecutionObservation:
        del context
        receipt = self.last_receipt.get(binding_ref)
        obj = binding_ref.split(":")[1].split("@")[0] if ":" in binding_ref else None
        if receipt is None:
            return ExecutionObservation(
                outcome="unknown", backend_code="unobserved", binding_ref=binding_ref,
                observed_resource_id=obj, current_area_id=None, evidence_ref="unobserved",
            )
        outcome = "succeeded" if receipt["ok"] is True else "no_effect_failure"
        return ExecutionObservation(
            outcome=outcome, backend_code=f"device:{receipt['code']}",
            binding_ref=binding_ref, observed_resource_id=obj, current_area_id=None,
            evidence_ref=f"device:{binding_ref}:{receipt['code']}",
        )

    async def release(self, request_id: str) -> None:
        self.released.append(request_id)


def _iso_now(offset_s: float = 0) -> str:
    moment = datetime.now(UTC) + timedelta(seconds=offset_s)
    return moment.isoformat().replace("+00:00", "Z")


class DecisionHandler:
    """Scripted approver keyed by (action, resource). Missing rule refuses."""

    def __init__(self, store: PermissionStore) -> None:
        self.store = store
        self.rules: dict[tuple[str, str], str] = {}
        self.calls: list[str] = []

    def allow(self, action: str, resource_id: str, choice: str = "allow_always") -> None:
        self.rules[(action, resource_id)] = choice

    async def confirm(self, request: Any, missing_item_ids: Any, context: Any) -> Any:
        del missing_item_ids, context
        self.calls.append(request.approval_id)
        decisions = []
        for item in request.requirements:
            choice = self.rules.get((item.key.action, item.key.resource_id))
            if choice is None:
                raise ApprovalCancelled(
                    f"blackbox refuses {(item.key.action, item.key.resource_id)}"
                )
            decisions.append(ItemDecision(item_id=item.item_id, choice=choice))  # type: ignore[arg-type]
        return self.store.submit(
            request.approval_id,
            ApprovalSubmission(
                submission_id=f"sub-{request.request_id}",
                request_revision=request.revision,
                decisions=tuple(decisions),
            ),
            "blackbox",
        )


def _boom(arguments: Any, context: Any) -> ToolResult:
    raise AssertionError("physical tools must run through the adapter")


class Blackbox:
    """One device + real store/checker/executor wiring."""

    def __init__(self, tmp_path: Path, name: str = "bb") -> None:
        self.device = DeviceProcess()
        self.adapter = HomeDeviceAdapter(self.device)
        tool = FunctionTool(
            name="home_device", description="Blackbox household device.",
            input_schema={"type": "object"}, execute=_boom,
            physical=True, physical_adapter=self.adapter,
        )
        registry = ToolRegistry()
        registry.register(tool)
        clock = FakeClock()
        self.store = PermissionStore.open(tmp_path / f"{name}.sqlite3", clock=clock)
        self.checker = PermissionChecker(
            PermissionSettingsConfig(), store=self.store, clock=clock
        )
        self.handler = DecisionHandler(self.store)
        self.executor = ToolExecutor(
            registry, permission_checker=self.checker,
            confirmation_handler=self.handler, permission_store=self.store,
        )
        self.context = ToolExecutionContext(
            tmp_path, metadata={"session_id": "bb-session", "run_id": "bb-run"}
        )

    async def run(self, call: dict[str, Any]) -> ToolResult:
        return await self.executor.execute(
            ToolCall(id="1", name="home_device", arguments=call), self.context
        )

    def state(self) -> dict[str, Any]:
        return self.device.read()

    def side_effect_ops(self) -> dict[str, int]:
        ops = self.state()["ops"]
        return {key: ops[key] for key in ("move", "pick_up", "place", "clean")}

    def close(self) -> None:
        self.store.close()
        stderr = self.device.close()
        assert stderr == b"", f"device stderr not clean: {stderr!r}"


def _request_count(store: PermissionStore) -> int:
    conn = sqlite3.connect(str(store.path))
    try:
        return conn.execute("SELECT COUNT(*) FROM permission_requests").fetchone()[0]
    finally:
        conn.close()


def _grant_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM permission_grants WHERE revoked_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()


def _request_status(store: PermissionStore, request_id: str) -> str:
    conn = sqlite3.connect(str(store.path))
    try:
        row = conn.execute(
            "SELECT status FROM permission_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        assert row is not None
        return row[0]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_first_approval_zero_change(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t01")
    try:
        result = await bb.run({"op": "take", "object": "cup-a"})
        assert result.is_error is True
        assert result.metadata["status"] == "permission_denied"
        assert bb.side_effect_ops() == {"move": 0, "pick_up": 0, "place": 0, "clean": 0}
        state = bb.state()
        assert state["area"] == "living"
        assert state["objects"]["cup-a"]["held"] is False
        assert _request_count(bb.store) == 1
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_grant_a_does_not_cover_b(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t02")
    try:
        bb.handler.allow("pick_up", "cup-a")
        first = await bb.run({"op": "take", "object": "cup-a"})
        assert first.is_error is False
        assert bb.state()["objects"]["cup-a"]["held"] is True
        second = await bb.run({"op": "take", "object": "cup-b"})
        assert second.is_error is True
        state = bb.state()
        assert state["objects"]["cup-b"]["held"] is False
        assert state["objects"]["cup-a"]["held"] is True
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_take_grant_excludes_clean(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t03")
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_once")
        assert (await bb.run({"op": "take", "object": "cup-a"})).is_error is False
        refused = await bb.run({"op": "clean", "object": "cup-a"})
        assert refused.is_error is True
        assert bb.state()["objects"]["cup-a"]["clean"] is False
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_once_consumed_reasks(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t04")
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_once")
        bb.handler.allow("place", "cup-a", "allow_once")
        assert (await bb.run({"op": "take", "object": "cup-a"})).is_error is False
        assert (await bb.run({"op": "place", "object": "cup-a", "spot": "shelf"})).is_error is False
        assert bb.state()["objects"]["cup-a"]["held"] is False
        bb.handler.rules.clear()
        again = await bb.run({"op": "take", "object": "cup-a"})
        assert again.is_error is True
        assert bb.state()["objects"]["cup-a"]["held"] is False
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_bounded_retry_counts_attempts(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t05")
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_once")
        bb.device.fail_next()
        result = await bb.run({"op": "take", "object": "cup-a"})
        assert result.is_error is False
        assert result.metadata["backend_code"] == "pick_up-ok"
        assert bb.state()["ops"]["pick_up"] == 2
        assert bb.state()["objects"]["cup-a"]["held"] is True
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_unknown_outcome_never_resends(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t06")
    replacement: DeviceProcess | None = None
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_once")
        killed: list[str] = []

        def hook(binding: str) -> None:
            if not killed:
                killed.append(binding)
                bb.device.kill()

        bb.adapter.on_execute = hook
        result = await bb.run({"op": "take", "object": "cup-a"})
        assert result.is_error is True
        assert result.metadata["status"] == "outcome_unknown"
        assert sum(bb.adapter.exec_attempts.values()) == 1
        replacement = DeviceProcess()
        bb.adapter.device = replacement
        bb.adapter.on_execute = None
        retry = await bb.run({"op": "take", "object": "cup-a"})
        assert retry.is_error is False
        assert replacement.read()["objects"]["cup-a"]["held"] is True
    finally:
        bb.store.close()
        try:
            bb.device.close()
        except Exception:
            pass
        if replacement is not None:
            assert replacement.close() == b""


@pytest.mark.asyncio
async def test_partial_reject_zero_side_effects(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t07")
    try:
        bb.handler.allow("enter", "bedroom", "allow_always")
        bb.handler.allow("pick_up", "cup-b", "reject")
        result = await bb.run(
            {"op": "combo", "area": "bedroom", "action": "pick_up", "object": "cup-b"}
        )
        assert result.is_error is True
        assert bb.side_effect_ops() == {"move": 0, "pick_up": 0, "place": 0, "clean": 0}
        assert bb.state()["area"] == "living"
        from homemaster.permissions.models import ResourceKey

        covered = bb.store.matching_grants([
            ResourceKey(environment_id=ENV_ID, resource_kind="area",
                        resource_id="bedroom", action="enter"),
        ])
        assert len(covered) == 1
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_completed_nav_survives_later_reject(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t08")
    try:
        bb.handler.allow("enter", "bedroom", "allow_once")
        assert (await bb.run({"op": "enter", "area": "bedroom"})).is_error is False
        assert bb.state()["area"] == "bedroom"
        bb.handler.rules.clear()
        refused = await bb.run({"op": "take", "object": "cup-b"})
        assert refused.is_error is True
        assert bb.state()["area"] == "bedroom"
        assert bb.state()["objects"]["cup-b"]["held"] is False
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_destination_only_no_reask_inside(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t09")
    try:
        bb.handler.allow("enter", "bedroom", "allow_once")
        assert (await bb.run({"op": "enter", "area": "bedroom"})).is_error is False
        before = _request_count(bb.store)
        moves_before = bb.state()["ops"]["move"]
        result = await bb.run({"op": "enter", "area": "bedroom"})
        assert result.is_error is False
        assert "no physical effects required" in result.output
        assert _request_count(bb.store) == before
        assert bb.state()["ops"]["move"] == moves_before
        assert bb.state()["area"] == "bedroom"
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_leave_and_reenter_needs_approval(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t10")
    try:
        bb.handler.allow("enter", "bedroom", "allow_once")
        bb.handler.allow("enter", "living", "allow_once")
        assert (await bb.run({"op": "enter", "area": "bedroom"})).is_error is False
        assert (await bb.run({"op": "enter", "area": "living"})).is_error is False
        bb.handler.rules.clear()
        refused = await bb.run({"op": "enter", "area": "bedroom"})
        assert refused.is_error is True
        assert bb.state()["area"] == "living"
        bb.handler.allow("enter", "bedroom", "allow_once")
        assert (await bb.run({"op": "enter", "area": "bedroom"})).is_error is False
        assert bb.state()["area"] == "bedroom"
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_object_grant_follows_object(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t11")
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_always")
        bb.handler.allow("enter", "bedroom", "allow_once")
        bb.handler.allow("place", "cup-a", "allow_once")
        assert (await bb.run({"op": "take", "object": "cup-a"})).is_error is False
        assert (await bb.run({"op": "enter", "area": "bedroom"})).is_error is False
        placed = await bb.run({"op": "place", "object": "cup-a", "spot": "desk"})
        assert placed.is_error is False
        assert bb.state()["objects"]["cup-a"]["area"] == "bedroom"
        bb.handler.rules.clear()
        calls_before = len(bb.handler.calls)
        again = await bb.run({"op": "take", "object": "cup-a"})
        assert again.is_error is False
        assert len(bb.handler.calls) == calls_before
        state = bb.state()
        assert state["objects"]["cup-a"]["held"] is True
        assert state["area"] == "bedroom"
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_place_spot_locked_per_request(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t12")
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_once")
        assert (await bb.run({"op": "take", "object": "cup-a"})).is_error is False
        first = await bb.adapter.prepare(
            {"op": "place", "object": "cup-a", "spot": "table"}, bb.context
        )
        second = await bb.adapter.prepare(
            {"op": "place", "object": "cup-a", "spot": "shelf"}, bb.context
        )
        first_bindings = sorted(step.binding_ref for step in first.steps)
        second_bindings = sorted(step.binding_ref for step in second.steps)
        assert first_bindings != second_bindings
        bb.handler.allow("place", "cup-a", "allow_once")
        assert (
            await bb.run({"op": "place", "object": "cup-a", "spot": "table"})
        ).is_error is False
        assert bb.state()["objects"]["cup-a"]["spot"] == "table"
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_revoke_claim_interleaving(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t13")
    try:
        request = await bb.adapter.prepare(
            {"op": "take", "object": "cup-a"}, bb.context
        )
        bb.store.create_request(request)
        resolution = decide(
            bb.store, request,
            {request.requirements[0].item_id: "allow_always"}, "sub-t13",
        )
        assert resolution.request_status == "ready"
        binding = bb.store.claim_step(
            request.request_id,
            request.steps[-1].step_id,
            request.revision,
            MAX_ATTEMPTS_DEFAULT,
        )
        assert binding
        grant_id = resolution.persisted_grant_ids[0]
        bb.store.revoke(grant_id, "sub-t13-revoke", 1, "blackbox")

        assert bb.store.matching_grants([request.requirements[0].key]) == {}
        assert bb.side_effect_ops() == {"move": 0, "pick_up": 0, "place": 0, "clean": 0}
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_duplicate_submission_single_effect(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t14")
    try:
        request = await bb.adapter.prepare(
            {"op": "take", "object": "cup-a"}, bb.context
        )
        bb.store.create_request(request)
        item_id = request.requirements[0].item_id
        first = decide(bb.store, request, {item_id: "allow_always"}, "sub-dup")
        second = decide(bb.store, request, {item_id: "allow_always"}, "sub-dup")
        assert first.request_status == second.request_status == "ready"
        assert _grant_count(tmp_path / "t14.sqlite3") == 1
        assert bb.side_effect_ops() == {"move": 0, "pick_up": 0, "place": 0, "clean": 0}
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_failed_attempt_leaves_no_partial_state(tmp_path: Path) -> None:
    bb = Blackbox(tmp_path, "t15")
    try:
        before = bb.state()
        bb.device.fail_next()
        result = await bb.adapter.execute("take:cup-a@living", bb.context)
        assert result.is_error is True
        assert result.metadata["backend_code"] == "injected-failure"
        after = bb.state()
        assert after["objects"]["cup-a"] == before["objects"]["cup-a"]
        assert after["area"] == before["area"]
        observed = await bb.adapter.observe("take:cup-a@living", bb.context)
        assert observed.outcome == "no_effect_failure"
        assert observed.backend_code == "device:injected-failure"
        assert observed.evidence_ref
    finally:
        bb.close()


@pytest.mark.asyncio
async def test_disconnect_cancel_restart(tmp_path: Path) -> None:
    path = tmp_path / "restart.sqlite3"
    bb = Blackbox(tmp_path, "restart")
    try:
        bb.handler.allow("pick_up", "cup-a", "allow_always")
        assert (await bb.run({"op": "take", "object": "cup-a"})).is_error is False
        assert bb.state()["objects"]["cup-a"]["held"] is True
    finally:
        bb.store.close()
        assert bb.device.close() == b""
    store2 = PermissionStore.open(path, clock=FakeClock())
    device2 = DeviceProcess()
    try:

        adapter2 = HomeDeviceAdapter(device2)
        tool = FunctionTool(
            name="home_device", description="Blackbox household device.",
            input_schema={"type": "object"}, execute=_boom,
            physical=True, physical_adapter=adapter2,
        )
        from homemaster.tools.base import ToolRegistry as _Registry

        registry = _Registry()
        registry.register(tool)
        checker2 = PermissionChecker(
            PermissionSettingsConfig(), store=store2, clock=FakeClock()
        )
        handler2 = DecisionHandler(store2)
        executor2 = ToolExecutor(
            registry, permission_checker=checker2,
            confirmation_handler=handler2, permission_store=store2,
        )
        context2 = ToolExecutionContext(
            tmp_path, metadata={"session_id": "bb-session", "run_id": "bb-run"}
        )
        retaken = await executor2.execute(
            ToolCall(id="1", name="home_device",
                     arguments={"op": "take", "object": "cup-a"}),
            context2,
        )
        assert retaken.is_error is False
        assert handler2.calls == []
        assert device2.read()["objects"]["cup-a"]["held"] is True
        cancel_req = await adapter2.prepare({"op": "enter", "area": "kitchen"}, context2)
        store2.create_request(cancel_req)
        assert store2.cancel(cancel_req.request_id, "blackbox cancel") == "cancelled"
        assert device2.read()["area"] == "living"
    finally:
        store2.close()
        assert device2.close() == b""
