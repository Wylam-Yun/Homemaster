from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from task_brain.adapters import (
    EmbodiedSubgoalRequest,
    FakeRoboBrainClient,
    MockAtomicExecutor,
    MockPerceptionAdapter,
    MockVLNAdapter,
    SimulatorStyleAdapter,
)
from task_brain.capabilities import default_capability_registry
from task_brain.cli import app
from task_brain.context import build_task_context
from task_brain.domain import (
    FailureAnalysis,
    FailureType,
    HighLevelProgress,
    ParsedTask,
    Predicate,
    RuntimeState,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskNegativeEvidence,
    TaskRequest,
)
from task_brain.gateway import handle_message
from task_brain.graph import run_task_graph
from task_brain.memory import MemoryStore, retrieve_candidates
from task_brain.parser import parse_instruction
from task_brain.planner import LLMHighLevelPlanner, PlannerService
from task_brain.recovery import analyze_failure, apply_recovery_state_updates, decide_recovery
from task_brain.verification import VerificationEngine, VerificationResult
from task_brain.world import MockWorld

_CHECK_MEDICINE_INSTRUCTION = "去桌子那边看看药盒是不是还在。"
_FETCH_CUP_INSTRUCTION = "去厨房找水杯，然后拿给我"
_WORLD_FIXTURE = Path(__file__).parent / "fixtures" / "world_minimal.json"
_SCENARIOS_ROOT = Path(__file__).resolve().parents[1] / "data" / "scenarios"
_RUNNER = CliRunner()


def _stage1(_: Path) -> None:
    request = TaskRequest(
        source="cli",
        user_id="u-stage1",
        utterance="帮我看看药盒还在不在",
        request_id="req-stage1",
    )
    runtime = RuntimeState()
    dumped = runtime.model_dump()

    assert request.request_id == "req-stage1"
    assert request.created_at is not None
    assert "current_observation" in dumped
    assert "task_negative_evidence" in dumped


def _stage2(_: Path) -> None:
    world = MockWorld.from_file(_WORLD_FIXTURE)
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")

    assert observation.source.value == "mock_world"
    assert observation.visible_objects
    assert observation.raw_ref == "mock_world:kitchen_table_viewpoint"


def _stage3(_: Path) -> None:
    store = MemoryStore.from_dict(
        {
            "object_memory": [
                {
                    "memory_id": "mem-cup-1",
                    "object_category": "cup",
                    "anchor": {
                        "room_id": "kitchen",
                        "anchor_id": "kitchen_table_1",
                        "anchor_type": "table",
                    },
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                }
            ]
        }
    )
    parsed_task = ParsedTask(
        intent=TaskIntent.FETCH_OBJECT,
        target_object=TargetObject(category="cup", aliases=["水杯"]),
    )
    runtime_state = RuntimeState(
        task_negative_evidence=[
            TaskNegativeEvidence(
                task_request_id="req-stage3",
                location_key="kitchen:kitchen_table_1",
                status="searched_not_found",
                object_category="cup",
            )
        ]
    )
    excluded = retrieve_candidates(
        parsed_task=parsed_task,
        memory_store=store,
        runtime_state=runtime_state,
    )
    revisited = retrieve_candidates(
        parsed_task=parsed_task,
        memory_store=store,
        runtime_state=runtime_state,
        allow_revisit=True,
    )

    assert excluded == []
    assert revisited and revisited[0]["memory_id"] == "mem-cup-1"


def _stage4(_: Path) -> None:
    request = TaskRequest(
        source="cli",
        user_id="u-stage4",
        utterance=_FETCH_CUP_INSTRUCTION,
    )
    parsed = parse_instruction(request)
    context = build_task_context(
        request=request,
        parsed_task=parsed,
        runtime_state=RuntimeState(),
        ranked_candidates=[{"memory_id": "mem-cup-1"}],
    )
    dumped = context.model_dump()

    assert parsed.intent == TaskIntent.FETCH_OBJECT
    assert context.runtime_state is not None
    assert "memory_store" not in dumped
    assert "world" not in dumped
    assert "trace" not in dumped


def _stage5(_: Path) -> None:
    registry = default_capability_registry()
    required = {
        "mock_vln.navigate",
        "mock_perception.observe",
        "robobrain.plan",
        "mock_atomic_executor.execute",
        "verification.evaluate",
        "recovery.analyze_failure",
        "recovery.decide",
        "memory.reconcile",
    }

    assert required.issubset(registry)
    assert all(registry[key].name == key for key in required)


