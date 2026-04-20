from __future__ import annotations

import json
from pathlib import Path

from task_brain.memory import MemoryStore
from task_brain.world import MockWorld

_SCENARIO_NAMES = (
    "check_medicine_success",
    "check_medicine_stale_recover",
    "fetch_cup_retry",
    "object_not_found",
    "distractor_rejected",
)
_REQUIRED_WORLD_KEYS = {
    "rooms",
    "viewpoints",
    "furniture",
    "objects",
    "visibility",
    "symbolic_predicates",
}
_TARGET_CATEGORY = "cup"


def _scenario_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "scenarios"


def _scenario_file(scenario: str, name: str) -> Path:
    return _scenario_root() / scenario / name


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _visible_categories(world: MockWorld, viewpoint_ids: list[str]) -> set[str]:
    categories: set[str] = set()
    for viewpoint_id in viewpoint_ids:
        categories.update(
            item["category"]
            for item in world.get_visible_objects(viewpoint_id)
            if isinstance(item.get("category"), str)
        )
    return categories


def test_every_scenario_has_world_memory_and_failures_files() -> None:
    for scenario in _SCENARIO_NAMES:
        scenario_dir = _scenario_root() / scenario
        assert scenario_dir.is_dir(), f"missing scenario dir: {scenario}"

        world_path = _scenario_file(scenario, "world.json")
        memory_path = _scenario_file(scenario, "memory.json")
        failures_path = _scenario_file(scenario, "failures.json")

        assert world_path.is_file(), f"missing world.json for {scenario}"
        assert memory_path.is_file(), f"missing memory.json for {scenario}"
        assert failures_path.is_file(), f"missing failures.json for {scenario}"

        failures_payload = _load_json(failures_path)
        failure_rules = failures_payload.get("failure_rules")
        assert isinstance(failure_rules, list), f"invalid failure_rules for {scenario}"


def test_world_fixtures_load_and_include_required_keys() -> None:
    for scenario in _SCENARIO_NAMES:
        world_path = _scenario_file(scenario, "world.json")
        payload = _load_json(world_path)
        assert _REQUIRED_WORLD_KEYS.issubset(payload), f"world keys incomplete for {scenario}"

        world = MockWorld.from_file(world_path)
        viewpoint_payload = payload["viewpoints"]
        assert isinstance(viewpoint_payload, dict), f"viewpoints must be object for {scenario}"
        assert viewpoint_payload, f"viewpoints cannot be empty for {scenario}"

        for viewpoint_id, viewpoint in viewpoint_payload.items():
            assert isinstance(viewpoint, dict)
            assert world.get_viewpoint_room(viewpoint_id) == viewpoint["room_id"]
            assert isinstance(world.get_visible_objects(viewpoint_id), list)
            assert isinstance(world.get_visible_anchors(viewpoint_id), list)


def test_memory_fixtures_load_with_structured_anchors() -> None:
    for scenario in _SCENARIO_NAMES:
        store = MemoryStore.from_file(_scenario_file(scenario, "memory.json"))
        memories = store.dump_object_memory()
        assert memories, f"object_memory cannot be empty for {scenario}"

        for memory in memories:
            assert memory.anchor.room_id
            assert memory.anchor.anchor_id
            assert memory.anchor.anchor_type


def test_stale_scenario_top_memory_conflicts_with_world_truth() -> None:
    scenario = "check_medicine_stale_recover"
    world_path = _scenario_file(scenario, "world.json")
    memory_path = _scenario_file(scenario, "memory.json")

    world = MockWorld.from_file(world_path)
    store = MemoryStore.from_file(memory_path)
    memories = store.dump_object_memory()

    assert len(memories) >= 2
    top_memory = memories[0]
    second_memory = memories[1]

    assert top_memory.anchor.viewpoint_id is not None
    assert second_memory.anchor.viewpoint_id is not None

    top_categories = {
        item["category"] for item in world.get_visible_objects(top_memory.anchor.viewpoint_id)
    }
    second_categories = {
        item["category"] for item in world.get_visible_objects(second_memory.anchor.viewpoint_id)
    }

    assert "medicine_box" not in top_categories
    assert "medicine_box" in second_categories


def test_fetch_retry_scenario_contains_expected_failure_injection_rule() -> None:
    failures_payload = _load_json(_scenario_file("fetch_cup_retry", "failures.json"))
    failure_rules = failures_payload.get("failure_rules")

    assert isinstance(failure_rules, list)
    assert failure_rules, "fetch_cup_retry must include at least one failure rule"

    first_rule = failure_rules[0]
    assert isinstance(first_rule, dict)
    assert first_rule.get("attempt") == 1
    assert first_rule.get("status") == "failed"
    assert first_rule.get("reason") == "object_slipped"

    runtime_updates = first_rule.get("runtime_object_updates_candidate", [])
    assert isinstance(runtime_updates, list)
    assert all(
        update.get("reason") not in {"target_dropped", "target_location_changed"}
        for update in runtime_updates
        if isinstance(update, dict)
    )


def test_object_not_found_and_distractor_scenarios_miss_target_category() -> None:
    object_not_found_payload = _load_json(_scenario_file("object_not_found", "world.json"))
    object_not_found_world = MockWorld.from_file(_scenario_file("object_not_found", "world.json"))
    object_not_found_categories = _visible_categories(
        object_not_found_world,
        list(object_not_found_payload["viewpoints"].keys()),
    )
    assert _TARGET_CATEGORY not in object_not_found_categories

    distractor_payload = _load_json(_scenario_file("distractor_rejected", "world.json"))
    distractor_world = MockWorld.from_file(_scenario_file("distractor_rejected", "world.json"))
    distractor_categories = _visible_categories(
        distractor_world,
        list(distractor_payload["viewpoints"].keys()),
    )
    assert _TARGET_CATEGORY not in distractor_categories
    assert any(category != _TARGET_CATEGORY for category in distractor_categories)

    distractor_store = MemoryStore.from_file(_scenario_file("distractor_rejected", "memory.json"))
    first_candidate = distractor_store.dump_object_memory()[0]
    assert first_candidate.anchor.viewpoint_id is not None
    first_candidate_categories = {
        item["category"]
        for item in distractor_world.get_visible_objects(first_candidate.anchor.viewpoint_id)
    }
    assert _TARGET_CATEGORY not in first_candidate_categories
    assert first_candidate_categories
