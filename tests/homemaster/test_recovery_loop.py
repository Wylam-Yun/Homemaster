"""Tests for the Stage 05 recovery loop (P9).

Uses mock recovery decision providers and custom step decision providers
to verify loop control logic deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from homemaster.contracts import (
    ExecutionState,
    ModuleExecutionResult,
    OrchestrationPlan,
    PlanningContext,
    RecoveryDecision,
    StepDecision,
    Subtask,
    TaskCard,
    VerificationResult,
)
from homemaster.pipeline.core import PipelineContext
from homemaster.stages.executor import Stage05ExecutionResult, StepDecisionProvider
from homemaster.stages.recovery_loop import run_stage05_with_recovery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task_card() -> TaskCard:
    return TaskCard(
        task_type="fetch_object",
        target="水杯",
        delivery_target="user",
        location_hint="厨房",
        success_criteria=["把水杯交给用户"],
        needs_clarification=False,
        clarification_question=None,
        confidence=0.9,
    )


def _planning_context() -> PlanningContext:
    return PlanningContext(
        task_card=_task_card(),
        runtime_state_summary={"grounding_status": "grounded"},
        world_summary={"room_ids": ["kitchen", "living_room"]},
    )


def _single_subtask_plan() -> OrchestrationPlan:
    return OrchestrationPlan(
        goal="找到水杯",
        subtasks=[
            Subtask(
                id="find_cup",
                intent="找到水杯",
                target_object="水杯",
                room_hint="厨房",
                success_criteria=["观察到水杯"],
            )
        ],
    )


def _two_subtask_plan() -> OrchestrationPlan:
    return OrchestrationPlan(
        goal="找到水杯并交付",
        subtasks=[
            Subtask(
                id="find_cup",
                intent="找到水杯",
                target_object="水杯",
                room_hint="厨房",
                success_criteria=["观察到水杯"],
            ),
            Subtask(
                id="deliver_cup",
                intent="交付水杯给用户",
                target_object="水杯",
                recipient="user",
                depends_on=["find_cup"],
                success_criteria=["水杯已交付"],
            ),
        ],
    )


def _ctx(
    *,
    live_models: bool = False,
    scenario: str = "fetch_cup_retry",
    planning_context: PlanningContext | None = None,
    registry: Any = None,
) -> PipelineContext:
    return PipelineContext(
        run_id="test-recovery",
        scenario=scenario,
        utterance="帮我拿水杯",
        resolved_world_path=Path("/dev/null"),
        resolved_memory_path=Path("/dev/null"),
        runtime_memory_dir=Path("/tmp/test_memory"),
        case_dir=Path("/tmp/test_case"),
        results_dir=Path("/tmp/test_results"),
        live_models=live_models,
        mock_skills=True,
        config_path=Path("/dev/null"),
        provider_name="Mimo",
        embedding_provider_name="MemoryEmbedding",
        planning_context=planning_context or _planning_context(),
        registry=registry,
    )


class _FailingThenPassingProvider:
    """Fails on first call, succeeds on subsequent calls."""

    def __init__(self) -> None:
        self._call_count = 0

    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        self._call_count += 1
        if self._call_count == 1:
            # First call: produce a navigation decision with force_no_object
            return StepDecision(
                subtask_id=subtask.id,
                selected_skill="navigation",
                skill_input={
                    "goal_type": "find_object",
                    "target_object": subtask.target_object or "水杯",
                    "subtask_id": subtask.id,
                    "subtask_intent": subtask.intent,
                    "force_no_object": True,
                },
            )
        # Subsequent calls: normal navigation (succeeds)
        return StepDecision(
            subtask_id=subtask.id,
            selected_skill="navigation",
            skill_input={
                "goal_type": "find_object",
                "target_object": subtask.target_object or "水杯",
                "subtask_id": subtask.id,
                "subtask_intent": subtask.intent,
            },
        )


class _AlwaysFailingProvider:
    """Always produces a decision that causes verification failure."""

    def next_decision(
        self,
        subtask: Subtask,
        state: ExecutionState,
        context: PlanningContext,
    ) -> StepDecision:
        return StepDecision(
            subtask_id=subtask.id,
            selected_skill="navigation",
            skill_input={
                "goal_type": "find_object",
                "target_object": subtask.target_object or "水杯",
                "subtask_id": subtask.id,
                "subtask_intent": subtask.intent,
                "force_no_object": True,
            },
        )


def _mock_recovery_result(action: str, reason: str = "test") -> Any:
    """Create a mock RecoveryDecisionGenerationResult."""
    from dataclasses import dataclass as dc
    from typing import Any as _Any

    @dc(frozen=True)
    class MockResult:
        decision: RecoveryDecision
        prompt: str = ""
        raw_response: str = ""
        parsed_json: dict[str, _Any] = None  # type: ignore[assignment]
        provider: dict[str, _Any] = None  # type: ignore[assignment]
        attempts: tuple[dict[str, _Any], ...] = ()

    return MockResult(
        decision=RecoveryDecision(action=action, reason=reason),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_deterministic_mode_skips_recovery() -> None:
    """live_models=False: no recovery, behaviour unchanged from pre-P9."""
    plan = _single_subtask_plan()
    provider = _AlwaysFailingProvider()

    result, recovery_attempts = run_stage05_with_recovery(
        ctx=_ctx(live_models=False),
        plan=plan,
        decision_provider=provider,
        live_models=False,
        config_path="/dev/null",
        provider_name="Mimo",
    )

    assert result.final_state.task_status == "failed"
    assert recovery_attempts == []


def test_finish_failed_stops_loop() -> None:
    """When recovery decides finish_failed, loop exits immediately."""
    plan = _single_subtask_plan()
    provider = _AlwaysFailingProvider()

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        return_value=_mock_recovery_result("finish_failed", "no hope"),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
            max_recovery_attempts=3,
        )

    assert result.final_state.task_status == "failed"
    assert len(recovery_attempts) == 1
    assert recovery_attempts[0]["action"] == "finish_failed"


def test_ask_user_sets_needs_user_input() -> None:
    """When recovery decides ask_user, task_status becomes needs_user_input."""
    plan = _single_subtask_plan()
    provider = _AlwaysFailingProvider()

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        return_value=_mock_recovery_result("ask_user", "which cup?"),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
        )

    assert result.final_state.task_status == "needs_user_input"
    assert recovery_attempts[0]["action"] == "ask_user"


def test_retry_step_succeeds_on_second_try() -> None:
    """retry_step resets the failed subtask; second try succeeds."""
    plan = _single_subtask_plan()
    provider = _FailingThenPassingProvider()

    # First call: retry_step.  Monkeypatch _fresh_decision_provider so
    # round 0 uses the _FailingThenPassingProvider (already passed in) and
    # round 1 also uses it (it will return a passing decision).
    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        return_value=_mock_recovery_result("retry_step", "try again"),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
        )

    assert result.final_state.task_status == "completed"
    assert len(recovery_attempts) == 1
    assert recovery_attempts[0]["action"] == "retry_step"


def test_max_attempts_enforced() -> None:
    """Loop runs exactly max_recovery_attempts then gives up."""
    plan = _single_subtask_plan()
    provider = _AlwaysFailingProvider()
    max_attempts = 2

    call_count = 0

    def mock_recovery(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _mock_recovery_result("retry_step", f"attempt {call_count}")

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        side_effect=mock_recovery,
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
            max_recovery_attempts=max_attempts,
        )

    assert result.final_state.task_status == "failed"
    # max_attempts rounds of retry_step + 1 final finish_failed
    retry_attempts = [a for a in recovery_attempts if a["action"] == "retry_step"]
    assert len(retry_attempts) == max_attempts
    assert recovery_attempts[-1]["action"] == "finish_failed"
    assert "max recovery" in recovery_attempts[-1]["reason"]


def test_no_infinite_loop() -> None:
    """retry_step respects max_recovery_attempts — no infinite loop."""
    plan = _single_subtask_plan()
    provider = _AlwaysFailingProvider()

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        return_value=_mock_recovery_result("retry_step", "keep trying"),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
            max_recovery_attempts=3,
        )

    assert result.final_state.task_status == "failed"
    # Should have exactly 3 retry_step + 1 finish_failed = 4 total
    assert len(recovery_attempts) == 4
    assert all(a["action"] == "retry_step" for a in recovery_attempts[:3])
    assert recovery_attempts[3]["action"] == "finish_failed"


def test_recovery_decision_generation_failure_graceful() -> None:
    """When LLM can't produce a recovery decision, fail gracefully."""
    from homemaster.stages.recovery import RecoveryDecisionGenerationError

    plan = _single_subtask_plan()
    provider = _AlwaysFailingProvider()

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        side_effect=RecoveryDecisionGenerationError(
            error_type="recovery_generation_failed",
            message="LLM timeout",
        ),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
        )

    assert result.final_state.task_status == "failed"
    assert len(recovery_attempts) == 1
    assert recovery_attempts[0]["action"] == "finish_failed"
    assert "generation failed" in recovery_attempts[0]["reason"]


