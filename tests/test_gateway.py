from __future__ import annotations

from typing import Any

from task_brain.gateway import GatewayMessage, handle_message


def test_gateway_message_converts_to_task_request_and_invokes_graph() -> None:
    calls: list[dict[str, Any]] = []

    def fake_runner(*, scenario: str, instruction: str, user_id: str) -> dict[str, Any]:
        calls.append(
            {
                "scenario": scenario,
                "instruction": instruction,
                "user_id": user_id,
            }
        )
        return {
            "final_status": "success",
            "trace": [
                {"event": "input_instruction"},
                {"event": "retrieve_memory"},
                {"event": "generate_plan"},
                {"event": "respond_with_trace"},
            ],
        }

    response = handle_message(
        message={
            "platform": "feishu",
            "user_id": "u-allow",
            "session_id": "s-1",
            "text": "去桌子那边看看药盒是不是还在。",
        },
        scenario="check_medicine_success",
        allowlisted_users={"u-allow"},
        graph_runner=fake_runner,
    )

    assert response.accepted is True
    assert response.status == "processed"
    assert response.final_status == "success"
    assert response.task_request is not None
    assert response.task_request.source == "gateway:feishu"
    assert response.task_request.user_id == "u-allow"
    assert response.task_request.request_id == "gw-s-1"
    assert calls == [
        {
            "scenario": "check_medicine_success",
            "instruction": "去桌子那边看看药盒是不是还在。",
            "user_id": "u-allow",
        }
    ]


def test_gateway_rejects_non_allowlisted_user() -> None:
    called = {"runner": False}

    def fake_runner(*, scenario: str, instruction: str, user_id: str) -> dict[str, Any]:
        _ = (scenario, instruction, user_id)
        called["runner"] = True
        return {"final_status": "success", "trace": []}

    response = handle_message(
        message=GatewayMessage(
            platform="feishu",
            user_id="u-blocked",
            session_id="s-2",
            text="去厨房找水杯，然后拿给我",
        ),
        scenario="fetch_cup_retry",
        allowlisted_users={"u-allow"},
        graph_runner=fake_runner,
    )

    assert response.accepted is False
    assert response.status == "rejected"
    assert response.reason == "user_not_allowlisted"
    assert "not allowlisted" in response.trace_summary
    assert called["runner"] is False


def test_gateway_summarizes_failed_trace_with_recovery_clues() -> None:
    def fake_runner(*, scenario: str, instruction: str, user_id: str) -> dict[str, Any]:
        _ = (scenario, instruction, user_id)
        return {
            "final_status": "failed",
            "trace": [
                {"event": "execute_subgoal_loop"},
                {"event": "post_action_verification_failed"},
                {"event": "analyze_failure"},
                {"event": "decide_recovery"},
                {"event": "recovery_report_failure"},
                {"event": "respond_with_trace"},
            ],
        }

    response = handle_message(
        message={
            "platform": "feishu",
            "user_id": "u-allow",
            "session_id": "s-3",
            "text": "去厨房找水杯，然后拿给我",
        },
        scenario="object_not_found",
        allowlisted_users={"u-allow"},
        graph_runner=fake_runner,
    )

    assert response.accepted is True
    assert response.final_status == "failed"
    assert "final_status=failed" in response.trace_summary
    assert "failure_events=" in response.trace_summary
    assert "analyze_failure" in response.trace_summary
    assert "decide_recovery" in response.trace_summary
    assert "recovery_report_failure" in response.trace_summary


def test_gateway_does_not_create_second_runtime_state_source() -> None:
    def fake_runner(*, scenario: str, instruction: str, user_id: str) -> dict[str, Any]:
        _ = (scenario, instruction, user_id)
        return {
            "final_status": "success",
            "trace": [{"event": "respond_with_trace"}],
            "runtime_state": {"high_level_progress": {"current_subgoal_id": "sg-1"}},
            "task_progress": {"unexpected": True},
        }

    response = handle_message(
        message={
            "platform": "feishu",
            "user_id": "u-allow",
            "session_id": "s-4",
            "text": "看看药盒还在不在",
        },
        scenario="check_medicine_success",
        allowlisted_users={"u-allow"},
        graph_runner=fake_runner,
    )
    dump = response.model_dump()

    assert "runtime_state" not in dump
    assert "task_progress" not in dump
    assert "current_object_changes" not in dump
    assert "embodied_progress" not in dump


def test_gateway_does_not_expose_or_mutate_long_term_memory_fields() -> None:
    def fake_runner(*, scenario: str, instruction: str, user_id: str) -> dict[str, Any]:
        _ = (scenario, instruction, user_id)
        return {
            "final_status": "success",
            "trace": [{"event": "update_memory", "updated": 1}],
            "memory_store": {"object_memory": [{"memory_id": "mem-1"}]},
        }

    response = handle_message(
        message={
            "platform": "feishu",
            "user_id": "u-allow",
            "session_id": "s-5",
            "text": "去桌子那边看看药盒是不是还在。",
        },
        scenario="check_medicine_success",
        allowlisted_users={"u-allow"},
        graph_runner=fake_runner,
    )
    dump = response.model_dump()

    assert response.accepted is True
    assert "memory_store" not in dump
    assert "object_memory" not in response.trace_summary
    assert response.trace_events[0]["event"] == "update_memory"
