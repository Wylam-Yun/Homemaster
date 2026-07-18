from __future__ import annotations

import subprocess
import sys

import pytest

from scripts.guard_no_legacy_terms import _has_blocked_text, _should_skip_path


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
        "docs/superpowers/plans/2026-07-17-coworker-executive-demo.md",
        "docs/superpowers/specs/2026-07-17-coworker-executive-demo-design.md",
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


@pytest.mark.parametrize(
    "path",
    [
        "docs/product/runtime.md",
        "docs/superpowers/runtime.md",
        "src/homemaster/agent/generic_runtime.py",
    ],
)
def test_guard_still_scans_product_files_outside_skip_directories(path: str) -> None:
    assert not _should_skip_path(path)


def test_no_legacy_terms_remain_in_tracked_product_files() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cleanup_guard_allows_ordinary_deterministic_language(tmp_path, monkeypatch) -> None:
    source = tmp_path / "ordinary.py"
    source.write_text(
        "def deterministic_order(values):\n    return sorted(values)\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    assert _has_blocked_text("ordinary.py") == []
