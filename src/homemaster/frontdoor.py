"""LLM-first task understanding entrypoint for HomeMaster Stage 02."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from homemaster.contracts import TaskCard
from homemaster.llm_client import LLMClientError, RawJsonLLMClient
from homemaster.runtime import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_PROVIDER_NAME,
    LLM_CASE_ROOT,
    TEST_RESULTS_ROOT,
    ProviderConfig,
    ensure_stage_directories,
    load_provider_config,
)
from homemaster.trace import append_jsonl_event, write_json

STAGE_02_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_02"
STAGE_02_CASE_ROOT = LLM_CASE_ROOT / "stage_02"
DEFAULT_UNDERSTAND_CASE_NAME = "manual_task_understanding"
RETRY_INSTRUCTION = """上一次输出没有通过 TaskCard 校验。
请修正为严格 JSON object，只包含 TaskCard schema 中列出的字段。
不要添加额外字段。"""


class TaskUnderstandingError(RuntimeError):
    """Raised when task understanding cannot produce an accepted TaskCard."""

    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class TaskUnderstandingInput:
    utterance: str
    user_id: str | None = None
    source: str | None = None
    recent_task_summary: str | None = None

    def as_context(self) -> dict[str, Any]:
        return {
            "utterance": self.utterance,
            "user_id": self.user_id,
            "source": self.source,
            "recent_task_summary": self.recent_task_summary,
        }


@dataclass(frozen=True)
class TaskUnderstandingResult:
    passed: bool
    case_name: str
    provider: dict[str, Any]
    task_card: TaskCard
    checks: dict[str, bool]
    prompt: str
    raw_response: str
    parsed_json: dict[str, Any]
    case_dir: Path
    results_dir: Path
    retry_count: int
    elapsed_ms: float


class TaskUnderstandingProvider(Protocol):
    def understand(
        self,
        input_data: TaskUnderstandingInput,
        *,
        case_name: str = DEFAULT_UNDERSTAND_CASE_NAME,
        expected: dict[str, Any] | None = None,
    ) -> TaskUnderstandingResult:
        """Convert one user utterance into a validated TaskCard."""


class MimoTaskUnderstandingProvider:
    """Mimo-backed task understanding provider for Stage 02."""

    def __init__(
        self,
        provider: ProviderConfig,
        *,
        results_dir: Path = STAGE_02_RESULTS_DIR,
        client: httpx.Client | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self._provider = provider
        self._results_dir = results_dir
        self._client = client
        self._max_tokens = max_tokens

    def understand(
        self,
        input_data: TaskUnderstandingInput,
        *,
        case_name: str = DEFAULT_UNDERSTAND_CASE_NAME,
        expected: dict[str, Any] | None = None,
    ) -> TaskUnderstandingResult:
        case_dir = STAGE_02_CASE_ROOT / case_name
        expected_payload = expected or minimal_task_card_expectation(case_name=case_name)
        ensure_stage_directories(case_dir=case_dir, results_dir=self._results_dir)

        first_prompt = build_task_understanding_prompt(input_data)
        write_json(
            case_dir / "input.json",
            {
                "case_name": case_name,
                "input": input_data.as_context(),
                "prompt": first_prompt,
                "provider": self._provider.public_summary(),
                "schema": "TaskCard",
            },
        )
        write_json(case_dir / "expected.json", expected_payload)

        llm_client = RawJsonLLMClient(self._provider, client=self._client)
        attempts: list[dict[str, Any]] = []
        try:
            for attempt_index in (1, 2):
                prompt = build_task_understanding_prompt(
                    input_data,
                    retry_feedback=RETRY_INSTRUCTION if attempt_index == 2 else None,
                )
                attempt_record = {
                    "attempt_index": attempt_index,
                    "prompt": prompt,
                }
                try:
                    response = llm_client.complete_json(
                        prompt,
                        max_tokens=self._max_tokens,
                        temperature=0.0,
                    )
                    task_card = TaskCard.model_validate(response.json_payload)
                except (LLMClientError, ValidationError, ValueError) as exc:
                    attempt_record.update(
                        {
                            "passed": False,
                            "error_type": getattr(exc, "error_type", type(exc).__name__),
                            "message": str(exc),
                            "raw_text": getattr(exc, "raw_content", None),
                        }
                    )
                    attempts.append(attempt_record)
                    if attempt_index == 2:
                        return _fail_understanding(
                            case_name=case_name,
                            case_dir=case_dir,
                            results_dir=self._results_dir,
                            provider=self._provider,
                            input_data=input_data,
                            expected=expected_payload,
                            attempts=attempts,
                            error_type=attempt_record["error_type"],
                            message=attempt_record["message"],
                        )
                    continue

                checks = validate_task_card_expectations(task_card, expected_payload)
                passed = all(checks.values())
                attempt_record.update(
                    {
                        "passed": passed,
                        "provider": response.public_summary(),
                        "raw_text": response.content,
                        "json_payload": response.json_payload,
                        "task_card": task_card.model_dump(mode="json"),
                        "checks": checks,
                    }
                )
                attempts.append(attempt_record)

                if not passed:
                    return _fail_understanding(
                        case_name=case_name,
                        case_dir=case_dir,
                        results_dir=self._results_dir,
                        provider=self._provider,
                        input_data=input_data,
                        expected=expected_payload,
                        attempts=attempts,
                        error_type="expectation_failed",
                        message="TaskCard did not match expected case fields.",
                    )

                actual = {
                    "case_name": case_name,
                    "passed": True,
                    "provider": response.public_summary(),
                    "input": input_data.as_context(),
                    "attempts": attempts,
                    "retry_count": attempt_index - 1,
                    "raw_text": response.content,
                    "json_payload": response.json_payload,
                    "task_card": task_card.model_dump(mode="json"),
                    "checks": checks,
                }
                _write_understanding_assets(
                    case_dir=case_dir,
                    results_dir=self._results_dir,
                    provider=self._provider,
                    input_data=input_data,
                    expected=expected_payload,
                    actual=actual,
                    status="PASS",
                    message="Task understanding completed.",
                )
                return TaskUnderstandingResult(
                    passed=True,
                    case_name=case_name,
                    provider=response.public_summary(),
                    task_card=task_card,
                    checks=checks,
                    prompt=prompt,
                    raw_response=response.content,
                    parsed_json=response.json_payload,
                    case_dir=case_dir,
                    results_dir=self._results_dir,
                    retry_count=attempt_index - 1,
                    elapsed_ms=response.elapsed_ms,
                )
        finally:
            llm_client.close()

        raise TaskUnderstandingError(
            error_type="unexpected_understanding_exit",
            message="Task understanding exited without a result.",
        )


def understand_task(
    utterance: str,
    *,
    user_id: str | None = None,
    source: str | None = None,
    recent_task_summary: str | None = None,
    case_name: str = DEFAULT_UNDERSTAND_CASE_NAME,
    expected: dict[str, Any] | None = None,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    client: httpx.Client | None = None,
) -> TaskUnderstandingResult:
    provider = load_provider_config(config_path, provider_name=provider_name)
    task_input = TaskUnderstandingInput(
        utterance=utterance,
        user_id=user_id,
        source=source,
        recent_task_summary=recent_task_summary,
    )
    task_provider = MimoTaskUnderstandingProvider(provider, client=client)
    return task_provider.understand(task_input, case_name=case_name, expected=expected)


def build_task_understanding_prompt(
    input_data: TaskUnderstandingInput,
    *,
    retry_feedback: str | None = None,
) -> str:
    context_json = json.dumps(input_data.as_context(), ensure_ascii=False, indent=2)
    retry_section = f"\n\n{retry_feedback}" if retry_feedback else ""
    return f"""你是 HomeMaster V1.2 的 LLM-first 任务理解入口。

