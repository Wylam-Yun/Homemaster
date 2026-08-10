"""Unified YAML configuration for HomeMaster."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from homemaster.config.observability import ObservabilityConfig
from homemaster.mcp.types import McpSettingsConfig
from homemaster.permissions.config import PermissionSettingsConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)
_WARNED_LEGACY_MEMORY_FIELDS: set[tuple[str, ...]] = set()


def _default_config_path(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    configured = values.get("HOMEMASTER_CONFIG_PATH", "").strip()
    return Path(configured).expanduser() if configured else REPO_ROOT / "config" / "homemaster.yaml"


HOMEMASTER_CONFIG_PATH = _default_config_path()
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
            "base_url": self.base_url,
            "model": self.model,
            "api_format": self.api_format,
            "transport": self.transport,
            "kind": self.kind,
            "auth_type": self.auth_type,
            "embedding_url": self.embedding_url,
            "api_keys": list(self.api_keys),
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
            "observe": 2,
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


def _private_absolute_path(value: Path) -> Path:
    expanded = value.expanduser()
    if not expanded.is_absolute():
        expanded = Path.cwd() / expanded
    return expanded.absolute()


class MemoryMigrationSpec(BaseModel):
    """Immutable one-time inputs captured while parsing legacy memory fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    files_source: Path
    evidence_source: Path
    explicit_legacy_fields: tuple[str, ...] = ()

    @field_validator("files_source", "evidence_source")
    @classmethod
    def _expand_paths(cls, value: Path) -> Path:
        return _private_absolute_path(value)


