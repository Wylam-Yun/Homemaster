from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from homemaster.frontdoor import (
    MimoTaskUnderstandingProvider,
    TaskUnderstandingError,
    TaskUnderstandingInput,
    build_task_understanding_prompt,
    stage_02_case_expectations,
    understand_task,
)
from homemaster.runtime import load_provider_config


def _write_config(path: Path) -> None:
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


def _anthropic_response(payload: dict[str, Any] | str) -> httpx.Response:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return httpx.Response(
        status_code=200,
        json={"content": [{"type": "text", "text": text}]},
    )


def _task_card_payload(
    *,
    task_type: str = "check_presence",
    target: str = "药盒",
    delivery_target: str | None = None,
    location_hint: str | None = "桌子那边",
    needs_clarification: bool = False,
    clarification_question: str | None = None,
) -> dict[str, Any]:
    return {
        "task_type": task_type,
        "target": target,
        "delivery_target": delivery_target,
        "location_hint": location_hint,
        "success_criteria": ["后续观察可以验证任务是否完成"],
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
        "confidence": 0.9,
    }


def test_frontdoor_prompt_contains_full_context() -> None:
    prompt = build_task_understanding_prompt(
        TaskUnderstandingInput(
            utterance="去厨房看看水杯还在不在",
            user_id="elder-001",
            source="voice",
            recent_task_summary="上一轮找过药盒",
        )
    )

    assert "LLM-first 任务理解入口" in prompt
    assert "不负责检索记忆" in prompt
    assert "不要编造用户没有说过的真实位置" in prompt
    assert '"utterance": "去厨房看看水杯还在不在"' in prompt
    assert '"user_id": "elder-001"' in prompt
    assert '"source": "voice"' in prompt
    assert '"recent_task_summary": "上一轮找过药盒"' in prompt
    assert "只输出 JSON object" in prompt


def test_understanding_accepts_mock_mimo_check_presence(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    _write_config(config_path)
    provider = load_provider_config(config_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_response(_task_card_payload())

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = MimoTaskUnderstandingProvider(
            provider,
            results_dir=tmp_path / "results",
            client=http_client,
        ).understand(
            TaskUnderstandingInput(utterance="去桌子那边看看药盒是不是还在。"),
            case_name="check_medicine_task_card",
            expected=stage_02_case_expectations()["check_medicine_task_card"],
        )

    assert result.passed is True
    assert result.task_card.task_type == "check_presence"
    assert result.task_card.target == "药盒"
    assert (result.case_dir / "result.md").is_file()
    result_md = (result.case_dir / "result.md").read_text(encoding="utf-8")
    assert "## Prompt Attempt 1" in result_md
    assert "## Mimo Raw Response Attempt 1" in result_md
    assert '"target": "药盒"' in result_md


def test_understanding_accepts_mock_mimo_fetch_object(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    _write_config(config_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_response(
            _task_card_payload(
                task_type="fetch_object",
                target="水杯",
                delivery_target="user",
                location_hint="厨房",
            )
        )

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = understand_task(
            "去厨房找水杯，然后拿给我",
            case_name="fetch_cup_task_card",
            expected=stage_02_case_expectations()["fetch_cup_task_card"],
            config_path=config_path,
            client=http_client,
        )

    assert result.passed is True
    assert result.task_card.task_type == "fetch_object"
    assert result.task_card.delivery_target == "user"
    assert result.checks["location_hint_matches"] is True


def test_understanding_retries_once_after_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    _write_config(config_path)
    responses = iter(
        [
            _anthropic_response("this is not json"),
            _anthropic_response(_task_card_payload()),
        ]
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return next(responses)

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = understand_task(
            "去桌子那边看看药盒是不是还在。",
            case_name="check_medicine_task_card",
            expected=stage_02_case_expectations()["check_medicine_task_card"],
            config_path=config_path,
            client=http_client,
        )

    assert calls == 2
    assert result.retry_count == 1
    result_md = (result.case_dir / "result.md").read_text(encoding="utf-8")
    assert "Prompt Attempt 2" in result_md
    assert "上一次输出没有通过 TaskCard 校验" in result_md


def test_understanding_fails_after_second_invalid_output(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    _write_config(config_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_response("not json")

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        with pytest.raises(TaskUnderstandingError) as exc_info:
            understand_task(
                "去桌子那边看看药盒是不是还在。",
                case_name="check_medicine_task_card",
                expected=stage_02_case_expectations()["check_medicine_task_card"],
                config_path=config_path,
                client=http_client,
            )

    assert exc_info.value.error_type == "provider_response_error"


def test_frontdoor_debug_assets_do_not_contain_secrets(tmp_path: Path) -> None:
    config_path = tmp_path / "nvidia_api_config.json"
    _write_config(config_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return _anthropic_response(_task_card_payload())

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = understand_task(
            "去桌子那边看看药盒是不是还在。",
            case_name="check_medicine_task_card",
            expected=stage_02_case_expectations()["check_medicine_task_card"],
            config_path=config_path,
            client=http_client,
        )

    for path in list(result.case_dir.glob("*")) + list(result.results_dir.glob("**/*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "secret-one" not in text
        assert "Bearer" not in text
        assert "x-api-key" not in text
        assert "Authorization" not in text
        assert "api_keys" not in text
        assert "sk-" not in text
