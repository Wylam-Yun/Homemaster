from __future__ import annotations

import subprocess
import sys


def test_no_legacy_terms_remain_in_tracked_product_files() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/guard_no_legacy_terms.py"],
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
