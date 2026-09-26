"""Phase 0 architecture boundary checks for the V3.5 migration."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "homemaster"
WORKER_ROOT = REPO_ROOT / "workers" / "alfworld_worker"


def _python_sources(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def test_non_cli_modules_do_not_import_cli_composition() -> None:
    forbidden = "homemaster.cli.composition"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in _python_sources(SRC_ROOT)
        if path.relative_to(SRC_ROOT).parts[:2] != ("cli", "composition.py")
        and forbidden in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"public composition is still imported by: {offenders}"


def test_application_and_agent_runtime_are_domain_agnostic() -> None:
    runtime_files = (
        SRC_ROOT / "application" / "runtime.py",
        SRC_ROOT / "agent" / "generic_runtime.py",
    )
    forbidden_terms = ("homemaster.alfworld", "AlfredThorEnv", "objectId", "scorer")
    offenders = {
        str(path.relative_to(REPO_ROOT)): [
            term for term in forbidden_terms if term in path.read_text(encoding="utf-8")
        ]
        for path in runtime_files
        if any(term in path.read_text(encoding="utf-8") for term in forbidden_terms)
    }
    assert offenders == {}, f"domain terms leaked into generic runtime: {offenders}"


def test_worker_source_exists_and_has_no_main_environment_imports() -> None:
    assert WORKER_ROOT.is_dir(), "the isolated ALFWorld worker source tree is missing"
    forbidden_modules = {"homemaster", "mindmemos"}
    offenders: list[str] = []
    for path in _python_sources(WORKER_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module.split(".", 1)[0]} if node.module else set()
            else:
                continue
            if names & forbidden_modules:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"worker imports main-environment packages: {offenders}"
