from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from homemaster.pipeline import (
    Stage01SmokeError,
    build_stage_01_task_card_prompt,
    run_stage_01_contract_smoke,
)


def test_stage_01_prompt_contains_task_card_contract() -> None:
    prompt = build_stage_01_task_card_prompt("去桌子那边看看药盒是不是还在。")

    assert "TaskCard schema" in prompt
    assert '"task_type": "check_presence | fetch_object | unknown"' in prompt
    assert '{"utterance": "去桌子那边看看药盒是不是还在。"}' in prompt
    assert "只输出 JSON object" in prompt


def test_stage_01_contract_smoke_writes_debug_assets_with_mock_llm(tmp_path: Path) -> None:
    config_path = tmp_path / "api_config.json"
    case_dir = tmp_path / "case"
    results_dir = tmp_path / "results"
    config_path.write_text(
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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "task_type": "check_presence",
                                "target": "药盒",
                                "delivery_target": None,
                                "location_hint": "桌子那边",
                                "success_criteria": ["确认药盒是否还在桌子附近"],
                                "needs_clarification": False,
                                "clarification_question": None,
                                "confidence": 0.9,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = run_stage_01_contract_smoke(
            config_path=config_path,
            case_dir=case_dir,
            results_dir=results_dir,
            client=http_client,
        )

    assert result.passed is True
    assert (case_dir / "input.json").is_file()
    assert (case_dir / "expected.json").is_file()
    assert (case_dir / "actual.json").is_file()
    assert (case_dir / "result.md").is_file()
    assert (results_dir / "llm_samples.jsonl").is_file()

    actual_text = (case_dir / "actual.json").read_text(encoding="utf-8")
    result_text = (case_dir / "result.md").read_text(encoding="utf-8")
    assert "secret-one" not in actual_text
    assert "mimo-v2-pro" in actual_text
    assert "## Full Prompt Sent To Mimo" in result_text
    assert "## Mimo Raw Response" in result_text
    assert "去桌子那边看看药盒是不是还在。" in result_text
    assert '"task_type": "check_presence"' in result_text


def test_stage_01_retries_once_after_non_json_output(tmp_path: Path) -> None:
    config_path = tmp_path / "api_config.json"
    case_dir = tmp_path / "case"
    results_dir = tmp_path / "results"
    config_path.write_text(
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
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        requests.append(body["messages"][0]["content"])
        if len(requests) == 1:
            return httpx.Response(
                status_code=200,
                json={"content": [{"type": "text", "text": "我看到了药盒，但这不是 JSON"}]},
            )
        return httpx.Response(
            status_code=200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "task_type": "check_presence",
                                "target": "药盒",
                                "delivery_target": None,
                                "location_hint": "桌子那边",
                                "success_criteria": ["确认药盒是否还在桌子附近"],
                                "needs_clarification": False,
                                "clarification_question": None,
                                "confidence": 0.9,
                            },
                            ensure_ascii=False,
                        ),
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        result = run_stage_01_contract_smoke(
            config_path=config_path,
            case_dir=case_dir,
            results_dir=results_dir,
            client=http_client,
        )

    assert result.passed is True
    assert len(requests) == 2
    assert "上一次输出没有通过 TaskCard 校验" in requests[1]
    actual = json.loads((case_dir / "actual.json").read_text(encoding="utf-8"))
    assert actual["attempt_count"] == 2
    assert actual["attempts"][0]["passed"] is False
    assert actual["attempts"][1]["passed"] is True
    report = (case_dir / "result.md").read_text(encoding="utf-8")
    assert "Attempt 1" in report
    assert "Attempt 2" in report
    assert "我看到了药盒" in report


def test_stage_01_failure_report_contains_attempt_raw_response(tmp_path: Path) -> None:
    config_path = tmp_path / "api_config.json"
    case_dir = tmp_path / "case"
    results_dir = tmp_path / "results"
    config_path.write_text(
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

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={"content": [{"type": "text", "text": "不是 JSON 的回复"}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0) as http_client:
        with pytest.raises(Stage01SmokeError):
            run_stage_01_contract_smoke(
                config_path=config_path,
                case_dir=case_dir,
                results_dir=results_dir,
                client=http_client,
            )

    actual = json.loads((case_dir / "actual.json").read_text(encoding="utf-8"))
    assert actual["passed"] is False
    assert actual["attempt_count"] == 2
    assert actual["attempts"][0]["raw_text"] == "不是 JSON 的回复"
    assert actual["attempts"][1]["raw_text"] == "不是 JSON 的回复"
    report = (case_dir / "result.md").read_text(encoding="utf-8")
    assert "Status: FAIL" in report
    assert "不是 JSON 的回复" in report
    assert "(no raw response captured)" not in report
