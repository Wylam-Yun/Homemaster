from __future__ import annotations

from task_brain.graph import build_task_graph, run_task_graph

_CHECK_MEDICINE_INSTRUCTION = "去桌子那边看看药盒是不是还在。"
_FETCH_CUP_INSTRUCTION = "去厨房找水杯，然后拿给我"


def test_build_task_graph_returns_invokable_langgraph() -> None:
    graph = build_task_graph()
    assert hasattr(graph, "invoke")


def test_trace_order_retrieves_memory_before_plan_generation() -> None:
    result = run_task_graph(
        scenario="check_medicine_success",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )

    events = _trace_events(result)
    assert _first_index(events, "retrieve_memory") < _first_index(events, "generate_plan")


def test_check_medicine_success_trace_order() -> None:
    result = run_task_graph(
        scenario="check_medicine_success",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )

    events = _trace_events(result)
    assert result["final_status"] == "success"
    assert _first_index(events, "validate_plan") < _first_index(events, "execute_subgoal_loop")
    assert _first_index(events, "final_task_verification") < _first_index(
        events, "respond_with_trace"
    )


def test_check_medicine_stale_recover_switches_candidate() -> None:
    result = run_task_graph(
        scenario="check_medicine_stale_recover",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )

    events = _trace_events(result)
    assert result["final_status"] == "success"
    assert "write_task_negative_evidence" in events
    assert "recovery_switch_candidate" in events
    assert result["runtime_state"].task_negative_evidence


def test_fetch_cup_retry_uses_robobrain_and_retries() -> None:
    result = run_task_graph(
        scenario="fetch_cup_retry",
        instruction=_FETCH_CUP_INSTRUCTION,
    )

    events = _trace_events(result)
    assert result["final_status"] == "success"
    assert "call_robobrain_planner" in events
    assert events.count("execute_atomic_plan") >= 2
    assert "post_action_verification_failed" in events
    assert "recovery_retry_same_subgoal" in events


def test_object_not_found_candidate_exhausted_reports_failure() -> None:
    result = run_task_graph(
        scenario="object_not_found",
        instruction=_FETCH_CUP_INSTRUCTION,
    )

    events = _trace_events(result)
    assert result["final_status"] == "failed"
    assert "recovery_report_failure" in events


def test_distractor_object_rejected_does_not_pick_wrong_object() -> None:
    result = run_task_graph(
        scenario="distractor_rejected",
        instruction=_FETCH_CUP_INSTRUCTION,
    )

    events = _trace_events(result)
    assert result["final_status"] == "failed"
    assert "call_robobrain_planner" not in events
    assert "execute_atomic_plan" not in events


def test_final_status_success_requires_final_task_verification() -> None:
    result = run_task_graph(
        scenario="fetch_cup_retry",
        instruction=_FETCH_CUP_INSTRUCTION,
    )

    events = _trace_events(result)
    assert result["final_status"] == "success"
    assert _first_index(events, "final_task_verification") < _first_index(
        events, "respond_with_trace"
    )


def test_runtime_progress_updates_only_inside_runtime_state() -> None:
    result = run_task_graph(
        scenario="fetch_cup_retry",
        instruction=_FETCH_CUP_INSTRUCTION,
    )

    runtime_state = result["runtime_state"]
    task_context = result["task_context"]

    assert runtime_state.high_level_progress is not None
    assert runtime_state.embodied_action_progress is not None
    assert isinstance(runtime_state.runtime_object_updates, list)

    assert "high_level_progress" not in result
    assert "embodied_action_progress" not in result
    assert "runtime_object_updates" not in result

    context_dump = task_context.model_dump()
    assert "task_progress" not in context_dump
    assert "current_object_changes" not in context_dump
    assert "embodied_progress" not in context_dump


def _trace_events(result: dict[str, object]) -> list[str]:
    trace = result["trace"]
    assert isinstance(trace, list)
    return [item["event"] for item in trace if isinstance(item, dict)]


def _first_index(events: list[str], item: str) -> int:
    assert item in events
    return events.index(item)
