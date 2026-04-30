"""Scenario catalog loader and manifest resolver."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.contracts import MemoryProfile, ScenarioCatalogEntry, ScenarioManifest
from homemaster.runtime import REPO_ROOT

CATALOG_PATH = REPO_ROOT / "data" / "scenarios" / "catalog.json"
SCENARIOS_ROOT = REPO_ROOT / "data" / "scenarios"


def load_catalog(path: Path = CATALOG_PATH) -> list[ScenarioCatalogEntry]:
    """Load and validate all entries from catalog.json."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [ScenarioCatalogEntry(**entry) for entry in raw.get("scenarios", [])]


def load_scenario_manifest(scenario_name: str) -> ScenarioManifest | None:
    """Load scenario.json for a named scenario. Returns None if not found."""
    manifest_path = SCENARIOS_ROOT / scenario_name / "scenario.json"
    if not manifest_path.is_file():
        return None
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    return ScenarioManifest(**raw)


def load_memory_profile(scenario_name: str) -> MemoryProfile | None:
    """Load memory_profile.json for a named scenario. Returns None if not found."""
    profile_path = SCENARIOS_ROOT / scenario_name / "memory_profile.json"
    if not profile_path.is_file():
        return None
    raw = json.loads(profile_path.read_text(encoding="utf-8"))
    return MemoryProfile(**raw)


def active_scenario_names(
    catalog: list[ScenarioCatalogEntry] | None = None,
) -> list[str]:
    """Return names of all active scenarios from catalog."""
    if catalog is None:
        catalog = load_catalog()
    return [entry.name for entry in catalog if entry.status == "active"]
