from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

from homemaster.benchmarking.alfworld.env_adapter import AlfworldEnvAdapter
from homemaster.benchmarking.alfworld.execution import (
    OracleManipulationExecutor,
    OracleNavigationExecutor,
)
from homemaster.benchmarking.alfworld.registry import build_alfworld_tool_registry
from homemaster.benchmarking.alfworld.types import AlfworldStepResult

ROOT = Path(__file__).resolve().parents[3]


def test_every_step_result_constructor_supplies_typed_feedback() -> None:
    paths = [ROOT / "src", ROOT / "tests"]
    calls: list[tuple[Path, ast.Call]] = []
    for root in paths:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and _call_name(node.func) == "AlfworldStepResult":
                    calls.append((path, node))
    assert calls
    missing = [
        f"{path.relative_to(ROOT)}:{call.lineno}"
        for path, call in calls
        if "execution_feedback" not in {item.arg for item in call.keywords}
    ]
    assert missing == []


def test_step_failure_reason_is_read_only_projection() -> None:
    signature = inspect.signature(AlfworldStepResult)

    assert "execution_feedback" in signature.parameters
    assert "failure_reason" not in signature.parameters
    assert isinstance(AlfworldStepResult.failure_reason, property)


def test_alfworld_registry_has_one_navigation_tool_and_no_legacy_bypasses() -> None:
    names = set(build_alfworld_tool_registry().all_names())

    assert "robot_go_to" in names
    assert "robot_navigate" not in names
    assert "robot_find_object" not in names


def test_formal_executors_do_not_reach_candidate_or_text_navigation_helpers() -> None:
    source = "\n".join(
        (
            textwrap.dedent(inspect.getsource(AlfworldEnvAdapter._go_to_target_v18)),
            textwrap.dedent(inspect.getsource(AlfworldEnvAdapter._manipulate_with_thor_v18)),
            textwrap.dedent(inspect.getsource(OracleNavigationExecutor)),
            textwrap.dedent(inspect.getsource(OracleManipulationExecutor)),
        )
    )
    forbidden_names = {
        "_teleport_candidates",
        "_single_target_teleport_candidates",
        "_navigation_budget_stop",
        "virtual_navigate",
        "find_object",
        "ManipulationExecutor",
        "_thor_step",
    }
    call_names = {
        _call_name(node.func)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
    }

    assert forbidden_names.isdisjoint(call_names)


def test_product_source_does_not_import_gate_evidence_helpers() -> None:
    product = ROOT / "src" / "homemaster"
    offenders = []
    for path in product.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "GateCase" in source or "alfworld-evidence" in source or "run_gate_a" in source:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_alfworld_runner_does_not_assemble_model_facing_runtime_components() -> None:
    path = ROOT / "src/homemaster/benchmarking/alfworld/runner.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden = {
        "AgentRuntime",
        "GenericAgentRuntime",
        "LLMClient",
        "ToolDispatcher",
        "ToolRegistry",
        "build_alfworld_tool_registry",
    }
    imported_or_called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_or_called.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_or_called.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name is not None:
                imported_or_called.add(name)

    assert forbidden.isdisjoint(imported_or_called)


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None