def _stage6(_: Path) -> None:
    service = PlannerService(llm_first=False)
    plan = service.plan_from_request(
        request=TaskRequest(
            source="cli",
            user_id="u-stage6",
            utterance="这个输入命中不到规则",
        ),
        runtime_state=RuntimeState(),
        ranked_candidates=[],
    )

    assert plan.subgoals[0].subgoal_type == SubgoalType.ASK_CLARIFICATION
    assert service.last_diagnostics["planner_mode"] == "deterministic_parse_error"


def _stage7(_: Path) -> None:
    world = MockWorld.from_file(_WORLD_FIXTURE)
    nav = MockVLNAdapter.navigate(world, "kitchen_table_viewpoint")
    atomic = FakeRoboBrainClient().plan(
        EmbodiedSubgoalRequest(
            subgoal={"subgoal_type": "embodied_manipulation"},
            target_object={"category": "cup"},
        )
    )
    executed = MockAtomicExecutor.execute(
        plan=atomic,
        runtime_state=RuntimeState(),
        world=world,
        attempt=1,
    )

    assert nav.arrived is True
    assert atomic.atomic_plan
    assert executed.status == "success"


def _stage8(_: Path) -> None:
    world = MockWorld.from_file(_WORLD_FIXTURE)
    observation = MockPerceptionAdapter.observe(world, "kitchen_table_viewpoint")
    result = VerificationEngine().evaluate(
        success_conditions=[Predicate(name="visible_category", args=["cup"])],
        evidence={
            "observation": observation.model_dump(mode="json"),
            "robot_runtime_state": {
                "viewpoint_id": "kitchen_table_viewpoint",
                "room_id": "kitchen",
            },
        },
    )

    assert result.passed is True
    assert result.failed_conditions == []


def _stage9(_: Path) -> None:
    runtime_state = RuntimeState(selected_candidate_id="mem-cup-1")
    failure = analyze_failure(
        verification_result=VerificationResult(
            passed=False,
            failed_conditions=[Predicate(name="visible_category", args=["cup"])],
            evidence={},
        ),
        subgoal_type=SubgoalType.VERIFY_OBJECT_PRESENCE,
        runtime_state=runtime_state,
        target_category="cup",
    )
    decision = decide_recovery(
        failure_analysis=failure,
        runtime_state=runtime_state,
        ranked_candidates=[
            {
                "memory_id": "mem-cup-1",
                "anchor": {
                    "room_id": "kitchen",
                    "anchor_id": "kitchen_table_1",
                    "anchor_type": "table",
                },
            },
            {
                "memory_id": "mem-cup-2",
                "anchor": {
                    "room_id": "living_room",
                    "anchor_id": "side_table_1",
                    "anchor_type": "table",
                },
            },
        ],
        target_category="cup",
    )
    updated = apply_recovery_state_updates(
        runtime_state=runtime_state,
        failure_analysis=failure,
        recovery_decision=decision,
        ranked_candidates=[
            {
                "memory_id": "mem-cup-1",
                "anchor": {
                    "room_id": "kitchen",
                    "anchor_id": "kitchen_table_1",
                    "anchor_type": "table",
                },
            },
            {
                "memory_id": "mem-cup-2",
                "anchor": {
                    "room_id": "living_room",
                    "anchor_id": "side_table_1",
                    "anchor_type": "table",
                },
            },
        ],
        target_category="cup",
    )

    assert failure.failure_type == FailureType.OBJECT_PRESENCE_FAILURE
    assert decision.action.value == "switch_candidate"
    assert updated.task_negative_evidence


def _stage10(_: Path) -> None:
    scenarios = {
        "check_medicine_success",
        "check_medicine_stale_recover",
        "fetch_cup_retry",
        "object_not_found",
        "distractor_rejected",
    }
    existing = {item.name for item in _SCENARIOS_ROOT.iterdir() if item.is_dir()}

    assert scenarios.issubset(existing)
    for name in scenarios:
        root = _SCENARIOS_ROOT / name
        assert (root / "world.json").exists()
        assert (root / "memory.json").exists()
        assert (root / "failures.json").exists()


def _stage11(_: Path) -> None:
    result = run_task_graph(
        scenario="check_medicine_success",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )
    events = [item["event"] for item in result["trace"] if isinstance(item, dict)]

    assert result["final_status"] == "success"
    assert events.index("retrieve_memory") < events.index("generate_plan")
    assert events.index("validate_plan") < events.index("execute_subgoal_loop")


