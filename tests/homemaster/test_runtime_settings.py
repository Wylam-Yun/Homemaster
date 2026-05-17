"""Tests for RuntimeSettings — explicit construction, no import-time config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.config.runtime_settings import (
    RuntimeSettings,
    RuntimeSettingsError,
    load_runtime_settings,
)


def test_construction_with_defaults() -> None:
    settings = RuntimeSettings(
        run_id="test-1",
        runtime_root=Path("/tmp/runs"),
        debug_root=Path("/tmp/debug"),
        results_root=Path("/tmp/results"),
    )
    assert settings.provider_name == "Mimo"
    assert settings.embedding_provider_name == "MemoryEmbedding"
    assert settings.skill_mode == "simulated"
    assert settings.max_turns == 12


def test_construction_with_overrides() -> None:
    settings = RuntimeSettings(
        run_id="test-2",
        runtime_root=Path("/tmp/runs"),
        debug_root=Path("/tmp/debug"),
        results_root=Path("/tmp/results"),
        provider_name="Custom",
        skill_mode="real",
        max_turns=20,
    )
    assert settings.provider_name == "Custom"
    assert settings.skill_mode == "real"
    assert settings.max_turns == 20


def test_two_instances_independent() -> None:
    s1 = RuntimeSettings(
        run_id="a",
        runtime_root=Path("/tmp/a"),
        debug_root=Path("/tmp/a"),
        results_root=Path("/tmp/a"),
        provider_name="P1",
    )
    s2 = RuntimeSettings(
        run_id="b",
        runtime_root=Path("/tmp/b"),
        debug_root=Path("/tmp/b"),
        results_root=Path("/tmp/b"),
        provider_name="P2",
    )
    assert s1.provider_name == "P1"
    assert s2.provider_name == "P2"


# ---------------------------------------------------------------------------
# Loader validation tests
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, data: dict) -> Path:
    cfg = {"runtime_defaults": data}
    path = tmp_path / "homemaster.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_loader_rejects_live_models(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {"live_models": True})
    with pytest.raises(RuntimeSettingsError, match="live_models.*no longer supported"):
        load_runtime_settings(cfg_path)


def test_loader_rejects_mock_skills(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {"mock_skills": True})
    with pytest.raises(RuntimeSettingsError, match="mock_skills.*no longer supported"):
        load_runtime_settings(cfg_path)


def test_loader_rejects_skill_mode_real(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {"skill_mode": "real"})
    with pytest.raises(RuntimeSettingsError, match="skill_mode='real'.*not yet supported"):
        load_runtime_settings(cfg_path)


def test_loader_rejects_skill_mode_real_via_override(tmp_path: Path) -> None:
    with pytest.raises(RuntimeSettingsError, match="skill_mode='real'.*not yet supported"):
        load_runtime_settings(None, skill_mode="real")


def test_loader_accepts_skill_mode_simulated(tmp_path: Path) -> None:
    cfg_path = _write_config(tmp_path, {"skill_mode": "simulated"})
    settings = load_runtime_settings(
        cfg_path,
        run_id="test",
        runtime_root=Path("/tmp/runs"),
        debug_root=Path("/tmp/debug"),
        results_root=Path("/tmp/results"),
    )
    assert settings.skill_mode == "simulated"


def test_loader_no_config_uses_defaults() -> None:
    settings = load_runtime_settings(
        None,
        run_id="test",
        runtime_root=Path("/tmp/runs"),
        debug_root=Path("/tmp/debug"),
        results_root=Path("/tmp/results"),
    )
    assert settings.skill_mode == "simulated"
    assert settings.provider_name == "Mimo"
