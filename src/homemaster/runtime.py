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
DEFAULT_STAGE_01_CASE_NAME = "stage_01_llm_contract_smoke"


class RuntimeConfigError(RuntimeError):
    """Raised when HomeMaster runtime configuration is invalid."""


# ---------------------------------------------------------------------------
# P7: homemaster.json config loader
# ---------------------------------------------------------------------------

HOMEMASTER_CONFIG_PATH = REPO_ROOT / "config" / "homemaster.json"


def load_homemaster_config(
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load the optional homemaster config.

    Returns {} if file does not exist.
    Raises RuntimeConfigError if file exists but is invalid JSON or not a dict.
    """
    if config_path is None:
        config_path = HOMEMASTER_CONFIG_PATH
    path = Path(config_path)
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeConfigError(f"invalid homemaster config JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeConfigError(f"homemaster config must be a JSON object: {path}")
    return payload


def get_config_section(
    config: dict[str, Any], section: str
) -> dict[str, Any] | None:
    """Extract a section. Returns None if key missing (caller uses defaults).

    Raises RuntimeConfigError if key exists but value is not a dict.
    """
    if section not in config:
        return None
    value = config[section]
    if not isinstance(value, dict):
        raise RuntimeConfigError(
            f"config section {section!r} must be a JSON object, got {type(value).__name__}"
        )
    return value


def _require_type(value: Any, expected: type | tuple[type, ...], path: str) -> Any:
    """Validate value type, raise RuntimeConfigError on mismatch."""
    if not isinstance(value, expected):
        raise RuntimeConfigError(
            f"config {path} must be {_type_name(expected)}, got {type(value).__name__}: {value!r}"
        )
    return value


def _type_name(expected: type | tuple[type, ...]) -> str:
    if isinstance(expected, tuple):
        return " or ".join(t.__name__ for t in expected)
    return expected.__name__


def load_provider_client_config() -> dict[str, Any]:
    """Load provider_client section with validated numeric defaults."""
    cfg = get_config_section(load_homemaster_config(), "provider_client")
    if cfg is None:
        return {}
    for key in ("timeout_s", "connect_timeout_s", "write_timeout_s", "pool_timeout_s"):
        if key in cfg:
            _require_type(cfg[key], (int, float), f"provider_client.{key}")
            if cfg[key] <= 0:
                raise RuntimeConfigError(
                    f"config provider_client.{key} must be > 0, got {cfg[key]}"
                )
    return cfg


def load_runtime_paths_config() -> dict[str, str | None]:
    """Load runtime_paths section. Values are str paths or null (use code default)."""
    cfg = get_config_section(load_homemaster_config(), "runtime_paths")
    if cfg is None:
        return {}
    for key, value in cfg.items():
        if value is not None and not isinstance(value, str):
            raise RuntimeConfigError(
                f"config runtime_paths.{key} must be a string or null, "
                f"got {type(value).__name__}"
            )
    return cfg


def load_runtime_defaults_config() -> dict[str, Any]:
    """Load runtime_defaults section with type validation."""
    cfg = get_config_section(load_homemaster_config(), "runtime_defaults")
    if cfg is None:
        return {}
    if "live_models" in cfg:
        _require_type(cfg["live_models"], bool, "runtime_defaults.live_models")
    if "mock_skills" in cfg:
        _require_type(cfg["mock_skills"], bool, "runtime_defaults.mock_skills")
    for key in ("default_provider_name", "default_embedding_provider_name"):
        if key in cfg:
            _require_type(cfg[key], str, f"runtime_defaults.{key}")
    return cfg


# ---------------------------------------------------------------------------
# P7: derived constants from config (with safe defaults)
# ---------------------------------------------------------------------------

_defaults_cfg = load_runtime_defaults_config()
_paths_cfg = load_runtime_paths_config()

DEFAULT_PROVIDER_NAME: str = _defaults_cfg.get("default_provider_name", "Mimo")
DEFAULT_EMBEDDING_PROVIDER_NAME: str = _defaults_cfg.get(
    "default_embedding_provider_name", "MemoryEmbedding"
)
DEFAULT_LIVE_MODELS: bool = _defaults_cfg.get("live_models", False)
DEFAULT_MOCK_SKILLS: bool = _defaults_cfg.get("mock_skills", True)

_llm_case_root = _paths_cfg.get("llm_case_root")
LLM_CASE_ROOT = (
    Path(_llm_case_root) if _llm_case_root
    else REPO_ROOT / "tests" / "homemaster" / "llm_cases"
)

_test_results_root = _paths_cfg.get("test_results_root")
TEST_RESULTS_ROOT = (
    Path(_test_results_root) if _test_results_root
    else REPO_ROOT / "plan" / "V1.2" / "test_results"
)

STAGE_01_CASE_DIR = LLM_CASE_ROOT / "stage_01" / DEFAULT_STAGE_01_CASE_NAME
STAGE_01_RESULTS_DIR = TEST_RESULTS_ROOT / "stage_01"

_runtime_root = _paths_cfg.get("runtime_root")
DEFAULT_STAGE_07_RUNTIME_ROOT = (
    Path(_runtime_root) if _runtime_root
    else REPO_ROOT / "var" / "homemaster" / "runs"
)

_debug_root = _paths_cfg.get("debug_root")
DEFAULT_STAGE_07_DEBUG_ROOT = (
    Path(_debug_root) if _debug_root
    else REPO_ROOT / "var" / "homemaster" / "debug"
)


# ---------------------------------------------------------------------------
# Provider config loading
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_keys: tuple[str, ...]
    protocol: str
    embedding_url: str | None = None

    def public_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "protocol": self.protocol,
            "embedding_url": self.embedding_url,
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
        embedding_url=_optional_str(payload.get("embedding_url")),
    )


def _required_str(payload: dict[str, Any], key: str, *, fallback: str | None = None) -> str:
    value = payload.get(key, fallback)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError(f"missing or empty {key!r}")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
