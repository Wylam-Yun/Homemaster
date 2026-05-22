from __future__ import annotations

import subprocess
import sys


def test_cleanup_guard_report_only_runs() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py", "--report-only"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cleanup_guard_does_not_report_itself() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py", "--report-only"],
        text=True,
        capture_output=True,
    )
    assert "scripts/guard_no_legacy_terms.py" not in result.stdout
