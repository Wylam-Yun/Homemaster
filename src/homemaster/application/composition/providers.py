"""Provider-specific composition helpers."""

from __future__ import annotations

from homemaster.config import HomeMasterConfig


def image_provider_services(config: HomeMasterConfig) -> dict[str, object]:
    """Expose the configured chat provider to image/vision tool services."""

    try:
        provider = config.get_provider(config.runtime_defaults.default_provider_name, kind="chat")
    except Exception:
        return {}
    api_key = provider.api_keys[0] if provider.api_keys else ""
    common = {"model": provider.model, "api_key": api_key, "base_url": provider.base_url}
    return {
        "vision_model_config": dict(common),
        "image_generation_config": {**common, "provider": "openai"},
    }


__all__ = ["image_provider_services"]
