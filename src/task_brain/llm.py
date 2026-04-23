"""NVIDIA Kimi LLM provider for Phase B high-level planning."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from task_brain.domain import HighLevelPlan
from task_brain.logger import get_logger

logger = get_logger("llm")


class KimiProviderError(RuntimeError):
    """Base provider error carrying a stable error type."""

    def __init__(self, *, error_type: str, message: str) -> None:
        self.error_type = error_type
        self.message = message
        super().__init__(message)


class KimiProviderAuthError(KimiProviderError):
    """Raised when API key is missing or rejected."""


class KimiProviderNetworkError(KimiProviderError):
    """Raised for transport/network failures."""


class KimiProviderResponseError(KimiProviderError):
    """Raised for malformed or non-success responses."""


class KimiProviderSchemaError(KimiProviderError):
    """Raised when completion cannot be validated as HighLevelPlan."""


class KimiPlanProvider:
    """LLM provider with OpenAI/Anthropic compatibility and target fallback."""

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com"
    DEFAULT_MODEL = "moonshotai/kimi-k2.5"
    DEFAULT_API_KEY_ENV = "NVIDIA_API_KEY"
    DEFAULT_CONFIG_PATH = "config/nvidia_api_config.json"
    CONFIG_PATH_ENV = "TASK_BRAIN_API_CONFIG_PATH"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
        api_targets: list[dict[str, str]] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 20.0,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        client: httpx.Client | None = None,
    ) -> None:
        configured_targets = _normalize_api_targets(api_targets or [])
        if not configured_targets:
            configured_keys = _normalize_api_keys(api_keys or [])
            if api_key:
                configured_keys.extend(_normalize_api_keys([api_key]))
            configured_keys = _dedupe_preserve_order(configured_keys)
            configured_targets = [
                {
                    "base_url": base_url.rstrip("/"),
                    "model": model,
                    "api_key": key,
                    "protocol": "openai",
                }
                for key in configured_keys
            ]

        if not configured_targets:
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"missing API keys from config/env '{api_key_env}'",
            )

        self._api_targets = configured_targets
        self._api_key_env = api_key_env
        self._base_url = configured_targets[0]["base_url"]
        self._model = configured_targets[0]["model"]
        self._timeout_s = timeout_s
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self._timeout_s)

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_s: float = 20.0,
        api_key_env: str = DEFAULT_API_KEY_ENV,
        client: httpx.Client | None = None,
    ) -> KimiPlanProvider:
        """Build provider from local environment only."""
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"missing API key in env '{api_key_env}'",
            )
        return cls(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout_s=timeout_s,
            api_key_env=api_key_env,
            client=client,
        )

    @classmethod
    def from_config_file(
        cls,
        *,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        timeout_s: float = 20.0,
        client: httpx.Client | None = None,
    ) -> KimiPlanProvider:
        """Build provider from repository config file.

        Expected JSON shape:
        {
          "base_url": "...",
          "model": "...",
          "api_keys": ["key-1", "key-2", "key-3"]
        }
        """
        env_override = os.getenv(cls.CONFIG_PATH_ENV)
        uses_default_path = Path(str(config_path)) == Path(cls.DEFAULT_CONFIG_PATH)
        effective_config_path: str | Path = config_path
        if env_override and uses_default_path:
            effective_config_path = env_override
        config_file = Path(effective_config_path)
        if not config_file.is_absolute():
            repo_root = Path(__file__).resolve().parents[2]
            config_file = repo_root / config_file
        if not config_file.exists():
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"missing API config file: {config_file}",
            )

        try:
            payload = json.loads(config_file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"invalid API config JSON: {config_file}",
            ) from exc

        if not isinstance(payload, dict):
            raise KimiProviderAuthError(
                error_type="auth_missing_key",
                message=f"API config must be an object: {config_file}",
            )

        targets = _targets_from_grouped_config(payload)
        if targets:
            return cls(
                api_targets=targets,
                timeout_s=timeout_s,
                client=client,
            )

        base_url = _as_non_empty_str(payload.get("base_url")) or cls.DEFAULT_BASE_URL
        model = _as_non_empty_str(payload.get("model")) or cls.DEFAULT_MODEL
        protocol = _normalize_protocol(payload.get("protocol"), base_url)
        keys = _normalize_api_keys(payload.get("api_keys"))
        if keys:
            return cls(
                api_targets=[
                    {
                        "base_url": base_url,
                        "model": model,
                        "api_key": key,
                        "protocol": protocol,
                    }
                    for key in keys
                ],
                timeout_s=timeout_s,
                client=client,
            )

        raise KimiProviderAuthError(
            error_type="auth_missing_key",
            message=(
                "api targets are missing or placeholders in config file: "
                f"{config_file}"
            ),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_key_env(self) -> str:
        return self._api_key_env

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def generate_plan(self, *, prompt: str) -> HighLevelPlan:
        """Call Kimi and validate response into ``HighLevelPlan``.

        Multiple API targets are tried sequentially. The first successful
        response wins; otherwise an aggregated provider error is raised.
        """
        logger.start_task(f"Generating plan with {len(self._api_targets)} API targets")

        errors: list[str] = []
        for index, target in enumerate(self._api_targets, start=1):
            logger.info(
                f"Trying target {index}/{len(self._api_targets)}",
                model=target["model"],
                base_url=target["base_url"][:50] + "..." if len(target["base_url"]) > 50 else target["base_url"],
            )
            try:
                plan = self._generate_plan_with_target(prompt=prompt, target=target)
                logger.complete_task(success=True, message="Plan generated successfully")
                return plan
            except KimiProviderError as exc:
                if len(self._api_targets) == 1:
                    logger.complete_task(success=False, message=f"Failed: {exc.error_type}")
                    raise
                errors.append(f"target#{index}:{exc.error_type}")
                logger.info(f"Target {index} failed", error_type=exc.error_type)

        logger.complete_task(success=False, message="All targets failed")
        raise KimiProviderError(
            error_type="all_targets_failed",
            message="all configured API targets failed: " + "; ".join(errors),
        )

    def _generate_plan_with_target(
        self,
        *,
        prompt: str,
        target: dict[str, str],
    ) -> HighLevelPlan:
        """尝试单个target，根据配置决定协议优先级"""
        base_url = target["base_url"]
        model = target["model"]
        api_key = target["api_key"]

        # 保存原timeout，方法结束后恢复；复用client连接池避免重复TCP/TLS握手
        saved_timeout = self._client.timeout
        # 放宽超时：connect 10s, read 45s (考虑网络波动和模型推理时间)
        self._client.timeout = httpx.Timeout(connect=10.0, read=45.0, write=15.0, pool=10.0)
        try:

            # 从target获取指定的协议
            specified_protocol = target.get("protocol", "").lower()

            # 决定协议尝试顺序 - 即使指定了协议，也尝试fallback到另一个
            if specified_protocol == "anthropic":
                protocols_to_try = ["anthropic", "openai"]  # anthropic优先，但失败时试openai
            elif specified_protocol == "openai":
                protocols_to_try = ["openai", "anthropic"]  # openai优先，但失败时试anthropic
            else:
                # 未指定则两个都试，但根据URL判断优先级
                if "anthropic" in base_url.lower():
                    protocols_to_try = ["anthropic", "openai"]
                else:
                    protocols_to_try = ["openai", "anthropic"]

            last_error = None

            for protocol in protocols_to_try:
                logger.debug(f"Trying {protocol} protocol", base_url=base_url)
                try:
                    # 构建请求
                    if protocol == "anthropic":
                        completion_url = f"{base_url}/v1/messages"
                        headers = {
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                            "content-type": "application/json",
                        }
                        payload = {
                            "model": model,
                            "temperature": 0.0,
                            "max_tokens": 512,
                            "system": (
                                "You are a high-level task planner. "
                                "Return JSON only that validates as HighLevelPlan. "
                                "The JSON must have exactly these fields: "
                                "plan_id (string), intent (string from [check_object_presence, fetch_object]), "
                                "subgoals (array of objects with: subgoal_id, subgoal_type from "
                                "[navigate, observe, verify_object_presence, embodied_manipulation, "
                                "return_to_user, report_failure, ask_clarification], "
                                "description, success_conditions as array of {name:string, args:string[]}), "
                                "memory_grounding (array of strings), candidate_grounding (array of strings), "
                                "notes (string). "
                                "DO NOT add any extra fields like target_location, target_anchor, anchor_id, etc."
                            ),
                            "messages": [{"role": "user", "content": prompt}],
                        }
                    else:  # openai
                        completion_url = f"{base_url}/v1/chat/completions"
                        headers = {
                            "Authorization": f"Bearer {api_key}",
                            "Content-Type": "application/json",
                        }
                        payload = {
                            "model": model,
                            "temperature": 0.0,
                            "messages": [
                                {
                                    "role": "system",
                                    "content": (
                                        "You are a high-level task planner. "
                                        "Return JSON only that validates as HighLevelPlan. "
                                        "The JSON must have exactly these fields: "
                                        "plan_id (string), intent (string from [check_object_presence, fetch_object]), "
                                        "subgoals (array of objects with: subgoal_id, subgoal_type from "
                                        "[navigate, observe, verify_object_presence, embodied_manipulation, "
                                        "return_to_user, report_failure, ask_clarification], "
                                        "description, success_conditions as array of {name:string, args:string[]}), "
                                        "memory_grounding (array of strings), candidate_grounding (array of strings), "
                                        "notes (string). "
                                        "DO NOT add any extra fields like target_location, target_anchor, anchor_id, etc."
                                    ),
                                },
                                {"role": "user", "content": prompt},
                            ],
                        }

                    # 使用传入的client或复用自建client
                    if self._client and not self._owns_client:
                        # 使用传入的client（测试用）
                        response = self._client.post(completion_url, headers=headers, json=payload)
                    else:
                        # 复用self._client（连接池），超时已在上面设置
                        response = self._client.post(completion_url, headers=headers, json=payload)

                    # 处理响应
                    if response.status_code == 404:
                        # 404可能是协议不匹配或路径错误，尝试另一个协议
                        logger.debug(f"{protocol} protocol got 404, trying next protocol")
                        last_error = KimiProviderResponseError(
                            error_type="http_error",
                            message=f"{protocol} protocol got 404"
                        )
                        continue

                    if response.status_code in {401, 403}:
                        # 认证错误也可能是协议不匹配（header格式不对），尝试另一个协议
                        logger.debug(f"{protocol} protocol got {response.status_code}, trying next protocol")
                        last_error = KimiProviderAuthError(
                            error_type="auth_rejected",
                            message=f"auth rejected with status={response.status_code}",
                        )
                        # 不立即raise，尝试下一个协议
                        if protocol == protocols_to_try[-1]:
                            raise last_error
                        continue

                    if response.status_code >= 400:
                        error_message = _extract_error_message(response)
                        logger.debug(f"{protocol} protocol got {response.status_code}, trying next protocol")
                        last_error = KimiProviderResponseError(
                            error_type="http_error",
                            message=f"http status={response.status_code}: {error_message}",
                        )
                        # HTTP错误也可能是协议问题，尝试下一个协议
                        if protocol == protocols_to_try[-1]:
                            raise last_error
                        continue

                    try:
                        body = response.json()
                    except ValueError as exc:
                        raise KimiProviderResponseError(
                            error_type="response_not_json",
                            message="kimi response body is not valid JSON",
                        ) from exc

                    # 解析响应内容
                    if protocol == "anthropic":
                        content = _extract_anthropic_message_content(body)
                    else:
                        content = _extract_message_content(body)
                    plan_payload = _extract_json_payload(content)

                    try:
                        return HighLevelPlan.model_validate(plan_payload, strict=False)
                    except ValidationError as exc:
                        logger.error(f"Schema validation failed", payload=json.dumps(plan_payload)[:200] if plan_payload else "None")
                        raise KimiProviderSchemaError(
                            error_type="schema_validation_error",
                            message=f"llm plan schema validation failed: {exc}",
                        ) from exc

                except httpx.TimeoutException as exc:
                    logger.info(f"{protocol} protocol timed out, trying next protocol")
                    last_error = KimiProviderNetworkError(
                        error_type="timeout",
                        message=f"{protocol} protocol timed out for {base_url}"
                    )
                    # 超时也尝试下一个协议（可能另一个协议的endpoint更快）
                    if protocol == protocols_to_try[-1]:
                        raise last_error from exc
                    continue

                except httpx.RequestError as exc:
                    logger.info(f"{protocol} protocol network error, trying next protocol")
                    last_error = KimiProviderNetworkError(
                        error_type="network_error",
                        message=f"request failed with {protocol}: {exc}",
                    )
                    if protocol == protocols_to_try[-1]:
                        raise last_error from exc
                    continue

                except KimiProviderSchemaError:
                    # Schema错误说明API调用成功但返回格式不对，不要重试其他协议
                    raise

                except KimiProviderResponseError as exc:
                    last_error = exc
                    if protocol == protocols_to_try[-1]:
                        raise
                    continue

            # 所有协议都失败了
            if last_error:
                raise last_error
            else:
                raise KimiProviderError(
                    error_type="all_protocols_failed",
                    message="All protocols failed for target"
                )
        finally:
            self._client.timeout = saved_timeout


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip() or "unknown error"

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return response.text.strip() or "unknown error"


def _extract_message_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise KimiProviderResponseError(
            error_type="response_missing_choices",
            message="kimi response missing choices",
        )

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise KimiProviderResponseError(
            error_type="response_missing_message",
            message="kimi response missing message payload",
        )

    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content

    # Some compatible APIs may return list[parts].
    if isinstance(content, list):
        text_chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text_chunks.append(text)
        joined = "\n".join(text_chunks).strip()
        if joined:
            return joined

    raise KimiProviderResponseError(
        error_type="response_missing_content",
        message="kimi response missing textual content",
    )


def _extract_anthropic_message_content(body: dict[str, Any]) -> str:
    content = body.get("content")
    if isinstance(content, str) and content.strip():
        return content

    if isinstance(content, list):
        text_chunks: list[str] = []
        thinking_chunks: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            text = item.get("text")
            if item_type == "text" and isinstance(text, str) and text.strip():
                text_chunks.append(text)
            thinking = item.get("thinking")
            if item_type == "thinking" and isinstance(thinking, str) and thinking.strip():
                thinking_chunks.append(thinking)
        joined_text = "\n".join(text_chunks).strip()
        if joined_text:
            return joined_text
        joined_thinking = "\n".join(thinking_chunks).strip()
        if joined_thinking:
            return joined_thinking

    completion = body.get("completion")
    if isinstance(completion, str) and completion.strip():
        return completion

    raise KimiProviderResponseError(
        error_type="response_missing_content",
        message="anthropic response missing textual content",
    )


def _extract_json_payload(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, count=1).strip()
        cleaned = re.sub(r"```$", "", cleaned, count=1).strip()

    for candidate in (cleaned, _extract_braced_block(cleaned)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload

    raise KimiProviderSchemaError(
        error_type="response_not_plan_json",
        message="llm content is not parseable JSON object",
    )


def _extract_braced_block(content: str) -> str | None:
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if match is None:
        return None
    return match.group(0)


def _as_non_empty_str(value: Any) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and not _looks_like_placeholder(normalized):
            return normalized
    return None


def _normalize_api_keys(raw: Any) -> list[str]:
    if isinstance(raw, str):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = [item for item in raw if isinstance(item, str)]
    else:
        candidates = []

    normalized: list[str] = []
    for item in candidates:
        key = item.strip()
        if not key:
            continue
        if _looks_like_placeholder(key):
            continue
        normalized.append(key)
    return normalized


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _normalize_api_targets(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []

    targets: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        base_url = _as_non_empty_str(item.get("base_url"))
        model = _as_non_empty_str(item.get("model"))
        api_key = _as_non_empty_str(item.get("api_key"))
        protocol = _normalize_protocol(item.get("protocol"), base_url)
        if not base_url or not model or not api_key:
            continue
        targets.append(
            {
                "base_url": base_url.rstrip("/"),
                "model": model,
                "api_key": api_key,
                "protocol": protocol,
            }
        )
    return _dedupe_targets(targets)


def _targets_from_grouped_config(payload: dict[str, Any]) -> list[dict[str, str]]:
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return []

    targets: list[dict[str, str]] = []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        base_url = _as_non_empty_str(provider.get("base_url"))
        model = _as_non_empty_str(provider.get("model"))
        protocol = _normalize_protocol(provider.get("protocol"), base_url)
        keys = _normalize_api_keys(provider.get("api_keys"))
        if not base_url or not model or not keys:
            continue
        for key in keys:
            targets.append(
                {
                    "base_url": base_url.rstrip("/"),
                    "model": model,
                    "api_key": key,
                    "protocol": protocol,
                }
            )
    return _dedupe_targets(targets)


def _dedupe_targets(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    ordered: list[dict[str, str]] = []
    for item in items:
        key = (item["base_url"], item["model"], item["api_key"], item["protocol"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _normalize_protocol(value: Any, base_url: str | None) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"openai", "anthropic"}:
            return normalized
    if isinstance(base_url, str) and "anthropic" in base_url.lower():
        return "anthropic"
    return "openai"


def _looks_like_placeholder(value: str) -> bool:
    return (
        (value.startswith("<") and value.endswith(">"))
        or ("PLACEHOLDER" in value.upper())
    )


__all__ = [
    "KimiPlanProvider",
    "KimiProviderAuthError",
    "KimiProviderError",
    "KimiProviderNetworkError",
    "KimiProviderResponseError",
    "KimiProviderSchemaError",
]
