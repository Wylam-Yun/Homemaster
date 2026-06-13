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

    If no typed provider is configured, fall back to the legacy API config
    loader so existing local development configs continue to run.
    """
    raw_config = load_homemaster_config(config_path) if config_path is not None else {}
    use_typed_config = config_path is None or _looks_like_typed_config(raw_config)
    if use_typed_config:
        config = resolve_homemaster_config(config_path)
    else:
        config = HomeMasterConfig()
    if config.providers.items:
        return config.get_provider(provider_name)

    legacy_provider = load_provider_config(
        DEFAULT_CONFIG_PATH if config_path is None else config_path,
        provider_name=provider_name or "Mimo",
    )
    return ProviderProfileConfig(
        name=legacy_provider.name,
        protocol=legacy_provider.protocol,  # type: ignore[arg-type]
        base_url=legacy_provider.base_url,
        model=legacy_provider.model,
        api_keys=legacy_provider.api_keys,
        context_window_tokens=legacy_provider.context_window_tokens,
        max_output_tokens=legacy_provider.max_output_tokens,
        embedding_url=legacy_provider.embedding_url,
    )


def _looks_like_typed_config(config: dict[str, object]) -> bool:
    providers = config.get("providers")
    return isinstance(providers, dict) or any(
        key in config for key in ("context", "runtime", "prompts")
    )
