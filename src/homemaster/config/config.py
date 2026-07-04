"""Unified YAML configuration for HomeMaster."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from homemaster.config.observability import ObservabilityConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
HOMEMASTER_CONFIG_PATH = REPO_ROOT / "config" / "homemaster.yaml"
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
DEFAULT_PROVIDER_NAME = "Mimo"
DEFAULT_EMBEDDING_PROVIDER_NAME = "MemoryEmbedding"

ApiFormatName = Literal["anthropic", "openai"]
TransportName = Literal["anthropic_sdk", "openai_sdk", "raw_http"]
ProviderKind = Literal["chat", "embedding"]


class ConfigError(RuntimeError):
    """Raised when HomeMaster configuration is invalid."""


class ProviderProfileConfig(BaseModel):
    """Single model/embedding provider profile."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    api_format: ApiFormatName
    transport: TransportName = "raw_http"
    base_url: str
    model: str
    api_keys: tuple[str, ...] = Field(default_factory=tuple)
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    max_output_tokens: int | None = None
    embedding_url: str | None = None
    kind: ProviderKind = "chat"

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_protocol(cls, value: Any) -> Any:
        if isinstance(value, dict):
            data = dict(value)
            if "api_format" not in data and "protocol" in data:
                data["api_format"] = data["protocol"]
            if "transport" not in data:
                api_format = str(data.get("api_format") or "").strip().lower()
                if api_format == "anthropic":
                    data["transport"] = "raw_http"
                elif api_format == "openai":
                    data["transport"] = "raw_http"
            return data
        return value

    @field_validator("name", "model")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        stripped = value.strip().rstrip("/")
        if not stripped:
            raise ValueError("base_url must not be blank")
        return stripped

    @field_validator("api_keys", mode="before")
    @classmethod
    def _normalize_api_keys(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            key = value.strip()
            return (key,) if key and not _is_placeholder_api_key(key) else ()
        if isinstance(value, (list, tuple)):
            keys = []
            for item in value:
                key = str(item).strip()
                if key and not _is_placeholder_api_key(key):
                    keys.append(key)
            return tuple(keys)
        raise ValueError("api_keys must be a string or list of strings")

    @field_validator("context_window_tokens", mode="before")
    @classmethod
    def _default_context_window(cls, value: object) -> int:
        if value is None:
            return DEFAULT_CONTEXT_WINDOW_TOKENS
        return int(value)  # type: ignore[arg-type]

    @property
    def protocol(self) -> str:
        """Compatibility alias while old callers are migrated."""

        return self.api_format

    def public_summary(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url,
            "model": self.model,
            "api_format": self.api_format,
            "transport": self.transport,
            "kind": self.kind,
            "embedding_url": self.embedding_url,
            "api_key_count": len(self.api_keys),
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


class ProviderConfigSection(BaseModel):
    default: str = DEFAULT_PROVIDER_NAME
    items: list[ProviderProfileConfig] = Field(default_factory=list)


class ContextPolicyConfig(BaseModel):
    auto_compact_enabled: bool = True
    compression_threshold_ratio: float = 0.50
    output_reserve_tokens: int = 8192
    token_estimation_padding: float = 4 / 3
    safety_buffer_tokens: int = 13_000
    enabled_providers: tuple[str, ...] = (
        "conversation",
        "task_state_snapshot",
        "failure_summary",
        "runtime_budget_status",
        "memory",
        "skills",
    )

    protect_first_n: int = 3
    tail_token_ratio: float = 0.10
    aggressive_tail_token_ratio: float = 0.05
    aggressive_protect_first_n: int = 1
    keep_recent_images: int = 2
    keep_recent_tool_results_per_type: dict[str, int] = Field(
        default_factory=lambda: {
            "robot_observe": 2,
            "memory_retriever": 1,
            "robot_verify": 2,
        }
    )
    default_keep_recent_tool_results: int = 3
    enable_llm_summary: bool = True
    summary_model: str | None = None
    abort_on_summary_failure: bool = True
    summary_failure_cooldown_seconds: int = 60
    reactive_compact_max_retries: int = 2
    enable_disk_overflow: bool = False
    tool_result_overflow_threshold_chars: int = 4000

    # Compatibility fields used by the current assembler until the V1.6 compactor lands.
    recent_tail_ratio: float = 0.20
    preserve_recent_agent_steps: int = 20
    preserve_recent_user_turns: int = 3
    image_token_estimate: int = 4096


class RuntimeGuardConfig(BaseModel):
    max_tool_iterations: int | None = None
    max_consecutive_tool_errors: int = 5
    max_no_progress_iterations: int = 20
    max_wall_clock_minutes: float | None = None
    runtime_root: Path = Path("/tmp/homemaster/runs")
    debug_root: Path = Path("/tmp/homemaster/debug")
    results_root: Path = Path("/tmp/homemaster/results")


class PromptConfig(BaseModel):
    agent_system_prompt: str = "agent_system_prompt"
    compact_summary_prompt: str = "compact_summary_prompt"
    task_interpreter_prompt: str = "task_interpreter_prompt"
    memory_query_prompt: str = "memory_query_prompt"
    memory_query_retry: str = "memory_query_retry"
    task_summary_prompt: str = "task_summary_prompt"


class RetrievalScoringConfig(BaseModel):
    metadata_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "target_category_match": 0.2,
            "target_alias_match": 0.2,
            "location_match": 0.15,
            "high_confidence": 0.1,
            "medium_confidence": 0.05,
            "stale_penalty": -0.1,
        }
    )
    rrf_k: int = 60
    top_k_limit: int = 50


