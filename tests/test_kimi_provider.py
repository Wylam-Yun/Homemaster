from __future__ import annotations

import json
import os
from pathlib import Path

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


def _dual_protocol_handler(
    openai_handler: callable | None = None,
    anthropic_handler: callable | None = None,
) -> callable:
    """Helper: returns 404 for Anthropic (forcing fallback to OpenAI) unless overridden."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            if anthropic_handler:
                return anthropic_handler(request)
            return httpx.Response(status_code=404)
        elif request.url.path.endswith("/chat/completions"):
            if openai_handler:
                return openai_handler(request)
            return httpx.Response(status_code=500)
        return httpx.Response(status_code=500)
    return handler


def test_provider_reads_config_file_with_three_keys_and_fallbacks(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    config_path.write_text(
        json.dumps(
            {
                "base_url": "https://integrate.api.nvidia.com",
                "model": "moonshotai/kimi-k2.5",
                "api_keys": ["key-one", "key-two", "key-three"],
            }
        ),
        encoding="utf-8",
    )

    attempts: list[str] = []

    def openai_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        attempts.append(auth)
        if auth.endswith("key-one") or auth.endswith("key-two"):
            return httpx.Response(status_code=401, json={"error": {"message": "invalid key"}})
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "plan_id": "plan-from-third-key",
                                    "intent": "check_object_presence",
                                    "subgoals": [
                                        {
                                            "subgoal_id": "sg-1",
                                            "subgoal_type": "ask_clarification",
                                            "description": "Need clarification.",
                                            "success_conditions": [
                                                {"name": "clarification_received", "args": []}
                                            ],
                                        }
                                    ],
                                    "memory_grounding": [],
                                    "candidate_grounding": [],
                                    "notes": "from_config_fallback",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(_dual_protocol_handler(openai_handler=openai_handler)),
        base_url="https://integrate.api.nvidia.com",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_config_file(config_path=config_path, client=client)
        plan = provider.generate_plan(prompt="generate plan")

    assert isinstance(plan, HighLevelPlan)
    assert plan.plan_id == "plan-from-third-key"
    assert attempts == ["Bearer key-one", "Bearer key-two", "Bearer key-three"]


def test_provider_reads_three_groups_with_different_base_url_and_model(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "base_url": "https://provider-one.example",
                        "model": "model-one",
                        "api_keys": ["key-one"],
                    },
                    {
                        "base_url": "https://provider-two.example",
                        "model": "model-two",
                        "api_keys": ["key-two"],
                    },
                    {
                        "base_url": "https://provider-three.example",
                        "model": "model-three",
                        "api_keys": ["key-three"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    attempts: list[tuple[str, str, str]] = []

    def openai_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        body = json.loads(request.content.decode("utf-8"))
        model = body["model"]
        host = request.url.host or ""
        attempts.append((host, model, auth))

        if auth.endswith("key-one"):
            return httpx.Response(status_code=401, json={"error": {"message": "invalid key"}})
        if auth.endswith("key-two"):
            return httpx.Response(status_code=500, json={"error": {"message": "server error"}})
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "plan_id": "plan-group-fallback",
                                    "intent": "check_object_presence",
                                    "subgoals": [
                                        {
                                            "subgoal_id": "sg-1",
                                            "subgoal_type": "ask_clarification",
                                            "description": "Need clarification.",
                                            "success_conditions": [
                                                {"name": "clarification_received", "args": []}
                                            ],
                                        }
                                    ],
                                    "memory_grounding": [],
                                    "candidate_grounding": [],
                                    "notes": "from_group_fallback",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(_dual_protocol_handler(openai_handler=openai_handler)),
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_config_file(config_path=config_path, client=client)
        plan = provider.generate_plan(prompt="generate plan")

    assert isinstance(plan, HighLevelPlan)
    assert plan.plan_id == "plan-group-fallback"
    assert attempts == [
        ("provider-one.example", "model-one", "Bearer key-one"),
        ("provider-two.example", "model-two", "Bearer key-two"),
        ("provider-three.example", "model-three", "Bearer key-three"),
    ]


def test_explicit_config_path_takes_precedence_over_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit_config = tmp_path / "explicit.json"
    explicit_config.write_text(
        json.dumps(
            {
                "base_url": "https://provider-explicit.example",
                "model": "model-explicit",
                "api_keys": ["key-explicit"],
            }
        ),
        encoding="utf-8",
    )
    env_override_config = tmp_path / "env_override.json"
    env_override_config.write_text(
        json.dumps(
            {
                "base_url": "https://provider-env.example",
                "model": "model-env",
                "api_keys": ["key-env"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TASK_BRAIN_API_CONFIG_PATH", str(env_override_config))

    attempts: list[str] = []

    def openai_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        attempts.append(auth)
        if auth != "Bearer key-explicit":
            return httpx.Response(status_code=401, json={"error": {"message": "unexpected key"}})
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "plan_id": "plan-explicit-precedence",
                                    "intent": "check_object_presence",
                                    "subgoals": [
                                        {
                                            "subgoal_id": "sg-1",
                                            "subgoal_type": "ask_clarification",
                                            "description": "Need clarification.",
                                            "success_conditions": [
                                                {"name": "clarification_received", "args": []}
                                            ],
                                        }
                                    ],
                                    "memory_grounding": [],
                                    "candidate_grounding": [],
                                    "notes": "explicit_wins",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(_dual_protocol_handler(openai_handler=openai_handler)),
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_config_file(config_path=explicit_config, client=client)
        plan = provider.generate_plan(prompt="generate plan")

    assert isinstance(plan, HighLevelPlan)
    assert plan.plan_id == "plan-explicit-precedence"
    assert attempts == ["Bearer key-explicit"]


def test_default_config_path_uses_env_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_override_config = tmp_path / "env_override.json"
    env_override_config.write_text(
        json.dumps(
            {
                "base_url": "https://provider-env-only.example",
                "model": "model-env",
                "api_keys": ["key-env"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TASK_BRAIN_API_CONFIG_PATH", str(env_override_config))

    attempts: list[str] = []

    def openai_handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        attempts.append(auth)
        if auth != "Bearer key-env":
            return httpx.Response(status_code=401, json={"error": {"message": "unexpected key"}})
        return httpx.Response(
            status_code=200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "plan_id": "plan-env-override",
                                    "intent": "check_object_presence",
                                    "subgoals": [
                                        {
                                            "subgoal_id": "sg-1",
                                            "subgoal_type": "ask_clarification",
                                            "description": "Need clarification.",
                                            "success_conditions": [
                                                {"name": "clarification_received", "args": []}
                                            ],
                                        }
                                    ],
                                    "memory_grounding": [],
                                    "candidate_grounding": [],
                                    "notes": "env_override_used",
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(_dual_protocol_handler(openai_handler=openai_handler)),
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_config_file(client=client)
        plan = provider.generate_plan(prompt="generate plan")

    assert isinstance(plan, HighLevelPlan)
    assert plan.plan_id == "plan-env-override"
    assert attempts == ["Bearer key-env"]


def test_provider_config_placeholders_raise_missing_key(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    config_path.write_text(
        json.dumps(
            {
                "providers": [
                    {
                        "base_url": "<NVIDIA_BASE_URL_1>",
                        "model": "<NVIDIA_MODEL_1>",
                        "api_keys": ["<NVIDIA_API_KEY_1>"],
                    },
                    {
                        "base_url": "<NVIDIA_BASE_URL_2>",
                        "model": "<NVIDIA_MODEL_2>",
                        "api_keys": ["<NVIDIA_API_KEY_2>"],
                    },
                    {
                        "base_url": "<NVIDIA_BASE_URL_3>",
                        "model": "<NVIDIA_MODEL_3>",
                        "api_keys": ["<NVIDIA_API_KEY_3>"],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(KimiProviderAuthError) as exc_info:
        KimiPlanProvider.from_config_file(config_path=config_path)

    assert exc_info.value.error_type == "auth_missing_key"
    assert "placeholders" in str(exc_info.value)


def test_provider_reads_api_key_from_env_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    provider = KimiPlanProvider.from_env()
    try:
        assert provider.base_url == "https://integrate.api.nvidia.com"
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
        base_url="https://integrate.api.nvidia.com",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_env(client=client)
        with pytest.raises(KimiProviderNetworkError) as exc_info:
            provider.generate_plan(prompt="generate plan")

    assert exc_info.value.error_type in {"network_error", "timeout"}


def test_provider_http_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(status_code=500, json={"error": {"message": "internal failure"}})

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://integrate.api.nvidia.com",
        timeout=5.0,
    ) as client:
        provider = KimiPlanProvider.from_env(client=client)
        with pytest.raises((KimiProviderResponseError, KimiProviderError)) as exc_info:
            provider.generate_plan(prompt="generate plan")

    # 当两种协议都返回500时，最终是all_protocols_failed
    # 但每个协议的500错误本身是http_error
    assert exc_info.value.error_type in {"http_error", "all_protocols_failed"}
    assert "internal failure" in str(exc_info.value)


def test_provider_schema_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-stage15")

    def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        # Anthropic和OpenAI都返回非JSON内容
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                status_code=200,
                json={
                    "content": [{"type": "text", "text": "this is not a plan json"}],
                },
            )
        return httpx.Response(
            status_code=200,
            json={"choices": [{"message": {"content": "this is not a plan json"}}]},
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="https://integrate.api.nvidia.com",
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

    def openai_handler(request: httpx.Request) -> httpx.Response:
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
        transport=httpx.MockTransport(_dual_protocol_handler(openai_handler=openai_handler)),
        base_url="https://integrate.api.nvidia.com",
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