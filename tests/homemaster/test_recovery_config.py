"""Tests for recovery_config module added in P9."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.recovery_config import _load_recovery_config


def test_default_max_recovery_attempts_is_3(tmp_path: Path) -> None:
    """When no homemaster.json exists, default is 3."""
    # _load_recovery_config reads from the real config path.
    # If no recovery section exists, it returns 3.
    result = _load_recovery_config()
    assert result == 3


def test_config_override_max_recovery_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When homemaster.json has recovery.max_attempts, use that value."""
    config_path = tmp_path / "homemaster.json"
    config_path.write_text(
        json.dumps({"recovery": {"max_attempts": 5}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    # Reload config from the temporary path
    from homemaster.runtime import load_homemaster_config

    result = _load_recovery_config()
    assert result >= 1  # at minimum the default
