from __future__ import annotations

from typer.testing import CliRunner

from task_brain.cli import app
from task_brain.graph import run_task_graph

_CHECK_MEDICINE_INSTRUCTION = "去桌子那边看看药盒是不是还在。"
_FETCH_CUP_INSTRUCTION = "去厨房找水杯，然后拿给我"

_runner = CliRunner()


def test_quality_gate_core_cli_demos_succeed() -> None:
    cases = [
        ("check_medicine_success", _CHECK_MEDICINE_INSTRUCTION),
        ("check_medicine_stale_recover", _CHECK_MEDICINE_INSTRUCTION),
        ("fetch_cup_retry", _FETCH_CUP_INSTRUCTION),
    ]

    for scenario, instruction in cases:
        result = _runner.invoke(
            app,
            [
                "run",
                "--scenario",
                scenario,
                "--instruction",
                instruction,
            ],
        )
        assert result.exit_code == 0, result.stdout
        assert f"scenario: {scenario}" in result.stdout
        assert "final_status: success" in result.stdout


def test_quality_gate_hardening_scenarios_fail_safely() -> None:
    cases = [
        ("object_not_found", _FETCH_CUP_INSTRUCTION),
        ("distractor_rejected", _FETCH_CUP_INSTRUCTION),
    ]

    for scenario, instruction in cases:
        result = _runner.invoke(
            app,
            [
                "run",
                "--scenario",
                scenario,
                "--instruction",
                instruction,
            ],
        )
        assert result.exit_code != 0
        assert f"scenario: {scenario}" in result.stdout
        assert "final_status: failed" in result.stdout

    distractor_result = _runner.invoke(
        app,
        [
            "run",
            "--scenario",
            "distractor_rejected",
            "--instruction",
            _FETCH_CUP_INSTRUCTION,
        ],
    )
    assert "call_robobrain_planner" not in distractor_result.stdout
    assert "execute_atomic_plan" not in distractor_result.stdout


def test_quality_gate_trace_order_is_stable() -> None:
    result = run_task_graph(
        scenario="check_medicine_success",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )
    events = _trace_events(result)

    assert _first_index(events, "retrieve_memory") < _first_index(events, "generate_plan")
    assert _first_index(events, "validate_plan") < _first_index(events, "execute_subgoal_loop")
    assert _first_index(events, "final_task_verification") < _first_index(
        events, "respond_with_trace"
    )


def test_quality_gate_stale_trace_contains_recovery_signals() -> None:
    result = run_task_graph(
        scenario="check_medicine_stale_recover",
        instruction=_CHECK_MEDICINE_INSTRUCTION,
    )
    events = _trace_events(result)

    assert "write_task_negative_evidence" in events
    assert "recovery_switch_candidate" in events


def test_quality_gate_fetch_retry_trace_contains_retry_signals() -> None:
    result = run_task_graph(
        scenario="fetch_cup_retry",
        instruction=_FETCH_CUP_INSTRUCTION,
    )
    events = _trace_events(result)

    assert "post_action_verification_failed" in events
    assert "recovery_retry_same_subgoal" in events


def _trace_events(result: dict[str, object]) -> list[str]:
    trace = result["trace"]
    assert isinstance(trace, list)
    return [item["event"] for item in trace if isinstance(item, dict)]


def _first_index(events: list[str], item: str) -> int:
    assert item in events
    return events.index(item)
