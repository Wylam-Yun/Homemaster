"""Typed global configuration models for HomeMaster V1.5.

Covers provider profiles, context policy, runtime guards, and prompt config.
Loaded from homemaster.json (same file as runtime.py config sections).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from homemaster.runtime import RuntimeConfigError

ProtocolName = Literal["anthropic", "openai"]


class ProviderProfileConfig(BaseModel):
    name: str
    protocol: ProtocolName
    base_url: str
    model: str
    api_keys: tuple[str, ...] = Field(default_factory=tuple)
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    embedding_url: str | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("api_keys", mode="before")
    @classmethod
    def _normalize_api_keys(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(str(item) for item in value if str(item))
        if isinstance(value, tuple):
            return tuple(str(item) for item in value if str(item))
        raise ValueError("api_keys must be a string or list of strings")


class ProviderConfigSection(BaseModel):
    default: str = "Mimo"
    items: list[ProviderProfileConfig] = Field(default_factory=list)


class ContextPolicyConfig(BaseModel):
    auto_compact_enabled: bool = True
    compression_threshold_ratio: float = 0.50
    recent_tail_ratio: float = 0.20
    output_reserve_tokens: int = 8192
    preserve_recent_agent_steps: int = 20
    preserve_recent_user_turns: int = 3
    token_estimation_padding: float = 4 / 3
    safety_buffer_tokens: int = 13_000
    image_token_estimate: int = 4096
    enabled_providers: tuple[str, ...] = (
        "conversation",
        "task_state_snapshot",
        "failure_summary",
        "runtime_budget_status",
        "memory",
        "skills",
    )


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


class HomeMasterConfig(BaseModel):
    providers: ProviderConfigSection = Field(default_factory=ProviderConfigSection)
    context: ContextPolicyConfig = Field(default_factory=ContextPolicyConfig)
    runtime: RuntimeGuardConfig = Field(default_factory=RuntimeGuardConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)

    def get_provider(self, name: str | None = None) -> ProviderProfileConfig:
        target = (name or self.providers.default).casefold()
        for provider in self.providers.items:
            if provider.name.casefold() == target:
                return provider
        raise RuntimeConfigError(f"provider {name or self.providers.default!r} not found")


def load_model_config(config_path: str | Path | None = None) -> HomeMasterConfig:
    from homemaster.runtime import HOMEMASTER_CONFIG_PATH

    path = Path(config_path) if config_path is not None else HOMEMASTER_CONFIG_PATH
    if not path.is_absolute():
        from homemaster.runtime import REPO_ROOT

        path = REPO_ROOT / path
    if not path.exists():
        return HomeMasterConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeConfigError(f"invalid homemaster config JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeConfigError(f"homemaster config must be a JSON object: {path}")
    return HomeMasterConfig.model_validate(payload)