def test_recovery_attempts_populated_on_success() -> None:
    """When recovery succeeds, ctx.recovery_attempts is populated."""
    plan = _single_subtask_plan()
    provider = _FailingThenPassingProvider()

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        return_value=_mock_recovery_result("retry_step", "try again"),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
        )

    assert result.final_state.task_status == "completed"
    assert len(recovery_attempts) == 1
    assert recovery_attempts[0]["round"] == 0
    assert recovery_attempts[0]["action"] == "retry_step"


def test_reobserve_treated_as_retry_step() -> None:
    """reobserve is dispatched the same way as retry_step."""
    plan = _single_subtask_plan()
    provider = _FailingThenPassingProvider()

    with patch(
        "homemaster.stages.recovery_loop.load_provider_config",
        return_value="dummy",
    ), patch(
        "homemaster.stages.recovery_loop.generate_recovery_decision",
        return_value=_mock_recovery_result("reobserve", "look again"),
    ), patch(
        "homemaster.stages.recovery_loop._fresh_decision_provider",
        return_value=provider,
    ):
        result, recovery_attempts = run_stage05_with_recovery(
            ctx=_ctx(live_models=True),
            plan=plan,
            decision_provider=provider,
            live_models=True,
            config_path="/dev/null",
            provider_name="Mimo",
        )

    assert result.final_state.task_status == "completed"
    assert recovery_attempts[0]["action"] == "reobserve"
