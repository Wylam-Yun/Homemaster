"""Connection-free Home CLI dry-run resolution."""

from __future__ import annotations

from pathlib import Path

from homemaster.adapters.profiles import build_home_profile
from homemaster.config import ConfigError, HomeMasterConfig, load_config
from homemaster.observations import ObservationService


def build_dry_run_preview(
    *,
    prompt: str | None,
    config: HomeMasterConfig | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    provider_name: str | None = None,
    probe: bool = False,
) -> dict[str, object]:
    """Resolve local config and the Home ToolView without creating clients."""

    resolved = config or load_config()
    provider_error: str | None = None
    try:
        provider = resolved.get_provider(provider_name, kind="chat")
        provider_values = {
            "provider": provider.name,
            "model": provider.model,
            "protocol": provider.protocol,
            "base_url": provider.base_url,
        }
    except ConfigError as exc:
        provider_error = str(exc)
        provider_values = {
            "provider": provider_name or resolved.runtime_defaults.default_provider_name,
            "model": "unknown_until_configured",
            "protocol": "unknown_until_configured",
            "base_url": "",
        }
    profile = build_home_profile(
        observation_service=ObservationService(),
        world_path=world_path,
        memory_path=memory_path,
    )
    return {
        "type": "dry-run",
        "entrypoint": "model_prompt" if prompt else "interactive_session",
        "prompt": prompt,
        "settings": {
            "profile": "home",
            **provider_values,
            "max_tool_iterations": resolved.runtime.max_tool_iterations,
            "session_dir": str(Path(resolved.observability.session_dir).expanduser()),
        },
        "tools": list(profile.manifests()),
        "mcp_discovery": "not_configured" if probe else "unknown_until_connect",
        "probe_requested": probe,
        "external_io": False,
        "validation": {
            "provider": "ok" if provider_error is None else "configuration_required",
            "detail": provider_error or "",
        },
    }


__all__ = ["build_dry_run_preview"]
