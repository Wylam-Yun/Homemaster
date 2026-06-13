"""Resolve provider profiles for runtime model calls."""

from __future__ import annotations

from pathlib import Path

from homemaster.config.model_config import (
    HomeMasterConfig,
    ProviderProfileConfig,
    load_model_config,
)
from homemaster.runtime import DEFAULT_CONFIG_PATH, load_homemaster_config, load_provider_config


def resolve_homemaster_config(config_path: str | Path | None = None) -> HomeMasterConfig:
    """Load typed HomeMaster config, returning defaults when the file is absent."""
    return load_model_config(config_path)


def resolve_provider_profile(
    *,
    config_path: str | Path | None = None,
    provider_name: str | None = None,
) -> ProviderProfileConfig:
    """Resolve provider profile, preferring typed homemaster config.

    If no typed provider is configured, read config/api_config.json.
    """
    raw_config = load_homemaster_config(config_path) if config_path is not None else {}
    use_typed_config = config_path is None or _looks_like_typed_config(raw_config)
    if use_typed_config:
        config = resolve_homemaster_config(config_path)
    else:
        config = HomeMasterConfig()
    if config.providers.items:
        return config.get_provider(provider_name)

    provider = load_provider_config(
        DEFAULT_CONFIG_PATH if config_path is None else config_path,
        provider_name=provider_name or "Mimo",
    )
    return ProviderProfileConfig(
        name=provider.name,
        protocol=provider.protocol,  # type: ignore[arg-type]
        base_url=provider.base_url,
        model=provider.model,
        api_keys=provider.api_keys,
        context_window_tokens=provider.context_window_tokens,
        max_output_tokens=provider.max_output_tokens,
        embedding_url=provider.embedding_url,
    )


def _looks_like_typed_config(config: dict[str, object]) -> bool:
    providers = config.get("providers")
    return isinstance(providers, dict) or any(
        key in config for key in ("context", "runtime", "prompts")
    )
