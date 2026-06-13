"""Tests verifying domain import boundaries."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path


def _rg(pattern: str, path: str) -> list[str]:
    """Return matching Python lines, using rg when available."""
    if shutil.which("rg") is not None:
        result = subprocess.run(
            ["rg", "-n", pattern, path, "-g", "*.py"],
            capture_output=True,
            text=True,
        )
        return [line for line in result.stdout.strip().splitlines() if line]

    regex = re.compile(pattern)
    matches: list[str] = []
    for file_path in Path(path).rglob("*.py"):
        for lineno, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), 1):
            if regex.search(line):
                matches.append(f"{file_path}:{lineno}:{line}")
    return matches


def test_domain_home_tools_do_not_import_pipeline() -> None:
    matches = _rg(
        "homemaster\\.(pipeline|stages|task_runner)",
        "src/homemaster/domain/home/",
    )
    assert not matches, f"domain/home/ imports deleted runtime packages: {matches}"


def test_domain_home_tools_do_not_import_agent_runtime() -> None:
    matches = _rg(
        "from homemaster\\.agent\\.runtime import",
        "src/homemaster/domain/home/",
    )
    assert not matches, f"domain/home/ imports agent.runtime: {matches}"


def test_skills_do_not_import_pipeline() -> None:
    matches = _rg(
        "homemaster\\.(pipeline|stages|task_runner)",
        "src/homemaster/skills/",
    )
    assert not matches, f"skills/ imports deleted runtime packages: {matches}"


def test_memory_modules_do_not_import_pipeline() -> None:
    matches = _rg(
        "homemaster\\.(pipeline|stages|task_runner)",
        "src/homemaster/memory/",
    )
    assert not matches, f"memory/ imports deleted runtime packages: {matches}"


def test_no_old_contracts_imports_in_domain() -> None:
    """domain/home/ must use relative imports, not homemaster.contracts."""
    matches = _rg(
        "from homemaster\\.contracts import",
        "src/homemaster/domain/home/",
    )
    assert not matches, f"domain/home/ uses old contracts path: {matches}"


def test_no_old_contracts_imports_in_memory() -> None:
    """memory/ must use homemaster.domain.home.contracts, not homemaster.contracts."""
    matches = _rg(
        "from homemaster\\.contracts import",
        "src/homemaster/memory/",
    )
    assert not matches, f"memory/ uses old contracts path: {matches}"
