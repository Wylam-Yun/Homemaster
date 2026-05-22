"""Tests verifying domain import boundaries.

Domain tools may import homemaster.tools.*, homemaster.memory.*, and
homemaster.domain.home.*. They must NOT import homemaster.pipeline,
homemaster.stages, homemaster.task_runner, or homemaster.agent.runtime.
"""

from __future__ import annotations

import subprocess


def _rg(pattern: str, path: str) -> list[str]:
    """Run rg and return matching lines."""
    result = subprocess.run(
        ["rg", "-n", pattern, path, "-g", "*.py"],
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.strip().splitlines() if line]


def test_domain_home_tools_do_not_import_pipeline() -> None:
    matches = _rg(
        "homemaster\\.(pipeline|stages|task_runner)",
        "src/homemaster/domain/home/",
    )
    assert not matches, f"domain/home/ imports old runtime: {matches}"


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
    assert not matches, f"skills/ imports old runtime: {matches}"


def test_memory_modules_do_not_import_pipeline() -> None:
    matches = _rg(
        "homemaster\\.(pipeline|stages|task_runner)",
        "src/homemaster/memory/",
    )
    assert not matches, f"memory/ imports old runtime: {matches}"


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
