from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from homemaster.contracts import ExecutionState, PlanningContext, Subtask, SubtaskRuntimeState
from homemaster.runtime import load_provider_config
from homemaster.skill_selector import (
    StepDecisionGenerationError,
    build_step_decision_prompt,
    generate_step_decision,
)


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "api_config.json"
    path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "name": "Mimo",
                        "base_url": "https://mimo.example/anthropic",
                        "model": "mimo-v2-pro",
                        "api_keys": ["secret-one"],
                        "protocol": "anthropic",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _subtask() -> Subtask:
    return Subtask(
        id="find_cup",
        intent="找到水杯",
        target_object="水杯",
        room_hint="厨房",
        success_criteria=["观察到水杯"],
    )


def test_step_decision_prompt_contains_subtask_state_and_skill_manifest() -> None:
    prompt = build_step_decision_prompt(
        _subtask(),
        ExecutionState(
            current_subtask_id="find_cup",
            subtasks=[SubtaskRuntimeState(subtask_id="find_cup")],
        ),
        PlanningContext(
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
        ),
    )

    assert "找到水杯" in prompt
    assert "ExecutionState" in prompt
    assert '"navigation"' in prompt
    assert '"operation"' in prompt
    assert '"verification"' not in prompt
    assert "只能选择 navigation 或 operation" in prompt


def test_generate_step_decision_retries_after_invalid_json(tmp_path: Path) -> None:
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(
                status_code=200,
                json={"content": [{"type": "text", "text": "不是 JSON"}]},
            )
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "subtask_id": "find_cup",
                                "selected_skill": "navigation",
                                "skill_input": {
                                    "goal_type": "find_object",
                                    "target_object": "水杯",
                                    "subtask_id": "find_cup",
                                    "subtask_intent": "找到水杯",
                                },
                                "expected_result": "观察到水杯",
                                "reason": "需要先找到目标物",
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    provider = load_provider_config(_config_path(tmp_path), provider_name="Mimo")
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        result = generate_step_decision(
            _subtask(),
            ExecutionState(subtasks=[SubtaskRuntimeState(subtask_id="find_cup")]),
            PlanningContext(
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
            ),
            provider,
            client=client,
        )

    assert result.decision.selected_skill == "navigation"
    assert len(result.attempts) == 2
    assert "上一次输出没有通过 StepDecision 校验" in requests[1]["messages"][0]["content"]


def test_generate_step_decision_rejects_manual_verification(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "subtask_id": "find_cup",
                                "selected_skill": "verification",
                                "skill_input": {"scope": "subtask"},
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    provider = load_provider_config(_config_path(tmp_path), provider_name="Mimo")
    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as client:
        with pytest.raises(StepDecisionGenerationError) as exc_info:
            generate_step_decision(
                _subtask(),
                ExecutionState(subtasks=[SubtaskRuntimeState(subtask_id="find_cup")]),
                PlanningContext(
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
                ),
                provider,
                client=client,
            )

    assert exc_info.value.error_type in {"step_schema_error", "skill_input_error"}
