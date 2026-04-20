from __future__ import annotations

import json
import os

import httpx
import pytest

from task_brain.domain import HighLevelPlan
from task_brain.llm import KimiPlanProvider, KimiProviderError


def test_provider_reads_api_key_from_env_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    provider = KimiPlanProvider.from_env()
    try:
        assert provider.base_url == "https://integrate.api.nvidia.com/v1"
        assert provider.model == "moonshotai/kimi-k2.5"
        assert provider.api_key_env == "NVIDIA_API_KEY"
    finally:
        provider.close()


def test_fake_chat_completion_can_be_converted_to_high_level_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    expected_plan = {
        "plan_id": "plan-kimi-fake-1",
        "intent": "check_object_presence",
        "subgoals": [
            {
                "subgoal_id": "sg-1",
                "subgoal_type": "ask_clarification",
                "description": "Need clarification.",
                "success_conditions": [
                    {
                        "name": "clarification_received",
                        "args": [],
                    }
                ],
            }
        ],
        "memory_grounding": [],
        "candidate_grounding": [],
        "notes": "fake_response",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"].startswith("Bearer ")
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(expected_plan, ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://integrate.api.nvidia.com/v1",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_env(client=client)
        plan = provider.generate_plan(prompt="generate a safe plan")

    assert isinstance(plan, HighLevelPlan)
    assert plan.plan_id == "plan-kimi-fake-1"
    assert plan.subgoals[0].subgoal_type.value == "ask_clarification"


@pytest.mark.live_api
def test_live_kimi_smoke_returns_plan_or_structured_error() -> None:
    if not os.getenv("NVIDIA_API_KEY"):
        pytest.skip("NVIDIA_API_KEY is not set")

    provider = KimiPlanProvider.from_env()
    try:
        prompt = (
            "Return JSON only for a HighLevelPlan with intent check_object_presence, "
            "one ask_clarification subgoal, empty grounding arrays, and notes='live_smoke'."
        )
        try:
            plan = provider.generate_plan(prompt=prompt)
        except KimiProviderError as exc:
            assert isinstance(exc.error_type, str)
            assert exc.error_type
            assert str(exc)
        else:
            assert isinstance(plan, HighLevelPlan)
            assert plan.intent.value in {"check_object_presence", "fetch_object"}
    finally:
        provider.close()
