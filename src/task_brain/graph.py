"""Coarse-grained LangGraph orchestration for Phase A."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from task_brain.adapters import (
    EmbodiedSubgoalRequest,
    FakeRoboBrainClient,
    MockAtomicExecutor,
    MockPerceptionAdapter,
    MockVLNAdapter,
)
from task_brain.context import build_task_context
from task_brain.domain import (
    EmbodiedActionProgress,
    FailureAnalysis,
    HighLevelPlan,
    HighLevelProgress,
    ParsedTask,
    Predicate,
    RobotRuntimeState,
    RuntimeObjectUpdate,
    RuntimeState,
    Subgoal,
    SubgoalType,
    TargetObject,
    TaskIntent,
    TaskRequest,
)
from task_brain.evidence import build_verification_evidence
from task_brain.memory import MemoryStore, reconcile_memory_after_task, retrieve_candidates
from task_brain.parser import parse_instruction
from task_brain.planner import PlannerService, PlanValidator
from task_brain.recovery import analyze_failure, apply_recovery_state_updates, decide_recovery
from task_brain.verification import VerificationEngine
from task_brain.world import MockWorld


class TaskGraphState(TypedDict, total=False):
    """State carried across coarse-grained graph nodes."""

    scenario: str
    instruction: str
    request: TaskRequest
    parsed_task: ParsedTask
    world: MockWorld
    memory_store: MemoryStore
    runtime_state: RuntimeState
    memory_context: dict[str, Any]
    task_context: Any
    plan: HighLevelPlan
    current_observation: Any
    selected_object_id: str | None
    recent_failure_analysis: FailureAnalysis | None
    trace: list[dict[str, Any]]
    final_status: str
    failure_rules: list[dict[str, Any]]
    subgoal_attempts: dict[str, int]
    latest_execution_result: dict[str, Any] | None
    constraints: dict[str, Any]
    response: dict[str, Any]


def build_task_graph() -> Any:
    """Build a real compiled LangGraph for Phase A orchestration."""
    graph = StateGraph(TaskGraphState)

    graph.add_node("input_instruction", _input_instruction)
    graph.add_node("parse_instruction", _parse_instruction)
    graph.add_node("retrieve_memory", _retrieve_memory)
    graph.add_node("build_task_context", _build_context_node)
    graph.add_node("generate_plan", _generate_plan)
    graph.add_node("validate_plan", _validate_plan)
    graph.add_node("execute_subgoal_loop", _execute_subgoal_loop)
    graph.add_node("final_task_verification", _final_task_verification)
    graph.add_node("update_memory", _update_memory)
    graph.add_node("respond_with_trace", _respond_with_trace)

    graph.add_edge(START, "input_instruction")
    graph.add_edge("input_instruction", "parse_instruction")
    graph.add_edge("parse_instruction", "retrieve_memory")
    graph.add_edge("retrieve_memory", "build_task_context")
    graph.add_edge("build_task_context", "generate_plan")
    graph.add_edge("generate_plan", "validate_plan")
    graph.add_edge("validate_plan", "execute_subgoal_loop")
    graph.add_edge("execute_subgoal_loop", "final_task_verification")
    graph.add_edge("final_task_verification", "update_memory")
    graph.add_edge("update_memory", "respond_with_trace")
    graph.add_edge("respond_with_trace", END)

    return graph.compile()


def run_task_graph(*, scenario: str, instruction: str, user_id: str = "u-graph") -> TaskGraphState:
    """Run one task on a scenario fixture through the compiled graph."""
    scenario_dir = _scenario_dir(scenario)
    world = MockWorld.from_file(scenario_dir / "world.json")
    memory_store = MemoryStore.from_file(scenario_dir / "memory.json")
    failures_payload = json.loads((scenario_dir / "failures.json").read_text(encoding="utf-8"))

    initial_state: TaskGraphState = {
        "scenario": scenario,
        "instruction": instruction,
        "world": world,
        "memory_store": memory_store,
        "runtime_state": RuntimeState(robot_runtime_state=RobotRuntimeState()),
        "memory_context": {
            "ranked_candidates": [],
            "object_memory_hits": [],
            "category_prior_hits": [],
            "recent_episodic_summaries": [],
        },
        "trace": [],
        "final_status": "running",
        "failure_rules": _ensure_list_of_dict(failures_payload.get("failure_rules", [])),
        "subgoal_attempts": {},
        "latest_execution_result": None,
        "constraints": {},
    }

    app = build_task_graph()
    final_state = app.invoke(initial_state)
    final_state["request"] = TaskRequest(
        source="cli",
        user_id=user_id,
        utterance=instruction,
    )
    return final_state


def _input_instruction(state: TaskGraphState) -> TaskGraphState:
    request = TaskRequest(
        source="cli",
        user_id="u-graph",
        utterance=state["instruction"],
    )
    _append_trace(state, "input_instruction", instruction=state["instruction"])
    return {"request": request}


def _parse_instruction(state: TaskGraphState) -> TaskGraphState:
    constraints = dict(state.get("constraints", {}))
    request = state["request"]
    try:
        parsed_task = parse_instruction(request)
    except ValueError as exc:
        parsed_task = ParsedTask(
            intent=TaskIntent.CHECK_OBJECT_PRESENCE,
            target_object=TargetObject(category="unknown_object"),
            requires_navigation=False,
            requires_manipulation=False,
        )
        constraints["parse_error"] = str(exc)

    _append_trace(
        state,
        "parse_instruction",
        intent=parsed_task.intent.value,
        target_category=parsed_task.target_object.category,
    )
    return {"parsed_task": parsed_task, "constraints": constraints}


def _retrieve_memory(state: TaskGraphState) -> TaskGraphState:
    parsed_task = state["parsed_task"]
    runtime_state = state["runtime_state"]
    memory_store = state["memory_store"]

    ranked_candidates = retrieve_candidates(
        parsed_task=parsed_task,
        memory_store=memory_store,
        runtime_state=runtime_state,
    )
    category_prior_hits = memory_store.retrieve_category_prior(
        parsed_task.target_object.category,
        parsed_task.target_object.aliases,
    )
    recent_episodic_summaries = memory_store.retrieve_recent_episodic_summaries(
        parsed_task.target_object.category,
        parsed_task.target_object.aliases,
        limit=5,
    )
    object_memory_hits = [
        item.model_dump()
        for item in memory_store.retrieve_object_memory(
            parsed_task.target_object.category,
            parsed_task.target_object.aliases,
        )
    ]

    if ranked_candidates and runtime_state.selected_candidate_id is None:
        runtime_state.selected_candidate_id = ranked_candidates[0]["memory_id"]

    selected_object_id = runtime_state.selected_candidate_id
    _append_trace(
        state,
        "retrieve_memory",
        candidate_count=len(ranked_candidates),
        category_prior_count=len(category_prior_hits),
        episodic_summary_count=len(recent_episodic_summaries),
        selected_candidate_id=runtime_state.selected_candidate_id,
    )

    return {
        "memory_context": {
            "ranked_candidates": ranked_candidates,
            "object_memory_hits": object_memory_hits,
            "category_prior_hits": category_prior_hits,
            "recent_episodic_summaries": recent_episodic_summaries,
        },
        "selected_object_id": selected_object_id,
    }


def _build_context_node(state: TaskGraphState) -> TaskGraphState:
    runtime_state = state["runtime_state"]
    memory_context = state["memory_context"]

    task_context = build_task_context(
        request=state["request"],
        parsed_task=state["parsed_task"],
        runtime_state=runtime_state,
        ranked_candidates=memory_context["ranked_candidates"],
        object_memory_hits=memory_context["object_memory_hits"],
        category_prior_hits=memory_context.get("category_prior_hits"),
        recent_episodic_summaries=memory_context.get("recent_episodic_summaries"),
        constraints=state.get("constraints") or {},
    )

    _append_trace(
        state,
        "build_task_context",
        candidate_count=len(task_context.ranked_candidates),
        negative_evidence_count=len(task_context.task_negative_evidence),
    )
    return {"task_context": task_context}


def _generate_plan(state: TaskGraphState) -> TaskGraphState:
    planner = PlannerService()
    plan = planner.plan(state["task_context"])
    _append_planner_diagnostics_trace(state, planner.last_diagnostics)
    _append_trace(
        state,
        "generate_plan",
        plan_id=plan.plan_id,
        subgoal_count=len(plan.subgoals),
    )
    return {"plan": plan}


def _validate_plan(state: TaskGraphState) -> TaskGraphState:
    validator = PlanValidator()
    validator.validate(state["plan"], state["task_context"])
    _append_trace(state, "validate_plan", plan_id=state["plan"].plan_id)
    return {}


def _execute_subgoal_loop(state: TaskGraphState) -> TaskGraphState:
    runtime_state = state["runtime_state"]
    verifier = VerificationEngine()

    _append_trace(state, "execute_subgoal_loop")

    index = 0
    while index < len(state["plan"].subgoals):
        subgoal = state["plan"].subgoals[index]
        _start_subgoal_progress(runtime_state, state["plan"], subgoal, index)

        attempts = state["subgoal_attempts"].get(subgoal.subgoal_id, 0) + 1
        state["subgoal_attempts"][subgoal.subgoal_id] = attempts

        verification_result = _execute_and_verify_subgoal(
            state=state,
            subgoal=subgoal,
            attempt=attempts,
            verifier=verifier,
        )

        if verification_result.passed:
            _append_trace(
                state,
                "verify_subgoal",
                subgoal_id=subgoal.subgoal_id,
                subgoal_type=subgoal.subgoal_type.value,
                passed=True,
            )
            _mark_subgoal_completed(runtime_state, state["plan"], subgoal, index)
            index += 1
            continue

        if subgoal.subgoal_type in {SubgoalType.EMBODIED_MANIPULATION, SubgoalType.RETURN_TO_USER}:
            _append_trace(
                state,
                "post_action_verification_failed",
                subgoal_id=subgoal.subgoal_id,
                failed_conditions=[item.name for item in verification_result.failed_conditions],
            )

        analysis = analyze_failure(
            verification_result=verification_result,
            subgoal_type=subgoal.subgoal_type,
            runtime_state=runtime_state,
            target_category=state["parsed_task"].target_object.category,
        )
        _append_trace(
            state,
            "analyze_failure",
            failure_type=analysis.failure_type.value,
            reason=analysis.reason,
        )

        decision = decide_recovery(
            failure_analysis=analysis,
            runtime_state=runtime_state,
            ranked_candidates=state["memory_context"]["ranked_candidates"],
            target_category=state["parsed_task"].target_object.category,
        )
        _append_trace(
            state,
            "decide_recovery",
            action=decision.action.value,
            reason=decision.reason,
        )

        previous_negative_count = len(runtime_state.task_negative_evidence)
        updated_runtime = apply_recovery_state_updates(
            runtime_state=runtime_state,
            failure_analysis=analysis,
            recovery_decision=decision,
            ranked_candidates=state["memory_context"]["ranked_candidates"],
            target_category=state["parsed_task"].target_object.category,
        )
        state["runtime_state"] = updated_runtime
        runtime_state = updated_runtime
        state["recent_failure_analysis"] = updated_runtime.recent_failure_analysis

        if len(runtime_state.task_negative_evidence) > previous_negative_count:
            _append_trace(state, "write_task_negative_evidence")

        action = decision.action
        if action.value == "continue":
            index += 1
            continue
        if action.value == "retry_same_subgoal":
            _append_trace(state, "recovery_retry_same_subgoal", subgoal_id=subgoal.subgoal_id)
            continue
        if action.value == "switch_candidate":
            _append_trace(
                state,
                "recovery_switch_candidate",
                next_candidate_id=decision.next_candidate_id,
            )
            _replan_from_runtime(state, reason="switch_candidate")
            index = 0
            continue
        if action.value == "re_observe":
            _do_reobserve(state)
            _replan_from_runtime(state, reason="re_observe")
            index = 0
            continue
        if action.value == "replan":
            _replan_from_runtime(state, reason="replan")
            index = 0
            continue
        if action.value == "ask_clarification":
            state["final_status"] = "clarification_required"
            break
        if action.value == "report_failure":
            _append_trace(state, "recovery_report_failure")
            state["final_status"] = "failed"
            break

    if state.get("final_status") == "running":
        state["final_status"] = "ready_for_final_verification"

    return {
        "runtime_state": state["runtime_state"],
        "memory_context": state["memory_context"],
        "task_context": state.get("task_context"),
        "plan": state["plan"],
        "current_observation": state.get("current_observation"),
        "selected_object_id": state.get("selected_object_id"),
        "recent_failure_analysis": state.get("recent_failure_analysis"),
        "trace": state["trace"],
        "final_status": state["final_status"],
        "subgoal_attempts": state["subgoal_attempts"],
        "latest_execution_result": state.get("latest_execution_result"),
    }


def _final_task_verification(state: TaskGraphState) -> TaskGraphState:
    if state["final_status"] in {"failed", "clarification_required"}:
        _append_trace(
            state,
            "final_task_verification",
            skipped=True,
            final_status=state["final_status"],
        )
        return {
            "trace": state["trace"],
            "final_status": state["final_status"],
        }

    verifier = VerificationEngine()
    target_category = state["parsed_task"].target_object.category
    evidence = build_verification_evidence(
        observation=state["runtime_state"].current_observation,
        robot_runtime_state=state["runtime_state"].robot_runtime_state,
        execution_result=state.get("latest_execution_result"),
        task_negative_evidence=state["runtime_state"].task_negative_evidence,
    )
    final_result = verifier.evaluate(
        success_conditions=[Predicate(name="task_goal_satisfied", args=[target_category])],
        evidence=evidence,
    )

    state["final_status"] = "success" if final_result.passed else "failed"
    _append_trace(
        state,
        "final_task_verification",
        passed=final_result.passed,
        failed_conditions=[item.name for item in final_result.failed_conditions],
    )
    return {
        "trace": state["trace"],
        "final_status": state["final_status"],
    }


def _update_memory(state: TaskGraphState) -> TaskGraphState:
    reconciliation = reconcile_memory_after_task(
        parsed_task=state["parsed_task"],
        runtime_state=state["runtime_state"],
        memory_store=state["memory_store"],
        final_status=state["final_status"],
        latest_execution_result=state.get("latest_execution_result"),
    )
    _append_trace(
        state,
        "update_memory",
        verified=reconciliation["verified"],
        updated=reconciliation["updated"],
        created=reconciliation["created"],
        stale=reconciliation["stale"],
        contradicted=reconciliation["contradicted"],
        skipped_runtime_updates=reconciliation["skipped_runtime_updates"],
        skipped_reasons=reconciliation["skipped_reasons"],
    )
    return {
        "trace": state["trace"],
    }


def _respond_with_trace(state: TaskGraphState) -> TaskGraphState:
    response = {
        "scenario": state["scenario"],
        "final_status": state["final_status"],
    }
    _append_trace(
        state,
        "respond_with_trace",
        final_status=state["final_status"],
    )
    return {"response": response}


def _execute_and_verify_subgoal(
    *,
    state: TaskGraphState,
    subgoal: Subgoal,
    attempt: int,
    verifier: VerificationEngine,
):
    runtime_state = state["runtime_state"]
    target_category = state["parsed_task"].target_object.category

    if subgoal.subgoal_type == SubgoalType.NAVIGATE:
        candidate = _candidate_for_subgoal(state, subgoal)
        viewpoint_id = _candidate_viewpoint_id(candidate)
        execution_result: dict[str, Any]
        if viewpoint_id is None:
            execution_result = {
                "status": "failed",
                "reason": "missing_viewpoint_id",
                "state_delta": {"executor_status": "failed", "last_attempt": attempt},
            }
        else:
            _append_trace(state, "call_vln_navigate", viewpoint_id=viewpoint_id)
            try:
                nav_result = MockVLNAdapter.navigate(state["world"], viewpoint_id)
            except ValueError as exc:
                execution_result = {
                    "status": "failed",
                    "reason": str(exc),
                    "state_delta": {"executor_status": "failed", "last_attempt": attempt},
                }
            else:
                execution_result = nav_result.model_dump()
                if nav_result.arrived:
                    if runtime_state.robot_runtime_state is None:
                        runtime_state.robot_runtime_state = RobotRuntimeState()
                    runtime_state.robot_runtime_state.viewpoint_id = viewpoint_id
                    runtime_state.robot_runtime_state.room_id = nav_result.evidence.get("room_id")

        state["latest_execution_result"] = execution_result
        evidence = build_verification_evidence(
            observation=runtime_state.current_observation,
            robot_runtime_state=runtime_state.robot_runtime_state,
            execution_result=execution_result,
            task_negative_evidence=runtime_state.task_negative_evidence,
        )
        return verifier.evaluate(subgoal.success_conditions, evidence)

    if subgoal.subgoal_type == SubgoalType.OBSERVE:
        candidate = _candidate_for_subgoal(state, subgoal)
        viewpoint_id = _candidate_viewpoint_id(candidate)
        if viewpoint_id is None:
            evidence = build_verification_evidence(
                observation=runtime_state.current_observation,
                robot_runtime_state=runtime_state.robot_runtime_state,
                execution_result={"status": "failed", "reason": "missing_viewpoint_id"},
                task_negative_evidence=runtime_state.task_negative_evidence,
            )
            return verifier.evaluate(subgoal.success_conditions, evidence)

        _append_trace(state, "observe_scene", viewpoint_id=viewpoint_id)
        observation = MockPerceptionAdapter.observe(state["world"], viewpoint_id)
        runtime_state.current_observation = observation
        state["current_observation"] = observation

        if runtime_state.robot_runtime_state is None:
            runtime_state.robot_runtime_state = RobotRuntimeState()
        runtime_state.robot_runtime_state.viewpoint_id = observation.viewpoint_id
        runtime_state.robot_runtime_state.room_id = observation.room_id

        execution_result = {
            "status": "success",
            "viewpoint_id": observation.viewpoint_id,
            "room_id": observation.room_id,
        }
        state["latest_execution_result"] = execution_result

        evidence = build_verification_evidence(
            observation=observation,
            robot_runtime_state=runtime_state.robot_runtime_state,
            execution_result=execution_result,
            task_negative_evidence=runtime_state.task_negative_evidence,
        )
        return verifier.evaluate(subgoal.success_conditions, evidence)

    if subgoal.subgoal_type == SubgoalType.VERIFY_OBJECT_PRESENCE:
        # Stage 11 keeps final goal checks in `final_task_verification` node.
        # For presence subgoal we only verify presence-scoped predicates here.
        subgoal_conditions = [
            item for item in subgoal.success_conditions if item.name != "task_goal_satisfied"
        ] or subgoal.success_conditions
        evidence = build_verification_evidence(
            observation=runtime_state.current_observation,
            robot_runtime_state=runtime_state.robot_runtime_state,
            execution_result=state.get("latest_execution_result"),
            task_negative_evidence=runtime_state.task_negative_evidence,
        )
        return verifier.evaluate(subgoal_conditions, evidence)

    if subgoal.subgoal_type in {SubgoalType.EMBODIED_MANIPULATION, SubgoalType.RETURN_TO_USER}:
        _append_trace(
            state,
            "call_robobrain_planner",
            subgoal_id=subgoal.subgoal_id,
            subgoal_type=subgoal.subgoal_type.value,
        )
        request = EmbodiedSubgoalRequest(
            subgoal=subgoal.model_dump(mode="json"),
            target_object={"category": target_category},
            current_observation=(
                runtime_state.current_observation.model_dump(mode="json")
                if runtime_state.current_observation is not None
                else None
            ),
            constraints={"scenario": state["scenario"]},
            success_conditions=[item.to_list() for item in subgoal.success_conditions],
        )
        plan = FakeRoboBrainClient().plan(request)

        _append_trace(
            state,
            "execute_atomic_plan",
            subgoal_id=subgoal.subgoal_id,
            attempt=attempt,
        )
        failure_rules = (
            state["failure_rules"]
            if subgoal.subgoal_type == SubgoalType.EMBODIED_MANIPULATION
            else []
        )
        executor_result = MockAtomicExecutor.execute(
            plan=plan,
            runtime_state=runtime_state,
            world=state["world"],
            attempt=attempt,
            failure_rules=failure_rules,
        )

        execution_result = dict(executor_result.execution_result)
        if (
            subgoal.subgoal_type == SubgoalType.RETURN_TO_USER
            and executor_result.status == "success"
        ):
            execution_result["near_user"] = True

        if runtime_state.robot_runtime_state is None:
            runtime_state.robot_runtime_state = RobotRuntimeState()

        if subgoal.subgoal_type == SubgoalType.EMBODIED_MANIPULATION:
            if executor_result.status == "success":
                runtime_state.robot_runtime_state.is_holding_object = True
                runtime_state.robot_runtime_state.holding_object_category = target_category
            else:
                runtime_state.robot_runtime_state.is_holding_object = False
                runtime_state.robot_runtime_state.holding_object_category = None

        _update_runtime_progress_from_executor(state, executor_result.evidence)
        _append_runtime_object_updates(runtime_state, execution_result)

        state["latest_execution_result"] = execution_result
        evidence = build_verification_evidence(
            observation=runtime_state.current_observation,
            robot_runtime_state=runtime_state.robot_runtime_state,
            execution_result=execution_result,
            task_negative_evidence=runtime_state.task_negative_evidence,
        )
        return verifier.evaluate(subgoal.success_conditions, evidence)

    # ask_clarification/report_failure subgoals are non-executable and treated as failed here
    evidence = build_verification_evidence(
        observation=runtime_state.current_observation,
        robot_runtime_state=runtime_state.robot_runtime_state,
        execution_result={"status": "failed", "reason": "non_executable_subgoal"},
        task_negative_evidence=runtime_state.task_negative_evidence,
    )
    return verifier.evaluate(subgoal.success_conditions, evidence)


def _candidate_for_subgoal(state: TaskGraphState, subgoal: Subgoal) -> dict[str, Any] | None:
    candidate_id = (
        subgoal.target_memory_id
        or subgoal.candidate_id
        or state["runtime_state"].selected_candidate_id
    )
    if not isinstance(candidate_id, str):
        return None

    ranked_candidates = state["memory_context"].get("ranked_candidates", [])
    for candidate in ranked_candidates:
        memory_id = candidate.get("memory_id")
        if memory_id == candidate_id:
            return candidate

    memory_item = state["memory_store"].get_object_memory(candidate_id)
    if memory_item is None:
        return None
    return {
        "memory_id": memory_item.memory_id,
        "anchor": memory_item.anchor.model_dump(mode="json"),
    }


def _candidate_viewpoint_id(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    anchor = candidate.get("anchor")
    if not isinstance(anchor, dict):
        return None
    viewpoint_id = anchor.get("viewpoint_id")
    return viewpoint_id if isinstance(viewpoint_id, str) and viewpoint_id else None


def _start_subgoal_progress(
    runtime_state: RuntimeState,
    plan: HighLevelPlan,
    subgoal: Subgoal,
    index: int,
) -> None:
    progress = runtime_state.high_level_progress
    if progress is None:
        progress = HighLevelProgress()

    progress.current_subgoal_id = subgoal.subgoal_id
    progress.current_subgoal_type = subgoal.subgoal_type
    progress.pending_subgoal_ids = [item.subgoal_id for item in plan.subgoals[index:]]
    if progress.completed_subgoal_ids is None:
        progress.completed_subgoal_ids = []
    progress.execution_phase = "executing"

    runtime_state.high_level_progress = progress


def _mark_subgoal_completed(
    runtime_state: RuntimeState,
    plan: HighLevelPlan,
    subgoal: Subgoal,
    index: int,
) -> None:
    progress = runtime_state.high_level_progress
    if progress is None:
        progress = HighLevelProgress()

    if subgoal.subgoal_id not in progress.completed_subgoal_ids:
        progress.completed_subgoal_ids.append(subgoal.subgoal_id)

    progress.pending_subgoal_ids = [item.subgoal_id for item in plan.subgoals[index + 1 :]]
    progress.execution_phase = "subgoal_verified"
    runtime_state.high_level_progress = progress


def _update_runtime_progress_from_executor(
    state: TaskGraphState, executor_evidence: dict[str, Any]
) -> None:
    runtime_state = state["runtime_state"]
    runtime_progress = executor_evidence.get("runtime_progress")
    if not isinstance(runtime_progress, dict):
        return

    embodied = runtime_state.embodied_action_progress
    if embodied is None:
        embodied = EmbodiedActionProgress()

    active_skill = runtime_progress.get("active_skill_name")
    if isinstance(active_skill, str):
        embodied.active_skill_name = active_skill

    current_phase = runtime_progress.get("current_action_phase")
    if isinstance(current_phase, str):
        embodied.current_action_phase = current_phase

    flags = runtime_progress.get("local_world_state_flags")
    if isinstance(flags, dict):
        merged_flags = dict(embodied.local_world_state_flags)
        for key, value in flags.items():
            if key in merged_flags and isinstance(value, bool):
                merged_flags[key] = value
        embodied.local_world_state_flags = merged_flags

    embodied.completed_action_phases = list(dict.fromkeys(embodied.completed_action_phases))
    runtime_state.embodied_action_progress = embodied


def _append_runtime_object_updates(
    runtime_state: RuntimeState, execution_result: dict[str, Any]
) -> None:
    updates = execution_result.get("runtime_object_updates_candidate")
    if not isinstance(updates, list):
        return

    for item in updates:
        if not isinstance(item, dict):
            continue
        object_ref = item.get("object_ref")
        source = item.get("source")
        if not isinstance(object_ref, str) or not isinstance(source, str):
            continue

        runtime_state.runtime_object_updates.append(
            RuntimeObjectUpdate(
                object_ref=object_ref,
                source=source,
                reason=item.get("reason") if isinstance(item.get("reason"), str) else None,
            )
        )


def _replan_from_runtime(state: TaskGraphState, *, reason: str) -> None:
    runtime_state = state["runtime_state"]
    if runtime_state.high_level_progress is None:
        runtime_state.high_level_progress = HighLevelProgress()
    runtime_state.high_level_progress.replan_count += 1
    runtime_state.high_level_progress.execution_phase = "replanning"

    parsed_task = state["parsed_task"]
    memory_store = state["memory_store"]

    ranked_candidates = retrieve_candidates(
        parsed_task=parsed_task,
        memory_store=memory_store,
        runtime_state=runtime_state,
    )
    object_memory_hits = [
        item.model_dump()
        for item in memory_store.retrieve_object_memory(
            parsed_task.target_object.category,
            parsed_task.target_object.aliases,
        )
    ]

    if ranked_candidates and runtime_state.selected_candidate_id is None:
        runtime_state.selected_candidate_id = ranked_candidates[0]["memory_id"]

    state["memory_context"] = {
        "ranked_candidates": ranked_candidates,
        "object_memory_hits": object_memory_hits,
        "category_prior_hits": memory_store.retrieve_category_prior(
            parsed_task.target_object.category,
            parsed_task.target_object.aliases,
        ),
        "recent_episodic_summaries": memory_store.retrieve_recent_episodic_summaries(
            parsed_task.target_object.category,
            parsed_task.target_object.aliases,
            limit=5,
        ),
    }
    _append_trace(
        state,
        "retrieve_memory",
        candidate_count=len(ranked_candidates),
        category_prior_count=len(state["memory_context"]["category_prior_hits"]),
        episodic_summary_count=len(state["memory_context"]["recent_episodic_summaries"]),
        selected_candidate_id=runtime_state.selected_candidate_id,
        reason=reason,
    )

    task_context = build_task_context(
        request=state["request"],
        parsed_task=parsed_task,
        runtime_state=runtime_state,
        ranked_candidates=ranked_candidates,
        object_memory_hits=object_memory_hits,
        category_prior_hits=state["memory_context"].get("category_prior_hits"),
        recent_episodic_summaries=state["memory_context"].get("recent_episodic_summaries"),
        constraints=state.get("constraints") or {},
    )
    state["task_context"] = task_context
    _append_trace(
        state,
        "build_task_context",
        candidate_count=len(task_context.ranked_candidates),
        negative_evidence_count=len(task_context.task_negative_evidence),
        reason=reason,
    )

    planner = PlannerService()
    plan = planner.plan(task_context)
    state["plan"] = plan
    _append_planner_diagnostics_trace(state, planner.last_diagnostics, reason=reason)
    _append_trace(
        state,
        "generate_plan",
        plan_id=plan.plan_id,
        subgoal_count=len(plan.subgoals),
        reason=reason,
    )

    _append_trace(state, "validate_plan", plan_id=plan.plan_id, reason=reason)


def _do_reobserve(state: TaskGraphState) -> None:
    runtime_state = state["runtime_state"]
    robot_runtime = runtime_state.robot_runtime_state
    viewpoint_id = robot_runtime.viewpoint_id if robot_runtime is not None else None
    if not isinstance(viewpoint_id, str):
        return

    observation = MockPerceptionAdapter.observe(state["world"], viewpoint_id)
    runtime_state.current_observation = observation
    state["current_observation"] = observation
    _append_trace(state, "observe_scene", viewpoint_id=viewpoint_id, reason="re_observe")


def _scenario_dir(scenario: str) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "data" / "scenarios" / scenario


def _ensure_list_of_dict(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _append_planner_diagnostics_trace(
    state: TaskGraphState,
    diagnostics: dict[str, Any],
    *,
    reason: str | None = None,
) -> None:
    if diagnostics.get("llm_attempted"):
        _append_trace(state, "call_llm_planner", reason=reason)

    planner_error_type = diagnostics.get("planner_error_type")
    planner_error_message = diagnostics.get("planner_error_message")
    if planner_error_type or planner_error_message:
        _append_trace(
            state,
            "llm_planner_error",
            planner_error_type=planner_error_type,
            planner_error_message=planner_error_message,
            reason=reason,
        )

    if diagnostics.get("fallback_used"):
        _append_trace(
            state,
            "llm_planner_fallback",
            planner_mode=diagnostics.get("planner_mode"),
            reason=reason,
        )


def _append_trace(state: TaskGraphState, event: str, **payload: Any) -> None:
    trace = state.setdefault("trace", [])
    trace.append({"event": event, **payload})


__all__ = ["TaskGraphState", "build_task_graph", "run_task_graph"]
