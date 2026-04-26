"""Stage 01 smoke pipeline for HomeMaster contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from homemaster.contracts import TaskCard
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    STAGE_01_CASE_DIR,
    STAGE_01_RESULTS_DIR,
    ProviderConfig,
    ensure_stage_directories,
    load_provider_config,
)
from homemaster.trace import append_jsonl_event, write_json

DEFAULT_STAGE_01_UTTERANCE = "去桌子那边看看药盒是不是还在。"
STAGE_01_RETRY_INSTRUCTION = """上一次输出没有通过 TaskCard 校验。
请修正为严格 JSON object，只包含 TaskCard schema 中列出的字段。
不要添加额外字段。"""


class Stage01SmokeError(RuntimeError):
    """Raised when the Stage 01 contract smoke fails."""


@dataclass(frozen=True)
class Stage01SmokeResult:
    passed: bool
    provider: dict[str, Any]
    task_card: TaskCard
    checks: dict[str, bool]
    case_dir: Path
    results_dir: Path
    elapsed_ms: float


def build_stage_01_task_card_prompt(utterance: str) -> str:
    return f"""你是 HomeMaster V1.2 的任务理解 smoke 测试组件。

只做一件事：把用户一句话转换成 TaskCard JSON。
必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置。

TaskCard schema:
{{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可以使用中文名或中英别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}}

规则:
- 用户说“看看、还在不在、是否在”时，task_type 使用 "check_presence"。
- 用户说“找、拿给我、取来、送来”时，task_type 使用 "fetch_object"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出 clarification_question。
- 如果不需要澄清，needs_clarification 使用 false，clarification_question 使用 null。
- success_criteria 必须能被后续观察或验证模块判断。
- confidence 使用 0 到 1 之间的小数。

输入:
{{"utterance": "{utterance}"}}

