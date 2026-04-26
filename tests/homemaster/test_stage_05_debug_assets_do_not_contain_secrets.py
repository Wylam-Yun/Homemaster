from __future__ import annotations

from pathlib import Path

from homemaster.contracts import OrchestrationPlan, PlanningContext, StepDecision, Subtask
from homemaster.executor import StaticStepDecisionProvider, execute_stage_05_plan
from homemaster.stage_05 import write_stage_05_debug_assets


def test_stage_05_debug_assets_do_not_contain_secrets(tmp_path: Path) -> None:
    context = PlanningContext(
        task_card={
            "task_type": "fetch_object",
            "target": "水杯",
            "delivery_target": "user",
            "location_hint": "厨房",
            "success_criteria": ["拿到水杯"],
            "needs_clarification": False,
            "clarification_question": None,
            "confidence": 0.9,
        }
    )
    plan = OrchestrationPlan(
        goal="找到水杯",
        subtasks=[
            Subtask(
                id="find_cup",
                intent="找到水杯",
                target_object="水杯",
                success_criteria=["观察到水杯"],
            )
        ],
    )
    result = execute_stage_05_plan(
        context,
        plan,
        decision_provider=StaticStepDecisionProvider(
            [
                StepDecision(
                    subtask_id="find_cup",
                    selected_skill="navigation",
                    skill_input={
                        "goal_type": "find_object",
                        "target_object": "水杯",
                        "subtask_id": "find_cup",
                        "subtask_intent": "找到水杯",
                    },
                )
            ]
        ),
    )
    case_dir = tmp_path / "cases" / "debug_secret_scan"

    write_stage_05_debug_assets(
        case_dir=case_dir,
        results_dir=tmp_path / "results",
        expected={"must_pass": True},
        actual={
            "provider": {
                "api_key": "sk-should-not-appear",
                "headers": {"Authorization": "Bearer sk-should-not-appear"},
            },
            "execution": result.as_debug_payload(),
        },
        status="PASS",
    )

    combined = "\n".join(path.read_text(encoding="utf-8") for path in case_dir.iterdir())
    assert "sk-should-not-appear" not in combined
    assert "Authorization" in combined
    assert "Bearer" not in combined
