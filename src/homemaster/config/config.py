"""Unified YAML configuration for HomeMaster."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from homemaster.config.observability import ObservabilityConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
HOMEMASTER_CONFIG_PATH = REPO_ROOT / "config" / "homemaster.yaml"
DEFAULT_CONTEXT_WINDOW_TOKENS = 200_000
DEFAULT_PROVIDER_NAME = "Mimo"
DEFAULT_EMBEDDING_PROVIDER_NAME = "MemoryEmbedding"

ApiFormatName = Literal["anthropic", "openai"]
TransportName = Literal["anthropic_sdk", "openai_sdk", "raw_http"]
ProviderKind = Literal["chat", "embedding"]
AuthType = Literal["api_key", "auth_token"]
ConfigSource = Literal["default", "file", "env", "cli"]


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
    auth_type: AuthType = "api_key"
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
            "base_url": _redact_url_userinfo(self.base_url),
            "model": self.model,
            "api_format": self.api_format,
            "transport": self.transport,
            "kind": self.kind,
            "auth_type": self.auth_type,
            "embedding_url": _redact_url_userinfo(self.embedding_url),
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


class SkillSourcesConfig(BaseModel):
    user_dirs: tuple[Path, ...] = (
        Path("~/.homemaster/skills"),
        Path("~/.agents/skills"),
        Path("~/.claude/skills"),
    )
    project_dirs: tuple[str, ...] = (
        ".homemaster/skills",
        ".agents/skills",
        ".claude/skills",
    )
    explicit_dirs: tuple[Path, ...] = ()
    allow_project: bool = True
    allowed_builtin_overrides: tuple[str, ...] = ()

    @field_validator("project_dirs")
    @classmethod
    def _project_dirs_are_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("project skill directories must be safe relative paths")
        return values


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
    skills: SkillSourcesConfig = Field(default_factory=SkillSourcesConfig)

    _config_path: Path | None = PrivateAttr(default=None)
    _provenance: dict[str, ConfigSource] = PrivateAttr(default_factory=dict)

    @property
    def config_path(self) -> Path | None:
        return self._config_path

    def field_source(self, field: str) -> ConfigSource:
        """Return a secret-safe source label for a configured field."""

        return self._provenance.get(field, "default")

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


def load_config(
    config_path: str | Path | None = None,
    *,
    cli_overrides: dict[str, str] | None = None,
) -> HomeMasterConfig:
    """Load HomeMaster YAML config once and apply environment overrides."""

    path = _resolve_config_path(config_path)
    if not path.exists():
        config = HomeMasterConfig()
        config._config_path = path
        config._provenance = {}
        return _apply_env_overrides(config, cli_overrides=cli_overrides)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ConfigError(f"invalid HomeMaster YAML config: {path}{location}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"HomeMaster config must be a YAML mapping: {path}")
    try:
        config = HomeMasterConfig.model_validate(payload)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors(include_input=False)
        )
        raise ConfigError(f"invalid HomeMaster config: {path}: {details}") from exc
    except Exception as exc:
        raise ConfigError(f"invalid HomeMaster config: {path}: {type(exc).__name__}") from exc
    config._config_path = path
    config._provenance = {key: "file" for key in _config_leaf_paths(payload)}
    return _apply_env_overrides(config, cli_overrides=cli_overrides)


def _resolve_config_path(config_path: str | Path | None) -> Path:
    path = Path(config_path) if config_path is not None else HOMEMASTER_CONFIG_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def _apply_env_overrides(
    config: HomeMasterConfig,
    *,
    cli_overrides: dict[str, str] | None = None,
) -> HomeMasterConfig:
    providers: list[ProviderProfileConfig] = []
    for provider in config.providers.items:
        update: dict[str, Any] = {}
        env_fields = {
            "api_keys": _provider_env_value(provider.name, "API_KEY"),
            "base_url": _provider_env_value(provider.name, "BASE_URL"),
            "model": _provider_env_value(provider.name, "MODEL"),
            "auth_type": _provider_env_value(provider.name, "AUTH_TYPE"),
        }
        for field, value in env_fields.items():
            if value:
                update[field] = (value,) if field == "api_keys" else value
                config._provenance[f"providers.{provider.name}.{field}"] = "env"

        if cli_overrides:
            model = cli_overrides.get(f"providers.{provider.name}.model")
            if model is None and provider.name.casefold() == config.providers.default.casefold():
                model = cli_overrides.get("providers.default.model")
            if model:
                update["model"] = model
                config._provenance[f"providers.{provider.name}.model"] = "cli"

        if update:
            merged = provider.model_dump(mode="python")
            merged.update(update)
            providers.append(ProviderProfileConfig.model_validate(merged))
        else:
            providers.append(provider)

    if not providers:
        return config
    section = config.providers.model_copy(update={"items": providers})
    updated = config.model_copy(update={"providers": section})
    updated._config_path = config._config_path
    updated._provenance = dict(config._provenance)
    return updated


def _provider_env_value(provider_name: str, suffix: str) -> str | None:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in provider_name.upper())
    return os.getenv(f"HOMEMASTER_{normalized}_{suffix}")


def _is_placeholder_api_key(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def _config_leaf_paths(value: Any, prefix: str = "") -> list[str]:
    if prefix == "providers.items" and isinstance(value, list):
        paths: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                paths.append(prefix)
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                paths.append(prefix)
                continue
            paths.extend(_config_leaf_paths(item, f"providers.{name.strip()}"))
        return paths
    if not isinstance(value, dict):
        return [prefix] if prefix else []
    paths: list[str] = []
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else str(key)
        paths.extend(_config_leaf_paths(child, child_prefix))
    return paths


_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "auth_token",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "authorization",
    "credential",
    "private_key",
)


def redact_config_value(value: Any, *, key: object | None = None) -> Any:
    """Recursively redact config values for diagnostics and structured logs."""

    if key is not None:
        normalized = str(key).lower().replace("-", "_")
        if any(part in normalized for part in _SECRET_KEY_PARTS):
            return "[REDACTED]"
    if isinstance(value, dict):
        return {
            item_key: redact_config_value(item_value, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_config_value(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("bearer ", "basic ")):
        return "[REDACTED]"
    if isinstance(value, str):
        return _redact_url_userinfo(value)
    return value


def _redact_url_userinfo(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = urlsplit(value)
        if parsed.username is None and parsed.password is None:
            return value
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return urlunsplit(
            (parsed.scheme, f"[REDACTED]@{host}{port}", parsed.path, parsed.query, parsed.fragment)
        )
    except ValueError:
        return "[REDACTED_URL]"