只输出 JSON object:
"""


def run_stage_01_contract_smoke(
    *,
    utterance: str = DEFAULT_STAGE_01_UTTERANCE,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    case_dir: Path = STAGE_01_CASE_DIR,
    results_dir: Path = STAGE_01_RESULTS_DIR,
    max_tokens: int = 1024,
    client: httpx.Client | None = None,
) -> Stage01SmokeResult:
    ensure_stage_directories(case_dir=case_dir, results_dir=results_dir)
    prompt = build_stage_01_task_card_prompt(utterance)
    expected = _expected_payload()

    provider = load_provider_config(config_path, provider_name=provider_name)
    write_json(
        case_dir / "input.json",
        {
            "utterance": utterance,
            "prompt": prompt,
            "provider": provider.public_summary(),
            "schema": "TaskCard",
        },
    )
    write_json(case_dir / "expected.json", expected)

    llm_client = RawJsonLLMClient(provider, client=client)
    attempts: list[dict[str, Any]] = []
    try:
        for attempt_index in range(1, 3):
            attempt_prompt = _stage_01_attempt_prompt(prompt, attempt_index)
            attempt: dict[str, Any] = {
                "attempt": attempt_index,
                "prompt": attempt_prompt,
                "passed": False,
            }
            try:
                response = llm_client.complete_json(
                    attempt_prompt,
                    max_tokens=max_tokens,
                    temperature=0.0,
                )
                task_card = TaskCard.model_validate(response.json_payload)
                checks = validate_stage_01_task_card(task_card)
                passed = all(checks.values())
                attempt.update(
                    {
                        "provider": response.public_summary(),
                        "raw_text": response.content,
                        "json_payload": response.json_payload,
                        "task_card": task_card.model_dump(mode="json"),
                        "checks": checks,
                        "passed": passed,
                    }
                )
                if not passed:
                    attempt["error_type"] = "contract_check_failed"
                    attempt["message"] = "TaskCard did not satisfy Stage 01 expected checks."
                attempts.append(attempt)
                if passed:
                    actual = _stage_01_actual_from_attempt(
                        attempt,
                        attempts=attempts,
                        passed=True,
                    )
                    _write_success_assets(
                        case_dir=case_dir,
                        results_dir=results_dir,
                        provider=provider,
                        utterance=utterance,
                        prompt=prompt,
                        expected=expected,
                        actual=actual,
                        passed=True,
                    )
                    return Stage01SmokeResult(
                        passed=True,
                        provider=provider.public_summary(),
                        task_card=task_card,
                        checks=checks,
                        case_dir=case_dir,
                        results_dir=results_dir,
                        elapsed_ms=response.elapsed_ms,
                    )
            except (LLMClientError, ValidationError, ValueError) as exc:
                attempt.update(
                    {
                        "passed": False,
                        "error_type": getattr(exc, "error_type", type(exc).__name__),
                        "message": str(exc),
                        "raw_text": getattr(exc, "raw_content", None) or "",
                    }
                )
                attempts.append(attempt)

        final_attempt = attempts[-1] if attempts else {}
        _write_failure_assets(
            case_dir=case_dir,
            results_dir=results_dir,
            provider=provider,
            utterance=utterance,
            prompt=prompt,
            expected=expected,
            error_type=str(final_attempt.get("error_type", "stage_01_failed")),
            message=str(final_attempt.get("message", "Stage 01 failed after retry.")),
            attempts=attempts,
        )
        raise Stage01SmokeError(
            "stage 01 contract smoke failed after 2 attempts: "
            f"{final_attempt.get('message', 'unknown error')}"
        )
    finally:
        llm_client.close()

    raise Stage01SmokeError("stage 01 contract smoke failed unexpectedly")


def validate_stage_01_task_card(task_card: TaskCard) -> dict[str, bool]:
    location_hint = task_card.location_hint or ""
    target = task_card.target.casefold()
    return {
        "task_type_is_check_presence": task_card.task_type == "check_presence",
        "target_mentions_medicine_box": any(item in target for item in ("药盒", "药", "medicine")),
        "does_not_need_clarification": task_card.needs_clarification is False,
        "has_success_criteria": bool(task_card.success_criteria),
        "has_location_hint": bool(location_hint.strip()),
    }


def _expected_payload() -> dict[str, Any]:
    return {
        "case_name": "stage_01_llm_contract_smoke",
        "schema": "TaskCard",
        "required_checks": [
            "provider returns parseable JSON object",
            "JSON validates as TaskCard",
            "task_type == check_presence",
            "target mentions 药盒/药/medicine",
            "needs_clarification == false",
            "success_criteria has at least one item",
            "location_hint is a non-empty user-stated hint",
        ],
    }


def _stage_01_attempt_prompt(prompt: str, attempt_index: int) -> str:
    if attempt_index == 1:
        return prompt
    return f"{prompt.rstrip()}\n\n{STAGE_01_RETRY_INSTRUCTION}\n"


def _stage_01_actual_from_attempt(
    attempt: dict[str, Any],
    *,
    attempts: list[dict[str, Any]],
    passed: bool,
) -> dict[str, Any]:
    return {
        "provider": attempt.get("provider", {}),
        "raw_text": attempt.get("raw_text", ""),
        "json_payload": attempt.get("json_payload"),
        "task_card": attempt.get("task_card"),
        "checks": attempt.get("checks"),
        "passed": passed,
        "attempt_count": len(attempts),
        "attempts": attempts,
    }


def _write_success_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    provider: ProviderConfig,
    utterance: str,
    prompt: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    passed: bool,
) -> None:
    write_json(case_dir / "actual.json", actual)
    status = "PASS" if passed else "FAIL"
    _write_result_markdown(
        case_dir / "result.md",
        status=status,
        provider=provider,
        utterance=utterance,
        prompt=prompt,
        expected=expected,
        actual=actual,
        message="TaskCard contract smoke completed.",
        results_dir=results_dir,
    )
    append_jsonl_event(
        results_dir / "llm_samples.jsonl",
        event="stage_01_llm_contract_smoke",
        payload=actual,
    )
    append_jsonl_event(
        results_dir / "trace" / "stage_01_llm_contract_smoke.jsonl",
        event="stage_01_contract_validation",
        payload={
            "status": status,
            "checks": actual["checks"],
            "provider": provider.public_summary(),
        },
    )


def _write_failure_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    provider: ProviderConfig,
    utterance: str,
    prompt: str,
    expected: dict[str, Any],
    error_type: str,
    message: str,
    attempts: list[dict[str, Any]] | None = None,
) -> None:
    attempts = attempts or []
    final_attempt = attempts[-1] if attempts else {}
    payload = {
        "provider": provider.public_summary(),
        "passed": False,
        "error_type": error_type,
        "message": message,
        "raw_text": final_attempt.get("raw_text", ""),
        "json_payload": final_attempt.get("json_payload"),
        "task_card": final_attempt.get("task_card"),
        "checks": final_attempt.get("checks"),
        "attempt_count": len(attempts),
        "attempts": attempts,
    }
    write_json(case_dir / "actual.json", payload)
    _write_result_markdown(
        case_dir / "result.md",
        status="FAIL",
        provider=provider,
        utterance=utterance,
        prompt=prompt,
        expected=expected,
        actual=payload,
        message=f"{error_type}: {message}",
        results_dir=results_dir,
    )
    append_jsonl_event(
        results_dir / "trace" / "stage_01_llm_contract_smoke.jsonl",
        event="stage_01_contract_validation",
        payload=payload,
    )


def _write_result_markdown(
    path: Path,
    *,
    status: str,
    provider: ProviderConfig,
    utterance: str,
    prompt: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
    message: str,
    results_dir: Path,
) -> None:
    raw_text = str(actual.get("raw_text", ""))
    json_payload = actual.get("json_payload")
    task_card = actual.get("task_card")
    checks = actual.get("checks")
    path.write_text(
        "\n".join(
            [
                "# Stage 01 LLM Contract Smoke",
                "",
                f"Status: {status}",
                "",
                "## Summary",
                "",
                f"- Result: {status}",
                f"- Provider: {provider.name}",
                f"- Model: {provider.model}",
                f"- Protocol: {provider.protocol}",
                f"- Logs: {results_dir}",
                f"- Message: {message}",
                f"- Utterance: {utterance}",
                "",
                "## Full Prompt Sent To Mimo",
                "",
                "```text",
                prompt.rstrip(),
                "```",
                "",
                "## Mimo Raw Response",
                "",
                "````json",
                raw_text.rstrip() if raw_text else "(no raw response captured)",
                "````",
                "",
                "## Parsed JSON Payload",
                "",
                "```json",
                _json_block(json_payload),
                "```",
                "",
                "## Validated TaskCard",
                "",
                "```json",
                _json_block(task_card),
                "```",
                "",
                "## Contract Checks",
                "",
                "```json",
                _json_block(checks),
                "```",
                "",
                "## Expected Conditions",
                "",
                "```json",
                _json_block(expected),
                "```",
                "",
                "## Attempts",
                "",
                _attempts_markdown(actual.get("attempts")),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _json_block(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _attempts_markdown(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "(no attempts recorded)"
    sections: list[str] = []
    for attempt in value:
        if not isinstance(attempt, dict):
            continue
        sections.extend(
            [
                f"### Attempt {attempt.get('attempt', '?')}",
                "",
                f"- Passed: {attempt.get('passed', False)}",
                f"- Error Type: {attempt.get('error_type')}",
                f"- Message: {attempt.get('message')}",
                "",
                "#### Prompt",
                "",
                "```text",
                str(attempt.get("prompt", "")).rstrip(),
                "```",
                "",
                "#### Raw Response",
                "",
                "```text",
                str(attempt.get("raw_text", "")).rstrip(),
                "```",
                "",
                "#### Parsed JSON",
                "",
                "```json",
                _json_block(attempt.get("json_payload")),
                "```",
                "",
            ]
        )
    return "\n".join(sections)
