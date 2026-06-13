"""Run-scoped RuntimeSettings — constructed explicitly, never read at import time.

This module uses an explicit loader. Settings are code-default unless the caller
provides a config path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from homemaster.config.model_config import (
    ContextPolicyConfig,
    PromptConfig,
    RuntimeGuardConfig,
)


class RuntimeSettingsError(RuntimeError):
    """Raised when RuntimeSettings configuration is invalid."""


class RuntimeSettings(BaseModel):
    """Run-scoped settings for an AgentRuntime execution.

    Constructed explicitly by the caller (CLI, turn, or test).
    Default values come from code constants, not from user config files.
    """

    run_id: str
    max_turns: int = 12
    runtime_root: Path
    debug_root: Path
    results_root: Path
    provider_name: str = "Mimo"
    embedding_provider_name: str = "MemoryEmbedding"
    config_path: Path | None = None
    memory_path: Path | None = None
    world_path: Path | None = None
    context: ContextPolicyConfig = Field(default_factory=ContextPolicyConfig)
    runtime_guards: RuntimeGuardConfig = Field(default_factory=RuntimeGuardConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)


def load_runtime_settings(
    config_path: Path | None = None,
    **overrides: Any,
) -> RuntimeSettings:
    """Load RuntimeSettings from explicit config + overrides.

    If config_path is provided and exists, reads runtime_defaults section.
    Overrides take precedence over config values.
    Code defaults are used for any value not in config or overrides.
    """
    from homemaster.runtime import (
        get_config_section,
        load_homemaster_config,
    )

    defaults: dict[str, Any] = {}
    if config_path is not None:
        cfg = get_config_section(load_homemaster_config(config_path), "runtime_defaults")
        if cfg:
            if "default_provider_name" in cfg:
                defaults["provider_name"] = cfg["default_provider_name"]
            if "default_embedding_provider_name" in cfg:
                defaults["embedding_provider_name"] = cfg["default_embedding_provider_name"]

    if config_path is not None:
        raw_config = load_homemaster_config(config_path)
        if any(key in raw_config for key in ("context", "runtime", "prompts")):
            from homemaster.config.model_config import load_model_config

            model_config = load_model_config(config_path)
            defaults["context"] = model_config.context
            defaults["runtime_guards"] = model_config.runtime
            defaults["prompts"] = model_config.prompts
    defaults.update(overrides)
    return RuntimeSettings(**defaults)
