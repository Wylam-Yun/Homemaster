"""Runtime configuration helpers for HomeMaster stage runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERIC_CONFIG_PATH = REPO_ROOT / "config" / "api_config.json"
LEGACY_CONFIG_PATH = REPO_ROOT / "config" / "nvidia_api_config.json"
DEFAULT_CONFIG_PATH = GENERIC_CONFIG_PATH if GENERIC_CONFIG_PATH.exists() else LEGACY_CONFIG_PATH
DEFAULT_PROVIDER_NAME = "Mimo"
DEFAULT_STAGE_01_CASE_NAME = "stage_01_llm_contract_smoke"
LLM_CASE_ROOT = REPO_ROOT / "tests" / "homemaster" / "llm_cases"
TEST_RESULTS_ROOT = REPO_ROOT / "plan" / "V1.2" / "test_results"
STAGE_01_CASE_DIR = LLM_CASE_ROOT / "stage_01" / DEFAULT_STAGE_01_CASE_NAME
STAGE_01_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_01"


class RuntimeConfigError(RuntimeError):
    """Raised when HomeMaster runtime configuration is invalid."""


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_keys: tuple[str, ...]
    protocol: str

    def public_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "protocol": self.protocol,
            "api_key_count": len(self.api_keys),
        }


def load_provider_config(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    provider_name: str = DEFAULT_PROVIDER_NAME,
) -> ProviderConfig:
    """Load one provider from the repository config without exposing secrets."""

    path = Path(config_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        raise RuntimeConfigError(f"missing API config file: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeConfigError(f"invalid API config JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeConfigError(f"API config must be an object: {path}")

    providers = payload.get("providers")
    if isinstance(providers, list):
        for item in providers:
            if not isinstance(item, dict):
                continue
            name = _required_str(item, "name")
            if name.casefold() != provider_name.casefold():
                continue
            return _provider_from_payload(item, fallback_name=name)
        raise RuntimeConfigError(f"provider {provider_name!r} not found in {path}")

    return _provider_from_payload(payload, fallback_name=provider_name)


def ensure_stage_directories(
    *,
    case_dir: Path = STAGE_01_CASE_DIR,
    results_dir: Path = STAGE_01_RESULTS_DIR,
) -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "trace").mkdir(parents=True, exist_ok=True)


def _provider_from_payload(payload: dict[str, Any], *, fallback_name: str) -> ProviderConfig:
    name = _required_str(payload, "name", fallback=fallback_name)
    base_url = _required_str(payload, "base_url").rstrip("/")
    model = _required_str(payload, "model")
    protocol = _normalize_protocol(payload.get("protocol"), base_url)
    api_keys = tuple(_normalize_api_keys(payload.get("api_keys")))
    if not api_keys:
        raise RuntimeConfigError(f"provider {name!r} has no api_keys")
    return ProviderConfig(
        name=name,
        base_url=base_url,
        model=model,
        api_keys=api_keys,
        protocol=protocol,
    )


def _required_str(payload: dict[str, Any], key: str, *, fallback: str | None = None) -> str:
    value = payload.get(key, fallback)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError(f"missing or empty {key!r}")
    return value.strip()


def _normalize_api_keys(raw: Any) -> list[str]:
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def _normalize_protocol(raw: Any, base_url: str) -> str:
    if isinstance(raw, str) and raw.strip().lower() in {"anthropic", "openai"}:
        return raw.strip().lower()
    if "anthropic" in base_url.lower():
        return "anthropic"
    return "openai"