目标：把用户自然语言指令转换成一个 TaskCard JSON。
你只负责理解任务，不负责检索记忆、不负责选择候选地点、不负责规划机器人动作。

必须只输出一个 JSON object。
不要输出 Markdown。
不要输出解释。
不要输出代码块。
不要输出思考过程。
不要编造用户没有说过的真实位置、物品状态或执行结果。

TaskCard schema:
{{
  "task_type": "check_presence | fetch_object | unknown",
  "target": "非空字符串，目标物名称；可包含中文名或稳定英文别名",
  "delivery_target": "字符串或 null；只有取物/交付任务需要，默认用户为 user",
  "location_hint": "字符串或 null；只记录用户明说的位置提示",
  "success_criteria": ["至少一个可验证的完成条件"],
  "needs_clarification": true,
  "clarification_question": "字符串或 null",
  "confidence": 0.0
}}

判断规则:
- 用户要求“看看、确认、还在不在、是否在”某物时，task_type 使用 "check_presence"。
- 用户要求“找、拿、取、拿给我、送来”某物时，task_type 使用 "fetch_object"。
- 取物并交给说话人时，delivery_target 使用 "user"。
- 如果目标物不明确，task_type 使用 "unknown"，needs_clarification 使用 true，
  并给出澄清问题。
- 如果目标物不明确，target 使用 "unknown_object" 或 "不明确目标"；不要猜药盒、水杯等具体物品。
- 如果用户只说“那个”“这个”“它”且没有具体目标物，必须使用 task_type="unknown"，
  target="unknown_object"，needs_clarification=true，并提出“请问您想确认哪个物品？”这类澄清问题。
