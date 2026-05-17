"""Run-scoped RuntimeSettings — constructed explicitly, never read at import time.

Unlike legacy modules (runtime.py, token_budget.py, etc.) that read config at
import time for backward compatibility with the subprocess+reload test pattern,
this module uses an explicit loader. Settings are code-default unless the caller
provides a config path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, model_validator


class RuntimeSettingsError(RuntimeError):
    """Raised when RuntimeSettings configuration is invalid."""


class RuntimeSettings(BaseModel):
    """Run-scoped settings for an AgentRuntime execution.

    Constructed explicitly by the caller (CLI, task_runner, or test).
    Default values come from code constants, not from user config files.
    """

    run_id: str
    skill_mode: Literal["simulated", "real"] = "simulated"
    max_turns: int = 12
    runtime_root: Path
    debug_root: Path
    results_root: Path
    provider_name: str = "Mimo"
    embedding_provider_name: str = "MemoryEmbedding"
    config_path: Path | None = None
    scenario: str | None = None
    scenario_root: Path | None = None
    memory_path: Path | None = None
    world_path: Path | None = None
    case_dir: Path | None = None
    recovery_max_attempts: int = 3
    executor_step_multiplier: float = 1.0
    executor_minimum_max_steps: int = 5

    @model_validator(mode="after")
    def _reject_real_skill_mode(self) -> RuntimeSettings:
        if self.skill_mode == "real":
            raise RuntimeSettingsError(
                "skill_mode='real' is not yet supported. "
                "Real executor is not integrated. Use skill_mode='simulated'."
            )
        return self


_DEPRECATED_KEYS = {"live_models", "mock_skills"}

_DEPRECATION_MESSAGES = {
    "live_models": (
        "runtime_defaults.live_models is no longer supported. "
        "Deterministic runtime mode has been removed."
    ),
    "mock_skills": (
        "runtime_defaults.mock_skills is no longer supported. "
        "Use skill_mode='simulated' instead."
    ),
}


def load_runtime_settings(
    config_path: Path | None = None,
    **overrides: Any,
) -> RuntimeSettings:
    """Load RuntimeSettings from explicit config + overrides.

    If config_path is provided and exists, reads runtime_defaults section.
    Overrides take precedence over config values.
    Code defaults are used for any value not in config or overrides.

    Raises RuntimeSettingsError if deprecated keys (live_models, mock_skills)
    are present in config, or if skill_mode is set to "real" (not yet
    supported — real executor is not integrated).
    """
    from homemaster.runtime import (
        get_config_section,
        load_homemaster_config,
    )

    defaults: dict[str, Any] = {}
    if config_path is not None:
        cfg = get_config_section(load_homemaster_config(config_path), "runtime_defaults")
        if cfg:
            for key in _DEPRECATED_KEYS:
                if key in cfg:
                    raise RuntimeSettingsError(_DEPRECATION_MESSAGES[key])
            if "skill_mode" in cfg:
                defaults["skill_mode"] = cfg["skill_mode"]
            if "default_provider_name" in cfg:
                defaults["provider_name"] = cfg["default_provider_name"]
            if "default_embedding_provider_name" in cfg:
                defaults["embedding_provider_name"] = cfg["default_embedding_provider_name"]

    defaults.update(overrides)
    merged_skill_mode = defaults.get("skill_mode", "simulated")
    if merged_skill_mode == "real":
        raise RuntimeSettingsError(
            "skill_mode='real' is not yet supported. "
            "Real executor is not integrated. Use skill_mode='simulated'."
        )
    return RuntimeSettings(**defaults)
