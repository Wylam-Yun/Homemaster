"""Connection-free Home CLI dry-run resolution."""

from __future__ import annotations

from pathlib import Path

from homemaster.adapters.profiles import build_home_profile
from homemaster.cli.composition import load_home_skills
from homemaster.config import ConfigError, HomeMasterConfig, load_config
from homemaster.observations import ObservationService


def build_dry_run_preview(
    *,
    prompt: str | None,
    config: HomeMasterConfig | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    probe: bool = False,
) -> dict[str, object]:
    """Resolve local config and the Home ToolView without creating clients."""

    overrides = (
        {f"providers.{provider_name or 'default'}.model": model} if model is not None else None
    )
    resolved = config or load_config(cli_overrides=overrides)
    provider_error: str | None = None
    try:
        provider = resolved.get_provider(provider_name, kind="chat")
        summary = provider.public_summary()
        provider_values = {
            "provider": provider.name,
            "model": provider.model,
            "protocol": provider.protocol,
            "base_url": summary["base_url"],
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
    skill_registry = load_home_skills(resolved, profile)
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
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "tool_names": list(skill.tool_names),
                "source": skill.source,
                "provenance": [item.source for item in skill.provenance],
                "user_invocable": skill.user_invocable,
                "model_invocable": not skill.disable_model_invocation,
            }
            for skill in skill_registry.all()
        ],
        "skill_diagnostics": {
            "loaded": len(skill_registry.all()),
            "rejected": len(skill_registry.issues),
            "rejected_by_code": sorted(issue.code for issue in skill_registry.issues),
        },
        "config_sources": {
            "provider": resolved.field_source("providers.default"),
            "model": resolved.field_source(f"providers.{provider_values['provider']}.model"),
        },
        "mcp_discovery": "not_configured" if probe else "unknown_until_connect",
        "probe_requested": probe,
        "external_io": False,
        "validation": {
            "provider": "ok" if provider_error is None else "configuration_required",
            "detail": provider_error or "",
        },
    }


__all__ = ["build_dry_run_preview"]