class MemoryNeo4jConfig(BaseModel):
    """Neo4j connection and optional HomeMaster-owned local process settings."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    mode: Literal["external", "managed_local"] = "external"
    home: Path | None = None
    java_home: Path | None = None
    uri: str = "bolt://127.0.0.1:7687"
    username: str = "neo4j"
    password: SecretStr = SecretStr("")
    database: str = "neo4j"
    start_timeout_seconds: float = Field(default=60.0, gt=0)
    stop_timeout_seconds: float = Field(default=30.0, gt=0)

    @field_validator("home", "java_home")
    @classmethod
    def _expand_optional_paths(cls, value: Path | None) -> Path | None:
        return _private_absolute_path(value) if value is not None else None

    @field_validator("username", "database")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Neo4j username and database must not be blank")
        return stripped

    @field_validator("uri")
    @classmethod
    def _valid_bolt_uri(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"bolt", "neo4j"} or not parsed.hostname:
            raise ValueError("Neo4j uri must be an absolute bolt/neo4j URI")
        return value

    @model_validator(mode="after")
    def _managed_local_has_runtime_inputs(self) -> MemoryNeo4jConfig:
        if self.mode != "managed_local":
            return self
        missing: list[str] = []
        if self.home is None:
            missing.append("home")
        if self.java_home is None:
            missing.append("java_home")
        if not self.password.get_secret_value():
            missing.append("password")
        if missing:
            raise ValueError(
                "managed_local Neo4j requires home, java_home, and password; missing "
                + ", ".join(missing)
            )
        return self


class MemoryConfig(BaseModel):
    """File memory and embedded MindMemOS configuration."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    enabled: bool = True
    data_root: Path = Path("~/.homemaster/memory")
    soul_file: str = "SOUL.md"
    user_file: str = "USER.md"
    memory_file: str = "MEMORY.md"
    user_char_limit: int = Field(default=1375, gt=0)
    memory_char_limit: int = Field(default=2200, gt=0)
    embedding_provider_name: str = DEFAULT_EMBEDDING_PROVIDER_NAME
    embedding_dimensions: int = Field(default=4096, gt=0)
    neo4j: MemoryNeo4jConfig = Field(default_factory=MemoryNeo4jConfig)
    migration_spec: MemoryMigrationSpec = Field(exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def _capture_legacy_paths(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "migration_spec" in data:
            raise ValueError("memory.migration_spec is internal")
        has_data_root = "data_root" in data
        legacy_fields: list[str] = []
        if "root" in data:
            legacy_fields.append("memory.root")
        if has_data_root and legacy_fields:
            raise ValueError("memory.data_root cannot be combined with legacy memory path fields")

        data_root = _private_absolute_path(Path(data.get("data_root", "~/.homemaster/memory")))
        files_source = Path(
            data.pop(
                "root",
                data_root / "files" if has_data_root else "~/.homemaster/memories",
            )
        )
        data["data_root"] = data_root
        data["migration_spec"] = {
            "files_source": files_source,
            "evidence_source": data_root / "evidence.sqlite3",
            "explicit_legacy_fields": tuple(legacy_fields),
        }
        return data

    @field_validator("data_root")
    @classmethod
    def _expand_root(cls, value: Path) -> Path:
        return _private_absolute_path(value)

    @field_validator("soul_file", "user_file", "memory_file")
    @classmethod
    def _plain_file_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or Path(stripped).name != stripped or stripped in {".", ".."}:
            raise ValueError("memory file must be a plain file name")
        return stripped

    @field_validator("embedding_provider_name")
    @classmethod
    def _embedding_provider_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("embedding_provider_name must not be blank")
        return stripped

    @property
    def soul_path(self) -> Path:
        return self.files_root / self.soul_file

    @property
    def user_path(self) -> Path:
        return self.files_root / self.user_file

    @property
    def memory_path(self) -> Path:
        return self.files_root / self.memory_file

    @property
    def files_root(self) -> Path:
        return self.data_root / "files"

    @property
    def root(self) -> Path:
        """Compatibility property for file-store callers; never a config field."""

        return self.files_root

    @property
    def mindmemos_qdrant_path(self) -> Path:
        return self.data_root / "mindmemos" / "qdrant"

    @property
    def neo4j_runtime_root(self) -> Path:
        return self.data_root / "mindmemos" / "neo4j" / "runtime"

    @property
    def evidence_db_path(self) -> Path:
        return self.data_root / "evidence.sqlite3"


class SkillSourcesConfig(BaseModel):
    user_dirs: tuple[Path, ...] = (Path("~/.homemaster/skills"),)
    project_dirs: tuple[str, ...] = (".homemaster/skills",)
    explicit_dirs: tuple[Path, ...] = ()
    allow_project: bool = True
    plugin_roots: tuple[Path, ...] = ()
    enabled_plugins: dict[str, bool] = Field(default_factory=dict)
    allow_project_plugin_skills: bool = False
    allowed_builtin_overrides: tuple[str, ...] = ()

    @field_validator("project_dirs")
    @classmethod
    def _project_dirs_are_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("project skill directories must be safe relative paths")
        return values


class ChannelPrincipalConfig(BaseModel):
    principal_id: str
    roles: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    token_env: str = "HOMEMASTER_TELEGRAM_BOT_TOKEN"
    tenant_id: str = "local"
    bot_name: str = "HomeMaster"
    attachment_root: Path = Path("~/.homemaster/attachments/telegram")
    principals: dict[str, ChannelPrincipalConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _enabled_channel_has_explicit_principals(self) -> TelegramChannelConfig:
        if self.enabled and not self.principals:
            raise ValueError("enabled Telegram channel requires explicit principals")
        if "*" in self.principals:
            raise ValueError("Telegram wildcard principals are not allowed")
        return self


class FeishuChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    app_id: str = ""
    app_secret: SecretStr = SecretStr("")
    app_id_env: str = "HOMEMASTER_FEISHU_APP_ID"
    app_secret_env: str = "HOMEMASTER_FEISHU_APP_SECRET"
    encrypt_key_env: str = "HOMEMASTER_FEISHU_ENCRYPT_KEY"
    verification_token_env: str = "HOMEMASTER_FEISHU_VERIFICATION_TOKEN"
    tenant_id: str = "local"
    domain: Literal["feishu", "lark"] = "feishu"
    react_emoji: str = "EYES"
    attachment_root: Path = Path("~/.homemaster/attachments/feishu")

    @field_validator("app_id")
    @classmethod
    def _strip_app_id(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _enabled_channel_is_exactly_configured(self) -> FeishuChannelConfig:
        direct_secret = self.app_secret.get_secret_value().strip()
        if bool(self.app_id) != bool(direct_secret):
            raise ValueError("Feishu app_id and app_secret must be configured together")
        return self


class GatewayConfig(BaseModel):
    enabled: bool = False
    bus_capacity: int = Field(default=128, ge=1)
    per_tenant_capacity: int = Field(default=64, ge=1)
    per_session_capacity: int = Field(default=32, ge=1)
    shutdown_deadline_s: float = Field(default=5.0, gt=0)
    feishu: FeishuChannelConfig = Field(default_factory=FeishuChannelConfig)

    @model_validator(mode="after")
    def _capacity_is_consistent(self) -> GatewayConfig:
        if self.per_session_capacity > self.bus_capacity:
            raise ValueError("per-session Gateway capacity cannot exceed total capacity")
        if self.per_tenant_capacity > self.bus_capacity:
            raise ValueError("per-tenant Gateway capacity cannot exceed total capacity")
        if self.per_session_capacity > self.per_tenant_capacity:
            raise ValueError("per-session Gateway capacity cannot exceed tenant capacity")
        if self.feishu.enabled and not self.enabled:
            raise ValueError("Feishu cannot be enabled while Gateway is disabled")
        return self


class AlfworldGatewayConfig(BaseModel):
    """Deployment-only paths and deterministic episode selection for Gateway."""

    model_config = ConfigDict(extra="forbid")

    asset_root: Path | None = None
    data_root: Path | None = None
    config_path: Path | None = None
    python_executable: Path | None = None
    env_type: Literal["AlfredThorEnv"] = "AlfredThorEnv"
    split: Literal["train", "valid_seen", "valid_unseen"] = "valid_unseen"
    trial_manifest: Path | None = None
    trial_index: int = Field(default=0, ge=0)
    seed: int = 42
    display: str = ":102"
    manage_xvfb: bool = False
    xvfb_executable: Path = Path("/usr/bin/Xvfb")
    startup_timeout_s: float = Field(default=180.0, gt=0)
    request_timeout_s: float = Field(default=120.0, gt=0)
    allow_offscreen_object_navigation: bool = True

    @field_validator("display")
    @classmethod
    def _display_is_explicit(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(":") or not normalized[1:].isdigit():
            raise ValueError("display must be an explicit X display such as ':102'")
        return normalized

    def require_runtime_paths(self) -> tuple[Path, Path, Path, Path, Path]:
        required = {
            "asset_root": self.asset_root,
            "data_root": self.data_root,
            "config_path": self.config_path,
            "python_executable": self.python_executable,
            "trial_manifest": self.trial_manifest,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError("ALFWorld Gateway requires configured paths: " + ", ".join(missing))
        return (
            self.asset_root,
            self.data_root,
            self.config_path,
            self.python_executable,
            self.trial_manifest,
        )


class BrowserGatewayConfig(BaseModel):
    """Deployment-owned Browser Gateway target and Playwright policy."""

    model_config = ConfigDict(extra="forbid")

    start_url: str | None = None
    allowed_origins: tuple[str, ...] = ()
    headless: bool = True
    action_timeout_ms: int = Field(default=10_000, ge=1)
    navigation_timeout_ms: int = Field(default=20_000, ge=1)
    wait_timeout_ms: int = Field(default=10_000, ge=1)

    @model_validator(mode="after")
    def _start_origin_is_allowed(self) -> BrowserGatewayConfig:
        if self.start_url is None:
            return self
        start_origin = _http_origin(self.start_url, label="browser_gateway.start_url")
        origins = tuple(
            _http_origin(value, label="browser_gateway.allowed_origins")
            for value in self.allowed_origins
        )
        if len(origins) != len(set(origins)):
            raise ValueError("browser_gateway.allowed_origins must be unique")
        if start_origin not in origins:
            raise ValueError("browser_gateway start_url origin must be in allowed_origins")
        self.allowed_origins = origins
        return self

    def require_runtime(self) -> tuple[str, tuple[str, ...]]:
        if self.start_url is None:
            raise ValueError("Browser Gateway requires configured start_url")
        if not self.allowed_origins:
            raise ValueError("Browser Gateway requires configured allowed_origins")
        return self.start_url, self.allowed_origins


class ExtensionApprovalConfig(BaseModel):
    """Deployment-owned pin and grants for one trusted local extension."""

    model_config = ConfigDict(extra="forbid")

    manifest_path: Path
    extension_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    granted_capabilities: tuple[str, ...] = ()
    enabled_tool_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _sequences_are_unique_and_nonempty(self) -> ExtensionApprovalConfig:
        for label, values in (
            ("granted capabilities", self.granted_capabilities),
            ("enabled tool ids", self.enabled_tool_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"extension {label} must be unique")
            if any(not value.strip() for value in values):
                raise ValueError(f"extension {label} must be non-empty")
        return self


class ExtensionsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approvals: tuple[ExtensionApprovalConfig, ...] = ()

    @model_validator(mode="after")
    def _ids_are_unique(self) -> ExtensionsConfig:
        ids = [approval.extension_id for approval in self.approvals]
        if len(ids) != len(set(ids)):
            raise ValueError("extension approval ids must be unique")
        return self


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
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    skills: SkillSourcesConfig = Field(default_factory=SkillSourcesConfig)
    mcp: McpSettingsConfig = Field(default_factory=McpSettingsConfig)
    permissions: PermissionSettingsConfig = Field(default_factory=PermissionSettingsConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    alfworld_gateway: AlfworldGatewayConfig = Field(default_factory=AlfworldGatewayConfig)
    browser_gateway: BrowserGatewayConfig = Field(default_factory=BrowserGatewayConfig)
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)

    _config_path: Path | None = PrivateAttr(default=None)
    _provenance: dict[str, ConfigSource] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def _validate_memory_embedding_provider(self) -> HomeMasterConfig:
        if not self.memory.enabled or not self.providers.items:
            return self
        name = self.memory.embedding_provider_name.casefold()
        provider = next(
            (item for item in self.providers.items if item.name.casefold() == name),
            None,
        )
        if provider is not None and provider.kind != "embedding":
            label = self.memory.embedding_provider_name
            raise ValueError(f"memory provider {label!r} must exist with kind 'embedding'")
        return self

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


def _http_origin(value: str, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")
    default_port = 80 if parsed.scheme == "http" else 443
    suffix = f":{parsed.port}" if parsed.port is not None and parsed.port != default_port else ""
    return f"{parsed.scheme}://{parsed.hostname.lower()}{suffix}"


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
    legacy_fields = config.memory.migration_spec.explicit_legacy_fields
    if legacy_fields and legacy_fields not in _WARNED_LEGACY_MEMORY_FIELDS:
        _WARNED_LEGACY_MEMORY_FIELDS.add(legacy_fields)
        logger.warning(
            json.dumps(
                {
                    "event": "config.memory.legacy_paths",
                    "fields": list(legacy_fields),
                    "replacement": "memory.data_root",
                },
                sort_keys=True,
            )
        )
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
