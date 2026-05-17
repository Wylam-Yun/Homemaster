"""Import boundary tests for HomeMaster.

Verifies:
- No task_brain imports in src/homemaster
- New packages (config/, agent/, tools/, skills/, providers/) don't have import-time config reads
- RuntimeSettings can be constructed without a config file
- Root shims don't use import *
- src/homemaster doesn't import from tests/
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

HOMEMASTER_ROOT = Path(__file__).resolve().parents[2] / "src" / "homemaster"
_REPO_ROOT = str(Path(__file__).resolve().parents[2] / "src")


def _run_import_in_subprocess(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**__import__("os").environ, "PYTHONPATH": _REPO_ROOT},
    )


# ---------------------------------------------------------------------------
# Legacy: no task_brain imports
# ---------------------------------------------------------------------------


def test_homemaster_stage_01_does_not_import_task_brain() -> None:
    offenders: list[str] = []

    for path in sorted(HOMEMASTER_ROOT.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "task_brain" or alias.name.startswith("task_brain."):
                        offenders.append(f"{path}:{alias.name}")
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "task_brain" or module.startswith("task_brain."):
                    offenders.append(f"{path}:{module}")

    assert offenders == []


# ---------------------------------------------------------------------------
# Phase 2: new packages must not have import-time config reads
# ---------------------------------------------------------------------------


_NEW_PACKAGES = [
    "config",
    "agent",
    "tools",
    "skills",
    "events",
    "providers",
]


def test_new_packages_have_no_import_time_config_reads() -> None:
    """New Phase 2 packages must not call load_*_config() at module level."""
    import re
    pattern = re.compile(r"^[A-Za-z_].*=\s*(?:load_\w+|_load_\w+)\(", re.MULTILINE)
    offenders: list[str] = []

    for pkg in _NEW_PACKAGES:
        pkg_dir = HOMEMASTER_ROOT / pkg
        if not pkg_dir.exists():
            continue
        for path in sorted(pkg_dir.glob("**/*.py")):
            source = path.read_text(encoding="utf-8")
            for match in pattern.finditer(source):
                line = match.group().strip()
                if line.startswith("def "):
                    continue
                offenders.append(f"{pkg}/{path.name}: {line}")

    if offenders:
        pytest.fail(
            "New packages have import-time config reads:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )


def test_runtime_settings_constructable_without_config() -> None:
    """RuntimeSettings can be constructed in a fresh process without config file."""
    script = '''
import sys
from homemaster.config.runtime_settings import RuntimeSettings

# Construct with minimal required fields — no config file needed
settings = RuntimeSettings(
    run_id="test-123",
    runtime_root="/tmp/runs",
    debug_root="/tmp/debug",
    results_root="/tmp/results",
)
assert settings.provider_name == "Mimo"
assert settings.skill_mode == "simulated"
assert settings.max_turns == 12
print("OK")
'''
    result = _run_import_in_subprocess(script)
    assert result.returncode == 0, (
        f"RuntimeSettings construction failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Root shims: no import *
# ---------------------------------------------------------------------------


def test_phase2_files_no_star_import() -> None:
    """Phase 2 files (new packages + modified shims) must not use `import *`."""
    # Check new packages
    offenders: list[str] = []
    for pkg in _NEW_PACKAGES:
        pkg_dir = HOMEMASTER_ROOT / pkg
        if not pkg_dir.exists():
            continue
        for path in sorted(pkg_dir.glob("**/*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.names:
                    for alias in node.names:
                        if alias.name == "*":
                            offenders.append(f"{pkg}/{path.name}:{node.lineno}")

    # Check Phase 1 cleaned shims (stage_runtime.py, executor.py)
    for shim in ("stage_runtime.py", "executor.py"):
        path = HOMEMASTER_ROOT / shim
        if path.exists():
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.names:
                    for alias in node.names:
                        if alias.name == "*":
                            offenders.append(f"{shim}:{node.lineno}")

    if offenders:
        pytest.fail(
            "Phase 2 files use `import *`:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )


# ---------------------------------------------------------------------------
# Regression: existing modules have known import-time config reads
# (intentionally preserved for subprocess+reload test pattern).
# This test catches any NEW import-time config reads that sneak in.
# ---------------------------------------------------------------------------

_KNOWN_IMPORT_TIME_CONFIG_MODULES = {
    "runtime.py",       # _defaults_cfg, _paths_cfg
    "token_budget.py",  # MAX_LLM_ATTEMPTS, INITIAL_MAX_TOKENS
    "memory_rag.py",    # _scoring = _load_scoring_config()
    "grounding.py",     # ROOM_HINTS, ANCHOR_HINTS, SPECIFIC_ANCHOR_WORDS
    "recovery_config.py",
    "stages/executor.py",
    "llm_client.py",
    "embedding_client.py",
}


def test_no_new_import_time_config_reads_in_existing_modules() -> None:
    """Catch new import-time config reads in existing modules.

    Known modules are exempt (intentionally preserved for backward compat).
    New modules must use explicit loaders.
    """
    import re
    pattern = re.compile(r"^[A-Za-z_].*=\s*(?:load_\w+|_load_\w+)\(", re.MULTILINE)
    offenders: list[str] = []

    for path in sorted(HOMEMASTER_ROOT.glob("**/*.py")):
        rel = str(path.relative_to(HOMEMASTER_ROOT))
        if rel in _KNOWN_IMPORT_TIME_CONFIG_MODULES:
            continue
        # Skip new packages (already tested above)
        if any(rel.startswith(pkg + "/") for pkg in _NEW_PACKAGES):
            continue
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            line = match.group().strip()
            if line.startswith("def "):
                continue
            offenders.append(f"{rel}: {line}")

    if offenders:
        pytest.fail(
            "New import-time config reads found:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )


# ---------------------------------------------------------------------------
# src/homemaster must not import from tests/
# ---------------------------------------------------------------------------


def test_no_test_imports_in_src() -> None:
    """src/homemaster must not import from tests or test_doubles."""
    offenders: list[str] = []

    for path in sorted(HOMEMASTER_ROOT.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("tests") or "test_doubles" in module:
                    offenders.append(f"{path.name}:{module}")

    if offenders:
        pytest.fail(
            "src/homemaster imports from tests:\n"
            + "\n".join(f"  {o}" for o in offenders)
        )
