"""Interface audit for the V3.4 generic permission contract.

 Guards the real-robot-first boundary: the generic permissions package must
 not import simulator code, generic DTOs must not carry simulator fields,
 and physical tools must declare an explicit adapter (never name sniffing).
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

PERMISSIONS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "homemaster"
    / "permissions"
)
BANNED_IMPORT_PARTS = ("benchmarking", "alfworld", "thor")
BANNED_FIELD_NAMES = frozenset(
    {
        "scene",
        "episode",
        "scene_id",
        "episode_id",
        "native_ref",
        "native_handle",
        "simulator",
        "thor",
        "admissible_commands",
        "backend_handle",
    }
)


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def test_permissions_package_has_no_simulator_imports() -> None:
    assert PERMISSIONS_DIR.is_dir()
    offenders: list[str] = []
    for path in sorted(PERMISSIONS_DIR.glob("*.py")):
        for name in _imported_module_names(path):
            lowered = name.casefold()
            if any(part in lowered for part in BANNED_IMPORT_PARTS):
                offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_generic_models_have_no_simulator_fields() -> None:
    models_path = PERMISSIONS_DIR / "models.py"
    assert models_path.is_file()
    tree = ast.parse(models_path.read_text(encoding="utf-8"))
    fields: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(
                    item.target, ast.Name
                ):
                    fields.add(item.target.id)
    assert fields, "expected DTO fields in models.py"
    assert fields.isdisjoint(BANNED_FIELD_NAMES)


class _StubAdapter:
    async def prepare(self, call: Any, context: Any) -> Any:
        raise NotImplementedError

    async def execute(self, binding_ref: str, context: Any) -> Any:
        raise NotImplementedError

    async def observe(self, binding_ref: str, context: Any) -> Any:
        raise NotImplementedError

    async def release(self, request_id: str) -> None:
        raise NotImplementedError


def _registered(name: str = "probe_tool") -> Any:
    from homemaster.tools.contracts import ExecutionBackend

    return SimpleNamespace(
        definition=SimpleNamespace(
            internal_id="homemaster.probe_tool.v1",
            model_alias=name,
            description="Probe tool.",
            input_schema={"type": "object"},
            state_effects=(),
            execution_backend=ExecutionBackend.IN_PROCESS,
            required_capabilities=(),
            verification_policy=SimpleNamespace(
                execution_proof=SimpleNamespace(value="none"),
                terminal_rule=SimpleNamespace(value="none"),
            ),
            requires_model_observation=False,
            concurrency_policy=SimpleNamespace(value="parallel"),
            resource_key=None,
        ),
        executor=SimpleNamespace(),
        verifier=None,
        resource_key_resolver=None,
    )


def test_physical_adapter_passthrough_keeps_identity() -> None:
    from homemaster.tools.adapters import from_registered_tool
    from homemaster.tools.base import FunctionTool

    adapter = _StubAdapter()
    direct = FunctionTool(
        name="probe_tool",
        description="Probe tool.",
        input_schema={"type": "object"},
        execute=lambda arguments, context: "ok",
        physical=True,
        physical_adapter=adapter,
    )
    assert direct.physical_adapter is adapter
    wrapped = from_registered_tool(
        _registered(), physical=True, physical_adapter=adapter
    )
    assert wrapped.physical_adapter is adapter


def test_missing_adapter_is_refused_not_silently_ordinary() -> None:
    from homemaster.tools.adapters import from_registered_tool
    from homemaster.tools.base import FunctionTool, ToolRegistry

    with pytest.raises(ValueError):
        FunctionTool(
            name="probe_tool",
            description="Probe tool.",
            input_schema={"type": "object"},
            execute=lambda arguments, context: "ok",
            physical=True,
        )
    with pytest.raises(ValueError):
        from_registered_tool(_registered(), physical=True)
    with pytest.raises(ValueError):
        from_registered_tool(
            _registered(), physical_adapter=_StubAdapter()
        )
    tool = FunctionTool(
        name="probe_tool",
        description="Probe tool.",
        input_schema={"type": "object"},
        execute=lambda arguments, context: "ok",
    )
    tool.physical = True
    with pytest.raises(ValueError):
        ToolRegistry().register(tool)


def test_ordinary_tools_unaffected() -> None:
    from homemaster.tools.base import FunctionTool, ToolRegistry

    tool = FunctionTool(
        name="probe_tool",
        description="Probe tool.",
        input_schema={"type": "object"},
        execute=lambda arguments, context: "ok",
    )
    assert tool.physical is False
    assert tool.physical_adapter is None
    tool.validate_identity()
    ToolRegistry().register(tool)


def test_confirm_implementations_share_structured_signature() -> None:
    import inspect

    from homemaster.cli.confirmation import CliConfirmationHandler
    from homemaster.gateway.confirmation import FeishuGatewayConfirmationHandler
    from homemaster.web.confirmations import WebConfirmationHandler

    for cls in (
        WebConfirmationHandler,
        CliConfirmationHandler,
        FeishuGatewayConfirmationHandler,
    ):
        params = list(inspect.signature(cls.confirm).parameters)
        assert params == ["self", "request", "missing_item_ids", "context"], cls


def test_only_executor_dispatches_confirm() -> None:
    import ast

    root = Path(__file__).resolve().parents[3] / "src" / "homemaster"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "confirm"
            ):
                offenders.append(str(path.relative_to(root)))
                break
    assert offenders == []
    executor = root / "tools" / "executor.py"
    assert 'getattr(self.confirmation_handler, "confirm", None)' in executor.read_text(
        encoding="utf-8"
    )