- 如果目标物明确，needs_clarification 使用 false，clarification_question 使用 null。
- location_hint 只放用户明说的位置，例如“桌子那边”“厨房”；不要把它当成已验证位置。
- success_criteria 必须描述后续 observation/verification 可以判断的完成条件。
- confidence 使用 0 到 1 之间的小数。

输入上下文:
{context_json}

只输出 JSON object:{retry_section}
"""


def minimal_task_card_expectation(*, case_name: str) -> dict[str, Any]:
    return {
        "case_name": case_name,
        "schema": "TaskCard",
        "required_checks": [
            "provider returns parseable JSON object",
            "JSON validates as TaskCard",
            "success_criteria has at least one item",
        ],
    }


def stage_02_case_expectations() -> dict[str, dict[str, Any]]:
    return {
        "check_medicine_task_card": {
            "case_name": "check_medicine_task_card",
            "utterance": "去桌子那边看看药盒是不是还在。",
            "expected_task_type": "check_presence",
            "target_keywords": ["药盒", "药", "medicine"],
            "location_keywords": ["桌子"],
            "needs_clarification": False,
            "required_checks": [
                "task_type == check_presence",
                "target mentions 药盒/药/medicine",
                "needs_clarification == false",
                "location_hint contains 桌子",
            ],
        },
        "fetch_cup_task_card": {
            "case_name": "fetch_cup_task_card",
            "utterance": "去厨房找水杯，然后拿给我",
            "expected_task_type": "fetch_object",
            "target_keywords": ["水杯", "杯", "cup"],
            "location_keywords": ["厨房"],
            "delivery_target": "user",
            "needs_clarification": False,
            "required_checks": [
                "task_type == fetch_object",
                "target mentions 水杯/cup",
                "delivery_target == user",
                "location_hint contains 厨房",
            ],
        },
        "ambiguous_task_card": {
            "case_name": "ambiguous_task_card",
            "utterance": "帮我看看那个还在不在",
            "needs_clarification": True,
            "clarification_question_non_empty": True,
            "task_type_unknown_or_target_unclear": True,
            "forbidden_target_keywords": ["药盒", "水杯", "medicine", "cup"],
            "required_checks": [
                "needs_clarification == true",
                "clarification_question is non-empty",
                "task_type is unknown or target remains unclear",
                "target does not guess a concrete object",
            ],
        },
        "kitchen_hint_task_card": {
            "case_name": "kitchen_hint_task_card",
            "utterance": "去厨房看看水杯还在不在",
            "expected_task_type": "check_presence",
            "target_keywords": ["水杯", "杯", "cup"],
            "location_keywords": ["厨房"],
            "needs_clarification": False,
            "required_checks": [
                "task_type == check_presence",
                "target mentions 水杯/cup",
                "location_hint contains 厨房",
                "needs_clarification == false",
            ],
        },
    }


def run_stage_02_case(
    case_name: str,
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    provider_name: str = DEFAULT_PROVIDER_NAME,
    client: httpx.Client | None = None,
) -> TaskUnderstandingResult:
    cases = stage_02_case_expectations()
    if case_name not in cases:
        raise TaskUnderstandingError(
            error_type="unknown_case",
            message=f"unknown Stage 02 case: {case_name}",
        )
    expected = cases[case_name]
    return understand_task(
        str(expected["utterance"]),
        case_name=case_name,
        expected=expected,
        config_path=config_path,
        provider_name=provider_name,
        client=client,
    )


def validate_task_card_expectations(
    task_card: TaskCard,
    expected: dict[str, Any],
) -> dict[str, bool]:
    target = task_card.target.casefold()
    location_hint = (task_card.location_hint or "").casefold()
    checks: dict[str, bool] = {
        "schema_valid": True,
        "has_success_criteria": bool(task_card.success_criteria),
    }

    if "expected_task_type" in expected:
        checks["task_type_matches"] = task_card.task_type == expected["expected_task_type"]
    if "target_keywords" in expected:
        checks["target_matches"] = _contains_any(target, expected["target_keywords"])
    if "location_keywords" in expected:
        checks["location_hint_matches"] = _contains_any(
            location_hint,
            expected["location_keywords"],
        )
    if "delivery_target" in expected:
        checks["delivery_target_matches"] = task_card.delivery_target == expected["delivery_target"]
    if "needs_clarification" in expected:
        checks["needs_clarification_matches"] = (
            task_card.needs_clarification is expected["needs_clarification"]
        )
    if expected.get("clarification_question_non_empty"):
        checks["clarification_question_non_empty"] = bool(
            (task_card.clarification_question or "").strip()
        )
    if expected.get("task_type_unknown_or_target_unclear"):
        checks["task_type_unknown_or_target_unclear"] = (
            task_card.task_type == "unknown"
            or _contains_any(target, ["unknown", "unclear", "不明确", "未知", "那个"])
        )
    if "forbidden_target_keywords" in expected:
        checks["no_forbidden_target_guess"] = not _contains_any(
            target,
            expected["forbidden_target_keywords"],
        )
    return checks


def _fail_understanding(
    *,
    case_name: str,
    case_dir: Path,
    results_dir: Path,
    provider: ProviderConfig,
    input_data: TaskUnderstandingInput,
    expected: dict[str, Any],
    attempts: list[dict[str, Any]],
    error_type: str,
    message: str,
) -> TaskUnderstandingResult:
    actual = {
        "case_name": case_name,
        "passed": False,
        "provider": provider.public_summary(),
        "input": input_data.as_context(),
        "attempts": attempts,
        "retry_count": max(len(attempts) - 1, 0),
        "error_type": error_type,
        "message": message,
    }
    _write_understanding_assets(
        case_dir=case_dir,
        results_dir=results_dir,
        provider=provider,
        input_data=input_data,
        expected=expected,
        actual=actual,
        status="FAIL",
        message=f"{error_type}: {message}",
    )
    raise TaskUnderstandingError(error_type=error_type, message=message)


def _write_understanding_assets(
    *,
    case_dir: Path,
    results_dir: Path,
    provider: ProviderConfig,
    input_data: TaskUnderstandingInput,
    expected: dict[str, Any],
    actual: dict[str, Any],
    status: str,
    message: str,
) -> None:
    write_json(case_dir / "actual.json", actual)
    _write_understanding_markdown(
        case_dir / "result.md",
        case_name=str(actual["case_name"]),
        status=status,
        provider=provider,
        input_data=input_data,
        expected=expected,
        actual=actual,
        message=message,
        results_dir=results_dir,
    )
    append_jsonl_event(
        results_dir / "llm_samples.jsonl",
        event="stage_02_task_understanding",
        payload=actual,
    )
    append_jsonl_event(
        results_dir / "trace" / f"{actual['case_name']}.jsonl",
        event="stage_02_task_understanding",
        payload={
            "case_name": actual["case_name"],
            "status": status,
            "checks": actual.get("checks"),
            "retry_count": actual.get("retry_count", 0),
            "provider": provider.public_summary(),
        },
    )


def _write_understanding_markdown(
    path: Path,
    *,
    case_name: str,
    status: str,
    provider: ProviderConfig,
    input_data: TaskUnderstandingInput,
    expected: dict[str, Any],
    actual: dict[str, Any],
    message: str,
    results_dir: Path,
) -> None:
    lines = [
        "# Stage 02 Task Understanding",
        "",
        "## Summary",
        "",
        f"- Result: {status}",
        f"- Case: {case_name}",
        f"- Provider: {provider.name}",
        f"- Model: {provider.model}",
        f"- Protocol: {provider.protocol}",
        f"- Logs: {results_dir}",
        f"- Message: {message}",
        f"- Utterance: {input_data.utterance}",
        f"- Retry Count: {actual.get('retry_count', 0)}",
        "",
        "## Request Context",
        "",
        "```json",
        _json_block(input_data.as_context()),
        "```",
        "",
        "## Expected Conditions",
        "",
        "```json",
        _json_block(expected),
        "```",
        "",
    ]

    attempts = actual.get("attempts")
    if isinstance(attempts, list):
        for attempt in attempts:
            attempt_index = attempt.get("attempt_index", "?")
            lines.extend(
                [
                    f"## Prompt Attempt {attempt_index}",
                    "",
                    "```text",
                    str(attempt.get("prompt", "")).rstrip(),
                    "```",
                    "",
                    f"## Mimo Raw Response Attempt {attempt_index}",
                    "",
                    "````json",
                    str(attempt.get("raw_text") or "(no raw response captured)").rstrip(),
                    "````",
                    "",
                    f"## Parsed JSON Payload Attempt {attempt_index}",
                    "",
                    "```json",
                    _json_block(attempt.get("json_payload")),
                    "```",
                    "",
                    f"## Validated TaskCard Attempt {attempt_index}",
                    "",
                    "```json",
                    _json_block(attempt.get("task_card")),
                    "```",
                    "",
                ]
            )
            if attempt.get("error_type"):
                lines.extend(
                    [
                        f"## Error Attempt {attempt_index}",
                        "",
                        "```json",
                        _json_block(
                            {
                                "error_type": attempt.get("error_type"),
                                "message": attempt.get("message"),
                            }
                        ),
                        "```",
                        "",
                    ]
                )

    lines.extend(
        [
            "## Final Contract Checks",
            "",
            "```json",
            _json_block(actual.get("checks")),
            "```",
            "",
            "## Final TaskCard",
            "",
            "```json",
            _json_block(actual.get("task_card")),
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _contains_any(value: str, needles: Any) -> bool:
    if not isinstance(needles, list):
        return False
    return any(str(item).casefold() in value for item in needles)


def _json_block(value: Any) -> str:
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
