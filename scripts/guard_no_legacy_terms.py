#!/usr/bin/env python3
"""Scan tracked files for legacy stage/pipeline/scenario terms.

In --report-only mode, prints violations and exits 0.
In enforced (default) mode, prints violations and exits 1 if any found.
Skips itself by exact relative path so its own pattern strings don't trigger.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath

BLOCKED_PATHS = (
    "src/homemaster/pipeline/",
    "src/homemaster/stages/",
    "tests/homemaster/llm_cases/",
    "tests/homemaster/prompt_snapshots/",
    "data/scenarios/",
    "var/homemaster/",
)

BLOCKED_TEXT_PATTERNS = (
    "stage_",
    "run_stage",
    "stage_statuses",
    "pipeline",
    "scenario",
    "deterministic",
    "mock_skills",
    "live_models",
    "pipeline_compat",
    "shim_lifecycle",
    "legacy shim",
    "legacy compat",
    "final_status",
    "llm_cases",
    "prompt_snapshots",
    "stage runs",
    "llm_samples.jsonl",
    "result.md",
)

SKIP_DIRS = frozenset({".git/", ".venv/", ".pytest_cache/", "plan/V1.4/"})

# Test files that legitimately reference legacy terms in negative assertions
# (asserting legacy terms are ABSENT from output/imports/fixtures).
SKIP_FILES = frozenset({
    "tests/homemaster/test_cleanup_guard.py",
    "tests/homemaster/test_cli_help.py",
    "tests/homemaster/test_cli_run.py",
    "tests/homemaster/test_cli_interactive.py",
    "tests/homemaster/test_import_boundaries.py",
    "tests/homemaster/test_domain_import_boundaries.py",
    "tests/homemaster/test_domain_memory_tools.py",
    "tests/homemaster/test_runtime_settings.py",
    "tests/homemaster/test_prompt_externalization.py",
    "tests/homemaster/test_skills_registry.py",
    "tests/homemaster/test_doubles/fake_mimo_client.py",
})

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".tar", ".gz", ".bz2",
    ".pyc", ".pyo", ".so", ".dylib", ".dll",
    ".whl", ".egg",
})

SELF_PATH = "scripts/guard_no_legacy_terms.py"


def _git_ls_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def _should_skip_path(path: str) -> bool:
    posix = PurePosixPath(path)
    for skip_dir in SKIP_DIRS:
        if path.startswith(skip_dir):
            return True
    if path == SELF_PATH:
        return True
    if path in SKIP_FILES:
        return True
    suffix = posix.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return True
    return False


def _is_blocked_path(path: str) -> bool:
    return any(path.startswith(bp) for bp in BLOCKED_PATHS)


def _has_blocked_text(path: str) -> list[str]:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    hits = []
    for pattern in BLOCKED_TEXT_PATTERNS:
        if pattern in text:
            hits.append(pattern)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print violations but always exit 0",
    )
    args = parser.parse_args()

    files = _git_ls_files()
    violations: list[str] = []

    for path in files:
        if _should_skip_path(path):
            continue

        if _is_blocked_path(path):
            violations.append(f"BLOCKED PATH: {path}")
            continue

        hits = _has_blocked_text(path)
        if hits:
            violations.append(f"BLOCKED TEXT {hits}: {path}")

    for v in violations:
        print(v)

    if args.report_only:
        return 0
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
