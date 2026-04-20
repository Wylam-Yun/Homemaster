from __future__ import annotations

import json
import os

import httpx
import pytest

from task_brain.domain import HighLevelPlan
from task_brain.llm import (
    KimiPlanProvider,
    KimiProviderAuthError,
    KimiProviderError,
    KimiProviderNetworkError,
    KimiProviderResponseError,
    KimiProviderSchemaError,
)


def test_provider_reads_api_key_from_env_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    provider = KimiPlanProvider.from_env()
    try:
        assert provider.base_url == "https://integrate.api.nvidia.com/v1"
        assert provider.model == "moonshotai/kimi-k2.5"
        assert provider.api_key_env == "NVIDIA_API_KEY"
    finally:
        provider.close()


def test_provider_missing_key_raises_structured_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(KimiProviderAuthError) as exc_info:
        KimiPlanProvider.from_env()

    assert exc_info.value.error_type == "auth_missing_key"
    assert "NVIDIA_API_KEY" in str(exc_info.value)


def test_provider_network_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network break", request=request)

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://integrate.api.nvidia.com/v1",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_env(client=client)
        with pytest.raises(KimiProviderNetworkError) as exc_info:
            provider.generate_plan(prompt="generate plan")

    assert exc_info.value.error_type == "network_error"


def test_provider_http_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(status_code=500, json={"error": {"message": "internal failure"}})

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://integrate.api.nvidia.com/v1",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_env(client=client)
        with pytest.raises(KimiProviderResponseError) as exc_info:
            provider.generate_plan(prompt="generate plan")

    assert exc_info.value.error_type == "http_error"
    assert "internal failure" in str(exc_info.value)


def test_provider_schema_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": "this is not a plan json"}}]},
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://integrate.api.nvidia.com/v1",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_env(client=client)
        with pytest.raises(KimiProviderSchemaError) as exc_info:
            provider.generate_plan(prompt="generate plan")

    assert exc_info.value.error_type == "response_not_plan_json"


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
def test_live_kimi_smoke_succeeds_within_three_attempts() -> None:
    if not os.getenv("NVIDIA_API_KEY"):
        pytest.skip("NVIDIA_API_KEY is not set")

    prompt = (
        "Return JSON only. Do not include markdown or prose. "
        "Follow this exact schema and field names:\n"
        "{"
        "\"plan_id\":\"plan-live-smoke\","
        "\"intent\":\"check_object_presence\","
        "\"subgoals\":[{"
        "\"subgoal_id\":\"sg-1\","
        "\"subgoal_type\":\"ask_clarification\","
        "\"description\":\"Need clarification\","
        "\"success_conditions\":[{\"name\":\"clarification_received\",\"args\":[]}]"
        "}],"
        "\"memory_grounding\":[],"
        "\"candidate_grounding\":[],"
        "\"notes\":\"live_smoke\""
        "}"
    )
    errors: list[str] = []

    for attempt in range(1, 4):
        provider = KimiPlanProvider.from_env()
        try:
            plan = provider.generate_plan(prompt=prompt)
        except KimiProviderError as exc:
            assert isinstance(exc.error_type, str)
            assert exc.error_type
            assert str(exc)
            errors.append(f"attempt={attempt}:{exc.error_type}")
        else:
            assert isinstance(plan, HighLevelPlan)
            assert plan.plan_id
            assert plan.subgoals
            assert plan.intent.value in {"check_object_presence", "fetch_object"}
            return
        finally:
            provider.close()

    pytest.fail("live_api failed after 3 attempts: " + "; ".join(errors))
