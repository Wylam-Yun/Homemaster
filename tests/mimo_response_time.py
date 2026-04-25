from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERIC_CONFIG_PATH = REPO_ROOT / "config" / "api_config.json"
LEGACY_CONFIG_PATH = REPO_ROOT / "config" / "nvidia_api_config.json"
DEFAULT_CONFIG_PATH = GENERIC_CONFIG_PATH if GENERIC_CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
DEFAULT_PROVIDER_NAME = "Mimo"

PROMPT_SOURCE = (
    "请收到后仅用一句中文短句回复测试完成，并说明家庭服务机器人在老人照护中关注安全、"
    "任务规划、记忆检索、异常恢复、沟通反馈和可解释结果。请保持简短。"
)
PROMPT_100_CHARS = (PROMPT_SOURCE * 2)[:100]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key: str
    protocol: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a 100-character prompt to the Mimo provider and print response time."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"API config path. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER_NAME,
        help=f"Provider name to use. Default: {DEFAULT_PROVIDER_NAME}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Read timeout in seconds.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=128,
        help="Maximum response tokens.",
    )
    parser.add_argument(
        "--print-response",
        action="store_true",
        help="Print the full response text instead of a short preview.",
    )
    return parser.parse_args()


def load_provider(config_path: Path, provider_name: str) -> ProviderConfig:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    providers = payload.get("providers")
    if not isinstance(providers, list):
        raise ValueError(f"config does not contain a providers list: {config_path}")

    for provider in providers:
        if not isinstance(provider, dict):
            continue
        name = str(provider.get("name", "")).strip()
        if name.casefold() != provider_name.casefold():
            continue

        base_url = _required_str(provider, "base_url").rstrip("/")
        model = _required_str(provider, "model")
        api_key = _first_api_key(provider.get("api_keys"))
        protocol = str(provider.get("protocol", "anthropic")).strip().lower() or "anthropic"
        if protocol not in {"anthropic", "openai"}:
            raise ValueError(f"unsupported protocol for provider {name!r}: {protocol!r}")

        return ProviderConfig(
            name=name,
            base_url=base_url,
            model=model,
            api_key=api_key,
            protocol=protocol,
        )

    raise ValueError(f"provider {provider_name!r} not found in {config_path}")


def send_prompt(
    provider: ProviderConfig,
    prompt: str,
    *,
    timeout: float,
    max_tokens: int,
) -> tuple[float, str]:
    request_timeout = httpx.Timeout(connect=10.0, read=timeout, write=15.0, pool=10.0)
    with httpx.Client(timeout=request_timeout) as client:
        started = time.perf_counter()
        if provider.protocol == "anthropic":
            response = client.post(
                f"{provider.base_url}/v1/messages",
                headers={
                    "x-api-key": provider.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": provider.model,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        else:
            response = client.post(
                f"{provider.base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {provider.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": provider.model,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        elapsed_ms = (time.perf_counter() - started) * 1000

    if response.status_code >= 400:
        message = _extract_error_message(response)
        raise RuntimeError(f"HTTP {response.status_code} after {elapsed_ms:.2f} ms: {message}")

    body = response.json()
    content = (
        _extract_anthropic_content(body)
        if provider.protocol == "anthropic"
        else _extract_openai_content(body)
    )
    return elapsed_ms, content


def main() -> int:
    args = parse_args()
    prompt = PROMPT_100_CHARS
    if len(prompt) != 100:
        raise AssertionError(f"prompt must be exactly 100 characters, got {len(prompt)}")

    try:
        provider = load_provider(args.config, args.provider)
        elapsed_ms, content = send_prompt(
            provider,
            prompt,
            timeout=args.timeout,
            max_tokens=args.max_tokens,
        )
    except (httpx.RequestError, ValueError, RuntimeError) as exc:
        print(f"mimo_response_time_failed: {exc}", file=sys.stderr)
        return 1

    response_text = content if args.print_response else _preview(content)
    print(f"provider: {provider.name}")
    print(f"model: {provider.model}")
    print(f"protocol: {provider.protocol}")
    print(f"prompt_chars: {len(prompt)}")
    print(f"elapsed_ms: {elapsed_ms:.2f}")
    print(f"response_chars: {len(content)}")
    print(f"response: {response_text}")
    return 0


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or empty {key!r}")
    return value.strip()


def _first_api_key(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                return item.strip()
    raise ValueError("missing api_keys")


def _extract_anthropic_content(body: dict[str, Any]) -> str:
    content = body.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        text = "\n".join(part for part in text_parts if part).strip()
        if text:
            return text
        thinking_parts = [
            item.get("thinking", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "thinking"
        ]
        thinking = "\n".join(part for part in thinking_parts if part).strip()
        if thinking:
            return thinking
    return json.dumps(body, ensure_ascii=False)


def _extract_openai_content(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    return json.dumps(body, ensure_ascii=False)


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


def _preview(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main())
