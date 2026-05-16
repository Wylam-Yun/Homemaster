"""Non-live Stage 07 scenario structure and RAG gate tests.

Verifies catalog structure, memory profile materialization, world overlay
validity, and validator compliance. Does NOT run the live scenario matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.memory_profile import materialize_memory
from homemaster.runtime import REPO_ROOT
from homemaster.scenario_catalog import (
    baseline_scenario_names,
    legacy_compat_names,
    load_catalog,
    load_memory_profile,
)
from homemaster.scenario_validator import validate_all, validate_world_overlay
from homemaster.world_overlay import apply_world_overlay


def _baseline_names() -> list[str]:
    return baseline_scenario_names()


def _legacy_names() -> list[str]:
    return legacy_compat_names()


# ── Baseline Structure ───────────────────────────────────────────────────────


class TestBaselineStructure:
    def test_baseline_scenarios_exist_in_catalog(self) -> None:
        names = _baseline_names()
        assert len(names) >= 7, f"Expected >=7 baseline scenarios, got {len(names)}: {names}"

    def test_legacy_compat_scenarios_exist(self) -> None:
        names = _legacy_names()
        assert len(names) == 5, f"Expected 5 legacy_compat, got {len(names)}: {names}"

    def test_baseline_scenario_directories_exist(self) -> None:
        scenarios_root = REPO_ROOT / "data" / "scenarios"
        for name in _baseline_names():
            assert (scenarios_root / name).is_dir(), f"Missing directory: {name}"

    @pytest.mark.parametrize(
        "fname",
        ["scenario.json", "world_overlay.json", "memory_profile.json", "failures.json"],
    )
    def test_baseline_scenario_has_required_files(self, fname: str) -> None:
        scenarios_root = REPO_ROOT / "data" / "scenarios"
        for name in _baseline_names():
            assert (scenarios_root / name / fname).is_file(), f"{name} missing {fname}"

    def test_baseline_uses_homeworld_profile(self) -> None:
        catalog = load_catalog()
        for entry in catalog:
            if "llm_baseline" in entry.suites or "corpus_profile_smoke" in entry.suites:
                assert entry.data_source == "homeworld_profile", (
                    f"{entry.name} should use homeworld_profile, got {entry.data_source}"
                )


# ── Corpus/Profile RAG Gate ─────────────────────────────────────────────────


class TestCorpusProfileGate:
    def test_full_corpus_scenario_exists(self) -> None:
        catalog = load_catalog()
        smoke = [e.name for e in catalog if "corpus_profile_smoke" in e.suites]
        assert len(smoke) >= 1, "Need at least one corpus_profile_smoke scenario"

    def test_full_corpus_materializes_all_entries(self) -> None:
        corpus_path = (
            REPO_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"
        )
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        total = len(corpus.get("object_memory", []))
        for name in [e.name for e in load_catalog() if "corpus_profile_smoke" in e.suites]:
            profile = load_memory_profile(name)
            assert profile is not None
            assert profile.full_corpus is True
            result = materialize_memory(corpus, profile)
            assert len(result["object_memory"]) == total

    def test_include_memory_ids_scenario_materializes(self) -> None:
        corpus_path = (
            REPO_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"
        )
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        for entry in load_catalog():
            if "llm_baseline" not in entry.suites:
                continue
            profile = load_memory_profile(entry.name)
            if profile and not profile.full_corpus and profile.include_memory_ids:
                result = materialize_memory(corpus, profile)
                assert len(result["object_memory"]) == len(profile.include_memory_ids)

    def test_multi_memory_scenario_has_multiple_candidates(self) -> None:
        catalog = load_catalog()
        multi = [e for e in catalog if "multi_memory" in e.tags or "memory_conflict" in e.tags]
        assert len(multi) >= 1, "Need at least one multi-candidate scenario"
        for entry in multi:
            profile = load_memory_profile(entry.name)
            assert profile is not None
            assert len(profile.include_memory_ids) >= 2, (
                f"{entry.name} should have >=2 memory candidates"
            )


# ── World Overlay Validity ──────────────────────────────────────────────────


class TestWorldOverlayValidity:
    def test_all_overlays_validate(self) -> None:
        world_path = REPO_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"
        home_world = json.loads(world_path.read_text(encoding="utf-8"))
        scenarios_root = REPO_ROOT / "data" / "scenarios"
        for name in _baseline_names():
            overlay_path = scenarios_root / name / "world_overlay.json"
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            issues = validate_world_overlay(name, overlay, home_world)
            assert issues == [], f"{name} overlay issues: {issues}"

    def test_overlay_application_produces_valid_world(self) -> None:
        world_path = REPO_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"
        home_world = json.loads(world_path.read_text(encoding="utf-8"))
        scenarios_root = REPO_ROOT / "data" / "scenarios"
        for name in _baseline_names():
            overlay_path = scenarios_root / name / "world_overlay.json"
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            result = apply_world_overlay(home_world, overlay)
            assert "rooms" in result
            assert "objects" in result
            assert "viewpoints" in result


# ── Validator Compliance ────────────────────────────────────────────────────


class TestValidatorPasses:
    def test_validate_all_no_active_issues(self) -> None:
        result = validate_all(include_draft=False)
        assert result.issues == [], (
            f"Active scenario issues: {[(i.scope, i.error_type, i.message) for i in result.issues]}"
        )