class GroundingConfig(BaseModel):
    room_hints: dict[str, list[str]] = Field(default_factory=dict)
    anchor_hints: dict[str, list[str]] = Field(default_factory=dict)
    specific_anchor_words: dict[str, list[str]] = Field(default_factory=dict)


class ProviderClientConfig(BaseModel):
    timeout_s: float = 60.0
    connect_timeout_s: float = 10.0
    write_timeout_s: float = 15.0
    pool_timeout_s: float = 10.0
    max_retries: int = 2


class RuntimePathsConfig(BaseModel):
    runtime_root: str | None = None
    debug_root: str | None = None
    results_root: str | None = None
    test_results_root: str | None = None
    llm_case_root: str | None = None
    memory_case_root: str | None = None
    memory_results_root: str | None = None


class RuntimeDefaultsConfig(BaseModel):
    default_provider_name: str = DEFAULT_PROVIDER_NAME
    default_embedding_provider_name: str = DEFAULT_EMBEDDING_PROVIDER_NAME


class HomeMasterConfig(BaseModel):
    providers: ProviderConfigSection = Field(default_factory=ProviderConfigSection)
    context: ContextPolicyConfig = Field(default_factory=ContextPolicyConfig)
    runtime: RuntimeGuardConfig = Field(default_factory=RuntimeGuardConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)
    retrieval_scoring: RetrievalScoringConfig = Field(default_factory=RetrievalScoringConfig)
    grounding: GroundingConfig = Field(default_factory=GroundingConfig)
    provider_client: ProviderClientConfig = Field(default_factory=ProviderClientConfig)
    runtime_paths: RuntimePathsConfig = Field(default_factory=RuntimePathsConfig)
    runtime_defaults: RuntimeDefaultsConfig = Field(default_factory=RuntimeDefaultsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    _config_path: Path | None = PrivateAttr(default=None)

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def get_provider(
        self,
        name: str | None = None,
        *,
        kind: ProviderKind | None = None,
    ) -> ProviderProfileConfig:
        target = (name or self.providers.default).casefold()
        for provider in self.providers.items:
            if provider.name.casefold() == target and (kind is None or provider.kind == kind):
                return provider
        label = name or self.providers.default
        suffix = f" with kind {kind!r}" if kind else ""
        raise ConfigError(f"provider {label!r}{suffix} not found")


def load_config(config_path: str | Path | None = None) -> HomeMasterConfig:
    """Load HomeMaster YAML config once and apply environment overrides."""

    path = _resolve_config_path(config_path)
    if not path.exists():
        config = HomeMasterConfig()
        config._config_path = path
        return _apply_env_overrides(config)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid HomeMaster YAML config: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"HomeMaster config must be a YAML mapping: {path}")
    try:
        config = HomeMasterConfig.model_validate(payload)
    except Exception as exc:
        raise ConfigError(f"invalid HomeMaster config: {path}: {exc}") from exc
    config._config_path = path
    return _apply_env_overrides(config)


def _resolve_config_path(config_path: str | Path | None) -> Path:
    path = Path(config_path) if config_path is not None else HOMEMASTER_CONFIG_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _apply_env_overrides(config: HomeMasterConfig) -> HomeMasterConfig:
    providers: list[ProviderProfileConfig] = []
    for provider in config.providers.items:
        update: dict[str, Any] = {}
        api_key = _provider_env_value(provider.name, "API_KEY")
        if api_key:
            update["api_keys"] = (api_key,)

        if provider.name.casefold() == config.providers.default.casefold():
            anthropic_key = os.getenv("ANTHROPIC_AUTH_TOKEN")
            anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL")
            anthropic_model = os.getenv("ANTHROPIC_MODEL")
            if anthropic_key:
                update["api_keys"] = (anthropic_key,)
            if anthropic_base_url:
                update["base_url"] = anthropic_base_url
            if anthropic_model:
                update["model"] = anthropic_model

        providers.append(provider.model_copy(update=update) if update else provider)

    if not providers:
        return config
    section = config.providers.model_copy(update={"items": providers})
    return config.model_copy(update={"providers": section})


def _provider_env_value(provider_name: str, suffix: str) -> str | None:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in provider_name.upper())
    return os.getenv(f"HOMEMASTER_{normalized}_{suffix}")


def _is_placeholder_api_key(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


DEFAULT_CONFIG_PATH = HOMEMASTER_CONFIG_PATH
GENERIC_CONFIG_PATH = HOMEMASTER_CONFIG_PATH
MEMORY_CASE_ROOT = REPO_ROOT / "tests" / "homemaster" / "memory_cases"
MEMORY_RESULTS_ROOT = REPO_ROOT / "plan" / "test_results"
