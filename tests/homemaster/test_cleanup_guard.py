from __future__ import annotations

import subprocess
import sys

import pytest

from scripts.guard_no_legacy_terms import _should_skip_path


@pytest.mark.parametrize(
    "path",
    [
        "findings.md",
        "progress.md",
        "task_plan.md",
        "plan/change-coworker-demo-design.md",
        "apps/case02_openenv/src/case02_openenv/models.py",
        "config/coworker_demo.example.yaml",
        "data/coworker_demo/case_02/scenarios/normal.yaml",
        "docs/architecture/coworker-demo.md",
        "docs/coworker-demo-user-guide.md",
        "scripts/coworker_demo/preflight.py",
        "src/homemaster/benchmarking/coworker_demo/types.py",
        "src/homemaster/cli/coworker_router.py",
        "tests/case02_openenv/test_episode_store.py",
        "tests/homemaster/benchmarking/coworker_demo/test_turn.py",
        "tests/homemaster/benchmarking/test_alfworld_execution.py",
        "tests/homemaster/test_coworker_router.py",
    ],
)
def test_guard_skips_non_product_state_and_domain_specific_vocabulary(path: str) -> None:
    assert _should_skip_path(path)


def test_guard_still_scans_default_homemaster_product_files() -> None:
    assert not _should_skip_path("src/homemaster/agent/generic_runtime.py")


def test_no_legacy_terms_remain_in_tracked_product_files() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
