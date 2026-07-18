from __future__ import annotations

import subprocess
import sys

from scripts.guard_no_legacy_terms import _has_blocked_text


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