def _stage12(tmp_path: Path) -> None:
    output = tmp_path / "trace.jsonl"
    result = _RUNNER.invoke(
        app,
        [
            "run",
            "--scenario",
            "check_medicine_success",
            "--instruction",
            _CHECK_MEDICINE_INSTRUCTION,
            "--trace-jsonl",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert output.exists()
    assert "trace_events:" in result.stdout


def _stage13(_: Path) -> None:
    result = run_task_graph(
        scenario="check_medicine_stale_recover",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )
    update_event = next(
        item
        for item in result["trace"]
        if isinstance(item, dict) and item.get("event") == "update_memory"
    )

    assert "updated" in update_event
    assert "created" in update_event
    assert "stale" in update_event
    assert "contradicted" in update_event
    assert "skipped_runtime_updates" in update_event


def _stage14(_: Path) -> None:
    object_not_found = run_task_graph(
        scenario="object_not_found",
        instruction=_FETCH_CUP_INSTRUCTION,
    )
    distractor_rejected = run_task_graph(
        scenario="distractor_rejected",
        instruction=_FETCH_CUP_INSTRUCTION,
    )

    assert object_not_found["final_status"] == "failed"
    assert distractor_rejected["final_status"] == "failed"


def _stage15(_: Path) -> None:
    context = build_task_context(
        request=TaskRequest(
            source="cli",
            user_id="u-stage15",
            utterance=_CHECK_MEDICINE_INSTRUCTION,
        ),
        parsed_task=ParsedTask(
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            target_object=TargetObject(category="medicine_box", aliases=["药盒"]),
        ),
        runtime_state=RuntimeState(
            retry_budget=1,
            recent_failure_analysis=FailureAnalysis(
                failure_type=FailureType.OBJECT_PRESENCE_FAILURE,
                reason="searched_not_found",
            ),
            candidate_exclusion_state={"kitchen:kitchen_table_1": "searched_not_found"},
            high_level_progress=HighLevelProgress(
                current_subgoal_id="sg-2",
                execution_phase="running",
            ),
        ),
        ranked_candidates=[{"memory_id": "mem-medicine-1", "score": 9.0}],
        category_prior_hits=[{"anchor_id": "anchor_kitchen_cabinet_1"}],
        recent_episodic_summaries=[{"summary": "recent success in kitchen"}],
    )
    prompt = LLMHighLevelPlanner._build_prompt(context)

    assert "category_prior_hits" in prompt
    assert "recent_episodic_summaries" in prompt
    assert "candidate_exclusion_state" in prompt
    assert "json_only" in prompt


def _stage16(_: Path) -> None:
    observation = SimulatorStyleAdapter.to_observation(
        {
            "event_id": "evt-stage16",
            "pose": {"viewpoint_id": "vp-1", "room_id": "kitchen"},
            "visible_objects": [{"object_id": "obj-cup-1", "category": "cup"}],
        }
    )

    response = handle_message(
        message={
            "platform": "feishu",
            "user_id": "u-allow",
            "session_id": "s-stage16",
            "text": _CHECK_MEDICINE_INSTRUCTION,
        },
        scenario="check_medicine_success",
        allowlisted_users={"u-allow"},
    )

    assert observation.raw_ref == "simulator:evt-stage16"
    assert response.accepted is True
    assert response.status == "processed"


_STAGE_CHECKS: list[tuple[str, Callable[[Path], None]]] = [
    ("stage_1", _stage1),
    ("stage_2", _stage2),
    ("stage_3", _stage3),
    ("stage_4", _stage4),
    ("stage_5", _stage5),
    ("stage_6", _stage6),
    ("stage_7", _stage7),
    ("stage_8", _stage8),
    ("stage_9", _stage9),
    ("stage_10", _stage10),
    ("stage_11", _stage11),
    ("stage_12", _stage12),
    ("stage_13", _stage13),
    ("stage_14", _stage14),
    ("stage_15", _stage15),
    ("stage_16", _stage16),
]


def test_stage_acceptance_matrix_case_count() -> None:
    assert len(_STAGE_CHECKS) == 16


@pytest.mark.parametrize(
    ("stage", "checker"),
    _STAGE_CHECKS,
    ids=[item[0] for item in _STAGE_CHECKS],
)
def test_stage_acceptance_matrix(
    stage: str,
    checker: Callable[[Path], None],
    tmp_path: Path,
) -> None:
    _ = stage
    checker(tmp_path)
