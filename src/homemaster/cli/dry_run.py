"""Connection-free Home CLI dry-run resolution."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homemaster.adapters.profiles import build_universal_tool_registry
from homemaster.cli.composition import load_home_skills
from homemaster.config import ConfigError, HomeMasterConfig, load_config
from homemaster.mcp.audit import McpAuditLog
from homemaster.mcp.client import Connector, McpClientManager


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
    """Resolve local config and the universal tool Registry without creating clients."""

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
    registry = build_universal_tool_registry(
        world_path=world_path,
        memory_path=memory_path,
        memory_enabled=resolved.memory.enabled,
    )
    skill_registry = load_home_skills(resolved)
    mcp_statuses: list[dict[str, object]] = []
    external_io = False
    if probe and resolved.mcp.servers:
        mcp_statuses = asyncio.run(_probe_mcp(resolved))
        mcp_discovery = "probed"
        external_io = True
    elif resolved.mcp.servers:
        mcp_discovery = "unknown_until_connect"
    else:
        mcp_discovery = "not_configured"
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
        "tools": registry.to_api_schema(),
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
                "provenance": [item.source for item in skill_registry.provenance_for(skill)],
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
        "mcp": resolved.mcp.public_summary(),
        "mcp_discovery": mcp_discovery,
        "mcp_statuses": mcp_statuses,
        "probe_requested": probe,
        "external_io": external_io,
        "validation": {
            "provider": "ok" if provider_error is None else "configuration_required",
            "detail": provider_error or "",
        },
    }


async def _probe_mcp(
    config: HomeMasterConfig,
    *,
    connector: Connector | None = None,
) -> list[dict[str, object]]:
    """Probe configured MCP servers without creating an application runtime."""

    audit_path = Path(config.observability.trace_dir).expanduser() / "mcp_probe_audit.jsonl"
    manager = McpClientManager(
        config.mcp.servers,
        connector=connector,
        connect_timeout_s=config.mcp.connect_timeout_s,
        call_timeout_s=config.mcp.call_timeout_s,
        audit_sink=McpAuditLog(audit_path),
    )
    try:
        await manager.connect_all()
        return [
            {
                "name": status.name,
                "state": status.state,
                "transport": status.transport,
                "auth_configured": status.auth_configured,
                "error_code": status.error_code,
                "detail": status.detail,
                "tool_count": len(status.tools),
                "resource_count": len(status.resources),
            }
            for status in manager.list_statuses()
        ]
    finally:
        await manager.aclose()


__all__ = ["build_dry_run_preview"]
