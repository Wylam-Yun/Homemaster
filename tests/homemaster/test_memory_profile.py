"""Validate memory profile materializer, contracts, and catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.contracts import (
    MemoryProfile,
    ScenarioCatalogEntry,
    ScenarioManifest,
)
from homemaster.memory_profile import materialize_memory
from homemaster.runtime import REPO_ROOT
from homemaster.scenario_catalog import (
    load_catalog,
    load_scenario_manifest,
    active_scenario_names,
)

CORPUS_PATH = REPO_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"
CATALOG_PATH = REPO_ROOT / "data" / "scenarios" / "catalog.json"
SCENARIOS_ROOT = REPO_ROOT / "data" / "scenarios"

BASELINE_SCENARIOS = [
    "check_medicine_success",
    "check_medicine_stale_recover",
    "fetch_cup_retry",
    "object_not_found",
    "distractor_rejected",
]


@pytest.fixture(scope="module")
def corpus() -> dict:
    assert CORPUS_PATH.is_file(), f"Corpus not found: {CORPUS_PATH}"
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def corpus_memory_ids(corpus) -> set[str]:
    return {e["memory_id"] for e in corpus.get("object_memory", [])}


# --- materialize_memory ---


def test_materialize_full_corpus(corpus: dict):
    profile = MemoryProfile(full_corpus=True)
    result = materialize_memory(corpus, profile)
    assert len(result["object_memory"]) == 35


def test_materialize_include_ids(corpus: dict):
    profile = MemoryProfile(include_memory_ids=["mem-cup-1", "mem-medicine-1"])
    result = materialize_memory(corpus, profile)
    ids = {e["memory_id"] for e in result["object_memory"]}
    assert ids == {"mem-cup-1", "mem-medicine-1"}


def test_materialize_exclude_ids(corpus: dict):
    profile = MemoryProfile(full_corpus=True, exclude_memory_ids=["mem-cup-1"])
    result = materialize_memory(corpus, profile)
    ids = {e["memory_id"] for e in result["object_memory"]}
    assert "mem-cup-1" not in ids
    assert len(result["object_memory"]) == 34


def test_materialize_include_then_exclude(corpus: dict):
    profile = MemoryProfile(
        include_memory_ids=["mem-cup-1", "mem-cup-2", "mem-cup-3"],
        exclude_memory_ids=["mem-cup-2"],
    )
    result = materialize_memory(corpus, profile)
    ids = {e["memory_id"] for e in result["object_memory"]}
    assert ids == {"mem-cup-1", "mem-cup-3"}


def test_materialize_unknown_include_ids_fail_fast(corpus: dict):
    profile = MemoryProfile(include_memory_ids=["mem-cup-1", "nonexistent-id"])
    with pytest.raises(ValueError, match="include_memory_ids"):
        materialize_memory(corpus, profile)


def test_materialize_unknown_exclude_ids_fail_fast(corpus: dict):
    profile = MemoryProfile(full_corpus=True, exclude_memory_ids=["nonexistent-id"])
    with pytest.raises(ValueError, match="exclude_memory_ids"):
        materialize_memory(corpus, profile)


def test_materialize_empty_profile(corpus: dict):
    profile = MemoryProfile()
    result = materialize_memory(corpus, profile)
    assert result == {"object_memory": []}


# --- Contracts ---


def test_scenario_manifest_contract():
    manifest = ScenarioManifest(
        name="test_scenario",
        home_id="elder_home_v1",
        utterance="去厨房找水杯。",
        expected_final_status="completed",
        tags=["test"],
        purpose="unit test",
    )
    assert manifest.name == "test_scenario"
    assert manifest.expected_final_status == "completed"
    assert manifest.memory_profile is None


def test_scenario_manifest_rejects_extra_fields():
    with pytest.raises(Exception):
        ScenarioManifest(
            name="test",
            home_id="elder_home_v1",
            utterance="test",
            expected_final_status="completed",
            unknown_field="bad",
        )


def test_memory_profile_contract():
    profile = MemoryProfile(
        include_memory_ids=["  mem-cup-1  ", "mem-cup-2"],
        exclude_memory_ids=[" mem-cup-3 "],
    )
    assert profile.include_memory_ids == ["mem-cup-1", "mem-cup-2"]
    assert profile.exclude_memory_ids == ["mem-cup-3"]
    assert profile.full_corpus is False


def test_scenario_catalog_entry_contract():
    entry = ScenarioCatalogEntry(
        name="test",
        utterance="test utterance",
        expected_final_status="failed",
        tags=["tag1"],
        status="draft",
        data_source="legacy_files",
    )
    assert entry.status == "draft"
    assert entry.data_source == "legacy_files"


# --- Catalog ---


def test_catalog_json_loads_and_validates():
    catalog = load_catalog()
    assert len(catalog) > 0
    for entry in catalog:
        assert isinstance(entry, ScenarioCatalogEntry)


def test_catalog_active_scenarios_are_five():
    catalog = load_catalog()
    active = [e for e in catalog if e.status == "active"]
    assert len(active) == 5
    names = {e.name for e in active}
    assert names == set(BASELINE_SCENARIOS)


def test_active_scenarios_have_scenario_json():
    catalog = load_catalog()
    active_names = active_scenario_names(catalog)
    for name in active_names:
        manifest_path = SCENARIOS_ROOT / name / "scenario.json"
        assert manifest_path.is_file(), f"Missing scenario.json for active scenario: {name}"


def test_baseline_scenario_json_loads():
    for name in BASELINE_SCENARIOS:
        manifest = load_scenario_manifest(name)
        assert manifest is not None, f"Failed to load scenario.json for {name}"
        assert isinstance(manifest, ScenarioManifest)
        assert manifest.name == name
        assert manifest.home_id == "elder_home_v1"


def test_catalog_draft_scenarios_schema_only():
    catalog = load_catalog()
    draft = [e for e in catalog if e.status == "draft"]
    assert len(draft) >= 25
    for entry in draft:
        assert isinstance(entry, ScenarioCatalogEntry)
        assert entry.name
        assert entry.utterance


def test_materialized_entries_have_required_fields(corpus: dict):
    profile = MemoryProfile(
        include_memory_ids=["mem-cup-1", "mem-medicine-1", "mem-remote-1"],
    )
    result = materialize_memory(corpus, profile)
    for entry in result["object_memory"]:
        assert "memory_id" in entry
        assert "object_category" in entry
        anchor = entry.get("anchor", {})
        assert "room_id" in anchor
        assert "anchor_id" in anchor
        assert "viewpoint_id" in anchor
