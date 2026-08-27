from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRY_PATHS = (
    "src/homemaster/agent/turn.py",
    "src/homemaster/cli/interactive_shell.py",
    "src/homemaster/benchmarking/alfworld/runner.py",
)
FORBIDDEN_IMPORTS = {
    "homemaster.agent.generic_runtime",
    "homemaster.benchmarking.alfworld.registry",
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
    "build_home_tool_registry",
}

REMOVED_DEAD_PATHS = (
    "src/homemaster/benchmarking/alfworld/registry.py",
    "src/homemaster/benchmarking/alfworld/runtime_contract.py",
    "src/homemaster/benchmarking/browser_demo/trajectory.py",
    "src/homemaster/benchmarking/browser_demo/__init__.py",
    "src/homemaster/channels/impl/telegram.py",
    "src/homemaster/domain/grounding.py",
    "src/homemaster/domain/contracts.py",
    "src/homemaster/domain/tool_registry.py",
    "src/homemaster/memory/bm25_preflight.py",
    "src/homemaster/memory/index.py",
    "src/homemaster/memory/outbound_policy.py",
    "src/homemaster/memory/retrieval.py",
    "src/homemaster/memory/tokenizer.py",
    "src/homemaster/prompts/memory_query_prompt.md",
    "src/homemaster/prompts/memory_query_retry.md",
    "src/homemaster/prompts/task_interpreter_prompt.md",
    "src/homemaster/prompts/task_summary_prompt.md",
    "src/homemaster/tools/dispatcher.py",
    "src/homemaster/tools/registry.py",
)


def test_all_entries_have_no_legacy_model_runtime_assembly() -> None:
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
        "build_home_tool_registry",
    } & (imported | called)


def test_agent_has_no_private_run_context_or_runtime_tool_spec() -> None:
    runtime = _tree("src/homemaster/agent/generic_runtime.py")

    assert not any(
        isinstance(node, ast.ClassDef) and node.name == "ToolSpec"
        for node in ast.walk(runtime)
    )
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "_run_context"
        for node in ast.walk(runtime)
    )


@pytest.mark.parametrize("relative", REMOVED_DEAD_PATHS)
def test_migrated_dead_path_is_absent_from_distribution_source(relative: str) -> None:
    assert not (REPO_ROOT / relative).exists(), relative


def test_removed_execution_modules_are_absent_from_production() -> None:
    removed_modules = {
        "homemaster.benchmarking.alfworld.registry",
        "homemaster.benchmarking.alfworld.runtime_contract",
        "homemaster.benchmarking.browser_demo.trajectory",
        "homemaster.benchmarking.browser_demo",
        "homemaster.channels.impl.telegram",
        "homemaster.domain.grounding",
        "homemaster.domain.contracts",
        "homemaster.domain.tool_registry",
        "homemaster.memory.bm25_preflight",
        "homemaster.memory.index",
        "homemaster.memory.outbound_policy",
        "homemaster.memory.retrieval",
        "homemaster.memory.tokenizer",
        "homemaster.tools.catalog",
        "homemaster.tools.dispatcher",
        "homemaster.tools.pipeline",
        "homemaster.tools.registry",
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

    assert imported_modules == []


def _tree(relative: str) -> ast.Module:
    return ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
