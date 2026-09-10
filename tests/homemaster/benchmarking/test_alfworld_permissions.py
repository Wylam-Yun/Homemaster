"""Task 8 tests: ALFWorld permission-adapter mapping over a fake backend.

The fake implements the same AlfworldBackend seam the production
ThorBackendView serves (identity pinning, scene revisions, receipts), but
it is NOT THOR: real-backend verification stays pending per the
acceptance report. Every behavioral test records the fake backend code
it observed; no result here is presented as a THOR result.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from homemaster.benchmarking.alfworld.permission_adapter import (
    AlfworldBackend,
    AlfworldPermissionAdapter,
    BackendReceipt,
    BackendTarget,
    ThorBackendView,
)
from homemaster.permissions.models import TargetUnresolved
from homemaster.tools.base import ToolExecutionContext


@dataclass
class FakeObject:
    object_id: str
    labels: tuple[str, ...]
    kind: str
    display: str
    location: str
    toggle: bool | None = None


def _scene_objects() -> list[FakeObject]:
    return [
        FakeObject("CupA|001", ("cup a",), "object", "白色杯子", "卧室床头柜"),
        FakeObject("CupB|002", ("cup b", "cup"), "object", "蓝色杯子", "卧室书桌"),
        FakeObject("CupC|003", ("cup",), "object", "红色杯子", "客厅茶几"),
        FakeObject("Countertop|001", ("countertop",), "receptacle", "柜台", "厨房"),
        FakeObject("Microwave|001", ("microwave",), "receptacle", "微波炉", "厨房"),
        FakeObject("Lamp|001", ("lamp",), "toggle", "台灯", "卧室书桌", toggle=False),
    ]


class FakeAlfworldBackend:
    """In-memory scene behind the adapter seam. Not THOR."""

    def __init__(self, *, env: str = "alfworld-test:scene-1") -> None:
        self._env = env
        self._objects: dict[str, FakeObject] = {
            obj.object_id: obj for obj in _scene_objects()
        }
        self._rev = 0
        self.calls: list[tuple[str, Any]] = []
        self.inventory: list[str] = []
        self.fail_next: BackendReceipt | None = None
        self.unknown_next = False

    def environment_id(self) -> str:
        return self._env

    def scene_revision(self) -> str:
        return f"{self._env}#{self._rev}"

    def swap_scene(self, env: str, objects: list[FakeObject]) -> None:
        self._env = env
        self._objects = {obj.object_id: obj for obj in objects}
        self._rev += 1

    def resolve_target(
        self, label: str, *, allowed: frozenset[str]
    ) -> BackendTarget | None:
        if not isinstance(label, str) or not label.strip():
            return None
        norm = label.strip().casefold()
        matches = [
            obj
            for obj in self._objects.values()
            if obj.kind in allowed
            and norm in [item.casefold() for item in obj.labels]
        ]
        ids = {obj.object_id for obj in matches}
        if len(ids) != 1:
            return None
        obj = next(obj for obj in matches if obj.object_id in ids)
        return BackendTarget(
            object_id=obj.object_id,
            canonical_label=obj.labels[0],
            kind=obj.kind,
            display=obj.display,
            location=obj.location,
        )

    def toggle_state(self, object_id: str) -> bool | None:
        obj = self._objects.get(object_id)
        if obj is None or obj.kind != "toggle":
            return None
        return obj.toggle

    async def go_to(self, canonical_label: str) -> BackendReceipt:
        self.calls.append(("go_to", canonical_label))
        return self._next_receipt()

    async def manipulate(
        self, *, action: str, args: dict[str, Any] | Any
    ) -> BackendReceipt:
        payload = dict(args) if isinstance(args, dict) else {}
        self.calls.append(("manipulate", action, payload))
        receipt = self._next_receipt()
        if receipt.ok is True and action == "take" and isinstance(
            payload.get("object"), str
        ):
            self.inventory.append(payload["object"])
        return receipt

    def _next_receipt(self) -> BackendReceipt:
        if self.fail_next is not None:
            receipt, self.fail_next = self.fail_next, None
            return receipt
        if self.unknown_next:
            self.unknown_next = False
            return BackendReceipt(ok=None, backend_code="fake:unknown")
        return BackendReceipt(ok=True, backend_code="fake:ok")


def _context(tmp_path: Path) -> ToolExecutionContext:
    return ToolExecutionContext(tmp_path, metadata={"session_id": "s-1", "run_id": "r-1"})


def _adapter(backend: AlfworldBackend) -> AlfworldPermissionAdapter:
    return AlfworldPermissionAdapter(tool="robot_manipulate", backend=backend)


def _signature(request: Any) -> tuple[tuple[Any, ...], tuple[str, ...]]:
    keys = tuple(
        sorted(
            (
                item.key.environment_id,
                item.key.resource_kind,
                item.key.resource_id,
                item.key.action,
            )
            for item in request.requirements
        )
    )
    return keys, tuple(sorted(step.binding_ref for step in request.steps))


@pytest.mark.asyncio
async def test_take_declares_exact_identity_and_nav_closure(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    request = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    assert backend.calls == []
    assert len(request.requirements) == 1
    item = request.requirements[0]
    assert (item.key.resource_id, item.key.action) == ("CupA|001", "pick_up")
    assert (item.display_name, item.location, item.action_label) == (
        "白色杯子",
        "卧室床头柜",
        "拿取",
    )
    assert len(request.steps) == 2
    assert [step.summary for step in request.steps] == [
        "navigate to cup a",
        "pick_up cup a",
    ]
    for step in request.steps:
        assert step.required_item_ids == (item.item_id,)


@pytest.mark.asyncio
async def test_prepare_is_deterministic(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    call = {"action": "take", "object": "cup a"}
    first = await adapter.prepare(call, _context(tmp_path))
    second = await adapter.prepare(call, _context(tmp_path))
    assert _signature(first) == _signature(second)
    assert first.request_id == second.request_id


@pytest.mark.asyncio
async def test_ambiguous_and_unknown_labels_refuse(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    with pytest.raises(TargetUnresolved):
        await adapter.prepare({"action": "take", "object": "cup"}, _context(tmp_path))
    with pytest.raises(TargetUnresolved):
        await adapter.prepare(
            {"action": "take", "object": "dragon"}, _context(tmp_path)
        )
    assert backend.calls == []


@pytest.mark.asyncio
async def test_heat_without_receptacle_refuses(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    backend._objects = {
        oid: obj
        for oid, obj in backend._objects.items()
        if oid != "Microwave|001"
    }
    adapter = _adapter(backend)
    with pytest.raises(TargetUnresolved):
        await adapter.prepare(
            {"action": "heat", "object": "cup a"}, _context(tmp_path)
        )


@pytest.mark.asyncio
async def test_action_mapping_matrix(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    cases = [
        ({"action": "put", "object": "cup a", "target_receptacle": "countertop"},
         ("CupA|001", "place", "放置")),
        ({"action": "open", "target_receptacle": "microwave"},
         ("Microwave|001", "open", "打开")),
        ({"action": "clean", "object": "cup a", "tool_receptacle": "countertop"},
         ("CupA|001", "clean", "清洗")),
        ({"action": "slice", "object": "cup a"}, ("CupA|001", "slice", "切")),
        ({"action": "turn_on", "object": "lamp"}, ("Lamp|001", "turn_on", "打开")),
    ]
    for call, (resource_id, action, label) in cases:
        request = await adapter.prepare(call, _context(tmp_path))
        item = request.requirements[0]
        assert (item.key.resource_id, item.key.action, item.action_label) == (
            resource_id,
            action,
            label,
        )


@pytest.mark.asyncio
async def test_use_maps_by_toggle_state(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    off = await adapter.prepare({"action": "use", "object": "lamp"}, _context(tmp_path))
    assert off.requirements[0].key.action == "turn_on"
    backend._objects["Lamp|001"].toggle = True
    on = await adapter.prepare({"action": "use", "object": "lamp"}, _context(tmp_path))
    assert on.requirements[0].key.action == "turn_off"
    with pytest.raises(TargetUnresolved):
        await adapter.prepare({"action": "use", "object": "cup a"}, _context(tmp_path))
    backend._objects["Lamp|001"].toggle = None
    with pytest.raises(TargetUnresolved):
        await adapter.prepare({"action": "use", "object": "lamp"}, _context(tmp_path))


@pytest.mark.asyncio
async def test_execute_uses_backend_spellings_and_records_take(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    request = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    context = _context(tmp_path)
    nav_result = await adapter.execute(request.steps[0].binding_ref, context)
    assert nav_result.is_error is False
    op_result = await adapter.execute(request.steps[1].binding_ref, context)
    assert op_result.is_error is False
    assert op_result.metadata["backend_code"] == "fake:ok"
    assert backend.calls[0] == ("go_to", "cup a")
    assert backend.calls[1][0:2] == ("manipulate", "take")
    assert backend.calls[1][2]["object"] == "cup a"
    assert backend.inventory == ["cup a"]


@pytest.mark.asyncio
async def test_execute_consumes_locked_binding_after_scene_swap(
    tmp_path: Path,
) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    request = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    backend.swap_scene(
        "alfworld-test:scene-2",
        [FakeObject("CupA2|777", ("cup a",), "object", "白杯", "他处")],
    )
    context = _context(tmp_path)
    await adapter.execute(request.steps[0].binding_ref, context)
    await adapter.execute(request.steps[1].binding_ref, context)
    assert backend.calls[0] == ("go_to", "cup a")
    assert backend.calls[1][2]["object"] == "cup a"
    reprepared = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    assert _signature(reprepared) != _signature(request)
    assert reprepared.requirements[0].key.resource_id == "CupA2|777"


@pytest.mark.asyncio
async def test_cross_scene_isolation(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    first = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    backend.swap_scene(
        "alfworld-test:scene-2",
        [FakeObject("CupA2|777", ("cup a",), "object", "白杯", "他处")],
    )
    second = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    assert first.environment_id != second.environment_id
    assert first.requirements[0].item_id != second.requirements[0].item_id


@pytest.mark.asyncio
async def test_unknown_and_failed_receipts(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    request = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    context = _context(tmp_path)
    binding = request.steps[1].binding_ref
    fresh = await adapter.observe(binding, context)
    assert fresh.outcome == "unknown"
    assert fresh.backend_code == "unobserved"
    backend.unknown_next = True
    unknown_result = await adapter.execute(binding, context)
    assert unknown_result.is_error is True
    assert unknown_result.metadata["backend_code"] == "fake:unknown"
    observed = await adapter.observe(binding, context)
    assert observed.outcome == "unknown"
    assert observed.backend_code == "fake:unknown"
    assert observed.observed_resource_id == "CupA|001"
    assert observed.evidence_ref
    backend.fail_next = BackendReceipt(ok=False, backend_code="fake:blocked")
    failed_result = await adapter.execute(binding, context)
    assert failed_result.is_error is True
    failed = await adapter.observe(binding, context)
    assert failed.outcome == "no_effect_failure"
    assert failed.backend_code == "fake:blocked"


@pytest.mark.asyncio
async def test_unknown_binding_and_release(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    adapter = _adapter(backend)
    request = await adapter.prepare(
        {"action": "take", "object": "cup a"}, _context(tmp_path)
    )
    context = _context(tmp_path)
    with pytest.raises(TargetUnresolved):
        await adapter.execute("bind-nope", context)
    missing = await adapter.observe("bind-nope", context)
    assert missing.outcome == "unknown"
    binding = request.steps[1].binding_ref
    await adapter.execute(binding, context)
    await adapter.release(request.request_id)
    with pytest.raises(TargetUnresolved):
        await adapter.execute(binding, context)
    gone = await adapter.observe(binding, context)
    assert gone.backend_code == "unknown binding"


@pytest.mark.asyncio
async def test_refuses_unsupported_tools_and_calls(tmp_path: Path) -> None:
    backend = FakeAlfworldBackend()
    with pytest.raises(ValueError):
        AlfworldPermissionAdapter(tool="robot_go_to", backend=backend)
    adapter = _adapter(backend)
    for call in (
        {"action": "dance", "object": "cup a"},
        {"object": "cup a"},
        {"action": "toggle", "object": "lamp"},
        {"action": "take"},
    ):
        with pytest.raises(TargetUnresolved):
            await adapter.prepare(call, _context(tmp_path))


def test_adapter_has_no_translator_import() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "homemaster"
        / "benchmarking"
        / "alfworld"
        / "permission_adapter.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders.extend(
                alias.name for alias in node.names if "translator" in alias.name
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module and "translator" in node.module:
                offenders.append(node.module)
    assert offenders == []


def test_profiles_hook_builds_physical_tool(tmp_path: Path) -> None:
    from homemaster.adapters.profiles import alfworld_physical_manipulate_tool

    backend = FakeAlfworldBackend()
    tool = alfworld_physical_manipulate_tool(backend=backend)
    assert tool.physical is True
    assert isinstance(tool.physical_adapter, AlfworldPermissionAdapter)


def test_thor_view_marks_unverified_seams() -> None:
    view = ThorBackendView(env=None)
    assert view.toggle_state("Lamp|001") is None
