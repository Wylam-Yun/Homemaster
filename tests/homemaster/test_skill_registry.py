from __future__ import annotations

import pytest

from homemaster.skill_registry import (
    SkillInputValidationError,
    get_stage_05_mimo_action_manifests,
    get_stage_05_skill_manifests,
    get_stage_05_skill_prompt_payload,
    validate_skill_input,
)


def test_stage_05_skill_manifests_are_serializable_and_action_list_excludes_verification() -> None:
    manifests = get_stage_05_skill_manifests()
    action_manifests = get_stage_05_mimo_action_manifests()
    payload = get_stage_05_skill_prompt_payload(action_only=False)

    assert set(manifests) == {"navigation", "operation", "verification"}
    assert [manifest.name for manifest in action_manifests] == ["navigation", "operation"]
    assert all(manifest.name != "verification" for manifest in action_manifests)
    assert {item["name"] for item in payload} == {"navigation", "operation", "verification"}
    assert manifests["verification"].selectable_by_mimo is False


def test_navigation_skill_input_accepts_find_object_and_go_to_location() -> None:
    find_object = validate_skill_input(
        "navigation",
        {
            "goal_type": "find_object",
            "target_object": "水杯",
            "subtask_id": "find_cup",
            "subtask_intent": "找到水杯",
        },
    )
    go_to_location = validate_skill_input(
        "navigation",
        {
            "goal_type": "go_to_location",
            "target_location": "客厅沙发旁",
            "subtask_id": "go_to_user",
            "subtask_intent": "回到用户位置",
        },
    )

    assert find_object["target_object"] == "水杯"
    assert go_to_location["target_location"] == "客厅沙发旁"


def test_skill_input_rejects_manual_verification_and_missing_required_fields() -> None:
    with pytest.raises(SkillInputValidationError) as manual_verification:
        validate_skill_input("verification", {"scope": "subtask"})
    with pytest.raises(SkillInputValidationError) as missing_object:
        validate_skill_input("navigation", {"goal_type": "find_object"})
    with pytest.raises(SkillInputValidationError) as missing_intent:
        validate_skill_input("operation", {"target_object": "水杯"})

    assert "not selectable" in manual_verification.value.message
    assert "target_object" in missing_object.value.message
    assert "subtask_intent" in missing_intent.value.message
