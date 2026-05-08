from __future__ import annotations

import pytest

from homemaster.contracts import (
    ExecutionState,
    ModuleExecutionResult,
    OrchestrationPlan,
    PlanningContext,
    StepDecision,
    Subtask,
    TaskCard,
)
from homemaster.executor import (
    StaticStepDecisionProvider,
    execute_stage_05_plan,
)
from homemaster.skill_registry import (
    SkillInputValidationError,
    SkillManifest,
    SkillRegistry,
    build_default_skill_registry,
    get_default_skill_registry,
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


# ---------------------------------------------------------------------------
# P5: SkillRegistry tests
# ---------------------------------------------------------------------------


def test_skill_registry_register_and_lookup() -> None:
    registry = SkillRegistry()
    manifest = SkillManifest(name="test_skill", description="test", selectable_by_mimo=True)
    registry.register(manifest, validator=lambda x: x)

    assert registry.get_manifest("test_skill") is manifest
    assert registry.get_manifest("nonexistent") is None
    assert "test_skill" in registry.get_all_manifests()


def test_skill_registry_rejects_duplicate_registration() -> None:
    registry = SkillRegistry()
    manifest = SkillManifest(name="dup", description="test", selectable_by_mimo=True)
    registry.register(manifest, validator=lambda x: x)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(manifest, validator=lambda x: x)


def test_skill_registry_execute_dispatches_to_registered_executor() -> None:
    registry = SkillRegistry()
    manifest = SkillManifest(name="test", description="test", selectable_by_mimo=True)

    def mock_executor(
        decision: StepDecision, subtask: Subtask, state: ExecutionState
    ) -> ModuleExecutionResult:
        return ModuleExecutionResult(skill="test", status="success")

    registry.register(manifest, validator=lambda x: x, executor=mock_executor)

    decision = StepDecision(subtask_id="s1", selected_skill="test")
    subtask = Subtask(id="s1", intent="test")
    state = ExecutionState()

    result = registry.execute("test", decision, subtask, state)
    assert result.status == "success"
    assert result.skill == "test"


def test_skill_registry_execute_raises_when_no_executor() -> None:
    registry = SkillRegistry()
    manifest = SkillManifest(name="no_exec", description="test", selectable_by_mimo=True)
    registry.register(manifest, validator=lambda x: x)

    decision = StepDecision(subtask_id="s1", selected_skill="no_exec")
    subtask = Subtask(id="s1", intent="test")

    with pytest.raises(SkillInputValidationError, match="no executor registered"):
        registry.execute("no_exec", decision, subtask, ExecutionState())


def test_new_skill_runs_through_full_executor_loop() -> None:
    """P5 core acceptance: register greeting skill, run through execute_stage_05_plan().

    This proves that adding a new skill only requires registry.register()
    and does NOT require modifying the executor main loop.
    """
    registry = build_default_skill_registry()
    registry.register(
        manifest=SkillManifest(
            name="greeting",
            description="向用户打招呼",
            selectable_by_mimo=True,
        ),
        validator=lambda x: x,
        executor=lambda d, s, st: ModuleExecutionResult(
            skill="greeting",
            status="success",
            observation={"greeted": True},
        ),
    )

    plan = OrchestrationPlan(
        goal="向用户打招呼",
        subtasks=[
            Subtask(
                id="greet_user",
                intent="向用户打招呼",
                success_criteria=["完成打招呼"],
            )
        ],
    )
    context = PlanningContext(
        task_card=TaskCard(
            task_type="check_presence",
            target="用户",
            success_criteria=["完成打招呼"],
            needs_clarification=False,
            confidence=0.9,
        ),
    )

    result = execute_stage_05_plan(
        context,
        plan,
        decision_provider=StaticStepDecisionProvider(
            [StepDecision(subtask_id="greet_user", selected_skill="greeting")]
        ),
        skill_registry=registry,
    )

    assert result.final_state.task_status == "completed"
    assert result.skill_results[0].skill == "greeting"
    assert result.skill_results[0].status == "success"
    assert result.failure_records == []


def test_default_registry_contains_three_skills() -> None:
    registry = get_default_skill_registry()
    manifests = registry.get_all_manifests()
    assert set(manifests) == {"navigation", "operation", "verification"}
    assert manifests["verification"].selectable_by_mimo is False
    action_names = registry.get_action_names()
    assert action_names == ["navigation", "operation"]


def test_default_registry_navigation_and_operation_have_executors() -> None:
    registry = get_default_skill_registry()
    assert registry.has_executor("navigation") is True
    assert registry.has_executor("operation") is True
    assert registry.has_executor("verification") is False


def test_skill_manifest_rejects_invalid_names() -> None:
    """Skill name must be lowercase alphanumeric with underscores."""
    # Valid names
    SkillManifest(name="navigation", description="ok", selectable_by_mimo=True)
    SkillManifest(name="my_skill", description="ok", selectable_by_mimo=True)
    SkillManifest(name="skill2", description="ok", selectable_by_mimo=True)

    # Invalid: blank
    with pytest.raises(ValueError, match="must not be blank"):
        SkillManifest(name="", description="bad", selectable_by_mimo=True)

    # Invalid: uppercase
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        SkillManifest(name="Navigation", description="bad", selectable_by_mimo=True)

    # Invalid: spaces
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        SkillManifest(name="my skill", description="bad", selectable_by_mimo=True)

    # Invalid: starts with digit
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        SkillManifest(name="1skill", description="bad", selectable_by_mimo=True)

    # Invalid: special chars
    with pytest.raises(ValueError, match="lowercase alphanumeric"):
        SkillManifest(name="my-skill", description="bad", selectable_by_mimo=True)
