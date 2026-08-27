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

SKIP_DIRS = frozenset(
    {
        ".git/",
        ".homemaster/",
        ".pytest_cache/",
        ".venv/",
        "docs/superpowers/plans/",
        "docs/superpowers/specs/",
        "plan/",
        # Vendored third-party code and archived historical reports are not product copy.
        "src/homemaster/browser/vendor/",
        "story/",
        "src/homemaster/benchmarking/browser_demo/",
        "tests/homemaster/benchmarking/browser_demo/",
        "third_party/",
    }
)

# Test files that legitimately reference legacy terms in negative assertions
# (asserting legacy terms are ABSENT from output/imports/fixtures).
SKIP_FILES = frozenset(
    {
        "CHANGELOG.md",
        "README.md",
        "docs/architecture/memory-system.md",
        "docs/memory-user-guide.md",
        "docs/mindmemos-native-pipeline-integration-report-zh.md",
        "docs/skills-and-config-user-guide.md",
        # Historical coworker records archived from repo root on 2026-08-27.
        "docs/reports/2026-08-27-coworker-demo-findings.md",
        "docs/reports/2026-08-27-coworker-delivery-task-plan.md",
        "progress.md",
        "src/homemaster/application/factory.py",
        "src/homemaster/permissions/policy.py",
        "src/homemaster/memory/mindmemos_runtime.py",
        "src/homemaster/tools/memory_tools.py",
        "src/homemaster/application/runtime.py",
        "src/homemaster/tools/legacy_adapter.py",
        "src/homemaster/tools/pipeline.py",
        "docs/reports/2026-08-27-coworker-delivery-task-plan.md",
        "tests/homemaster/permissions/test_policy.py",
        "tests/homemaster/application/test_application_runtime.py",
        "tests/homemaster/application/test_factory.py",
        "tests/homemaster/application/test_runtime_stress.py",
        "tests/homemaster/devices/test_pipeline_integration.py",
        "tests/homemaster/integration/test_adapter_ownership.py",
        "tests/homemaster/memory/test_automatic_recall_integration.py",
        "tests/homemaster/memory/test_memory_tools.py",
        "tests/homemaster/memory/test_mindmemos_runtime.py",
        "tests/homemaster/skills/test_installed_package.py",
        "tests/homemaster/benchmarking/test_alfworld_execution.py",
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
        "tests/homemaster/test_third_party_logging.py",
        "tests/homemaster/tools/test_execution_pipeline.py",
        "tests/homemaster/tools/test_retry_policy.py",
        "tests/homemaster/tools/test_validation.py",
    }
)

BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".pyc",
        ".pyo",
        ".so",
        ".dylib",
        ".dll",
        ".whl",
        ".egg",
    }
)

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
