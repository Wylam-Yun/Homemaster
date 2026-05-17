"""Tests for RuntimeSettings — explicit construction, no import-time config."""

from __future__ import annotations

from pathlib import Path

from homemaster.config.runtime_settings import RuntimeSettings


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
