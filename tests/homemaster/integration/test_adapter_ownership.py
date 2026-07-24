from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRY_PATHS = (
    "src/homemaster/agent/turn.py",
    "src/homemaster/cli/interactive_shell.py",
    "src/homemaster/benchmarking/alfworld/runner.py",
    "src/homemaster/benchmarking/coworker_demo/turn.py",
)
FORBIDDEN_IMPORTS = {
    "homemaster.agent.generic_runtime",
    "homemaster.benchmarking.alfworld.registry",
    "homemaster.benchmarking.coworker_demo.registry",
    "homemaster.domain.tool_registry",
    "homemaster.providers.llm_client",
    "homemaster.tools.dispatcher",
    "homemaster.tools.registry",
}
FORBIDDEN_CONSTRUCTORS = {
    "AgentRuntime",
    "GenericAgentRuntime",
    "LLMClient",
    "ToolDispatcher",
    "ToolRegistry",
    "build_alfworld_tool_registry",
    "build_coworker_tool_registry",
    "build_home_tool_registry",
}


def test_all_four_entries_have_no_legacy_model_runtime_assembly() -> None:
    for relative in ENTRY_PATHS:
        tree = _tree(relative)
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not imported_modules & FORBIDDEN_IMPORTS, relative
        assert not called_names & FORBIDDEN_CONSTRUCTORS, relative


def test_profiles_use_only_the_universal_registry_builder() -> None:
    tree = _tree("src/homemaster/adapters/profiles.py")
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not {
        "build_alfworld_tool_registry",
        "build_coworker_tool_registry",
        "build_home_tool_registry",
    } & (imported | called)


def test_agent_and_dispatcher_have_no_private_run_context_or_runtime_tool_spec() -> None:
    runtime = _tree("src/homemaster/agent/generic_runtime.py")
    dispatcher = _tree("src/homemaster/tools/dispatcher.py")

    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ToolSpec"
        for node in ast.walk(runtime)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "_run_context"
        for node in ast.walk(runtime)
    )
    assert not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "set_run_context"
        for node in ast.walk(dispatcher)
    )


def test_removed_execution_modules_are_absent_from_production() -> None:
    removed_modules = {
        "homemaster.tools.catalog",
        "homemaster.tools.pipeline",
    }
    imported_modules = []
    for path in (REPO_ROOT / "src/homemaster").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in removed_modules:
                imported_modules.append((path.relative_to(REPO_ROOT).as_posix(), node.module))
            if isinstance(node, ast.Import):
                imported_modules.extend(
                    (path.relative_to(REPO_ROOT).as_posix(), alias.name)
                    for alias in node.names
                    if alias.name in removed_modules
                )

    assert not (REPO_ROOT / "src/homemaster/tools/catalog.py").exists()
    assert not (REPO_ROOT / "src/homemaster/tools/pipeline.py").exists()
    assert imported_modules == []


def _tree(relative: str) -> ast.Module:
    return ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
