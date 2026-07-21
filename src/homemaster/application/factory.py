"""Single composition root for the V1.9 application runtime."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.context import ContextAssembler
from homemaster.application.contracts import ResourceBinding, ResourceLifetime, RunRequest
from homemaster.application.resources import RunResourceScope
from homemaster.application.runtime import (
    ApplicationRuntime,
    ContextAssemblerFactory,
    ProviderFactory,
    ToolProfile,
)
from homemaster.application.session import SessionManager
from homemaster.config import HomeMasterConfig, load_config
from homemaster.events.bus import EventBus
from homemaster.observations import ObservationService
from homemaster.prompts.loader import load_prompt
from homemaster.providers.llm_client import LLMClient
from homemaster.tools.catalog import ToolCatalog
from homemaster.tools.pipeline import ToolExecutionPipeline


def create_application(
    *,
    config: HomeMasterConfig | None = None,
    config_path: str | Path | None = None,
    profiles: Mapping[str, ToolProfile] | None = None,
    catalog: ToolCatalog | None = None,
    pipeline: ToolExecutionPipeline | None = None,
    observation_service: ObservationService | None = None,
    event_bus: EventBus | None = None,
    session_manager: SessionManager | None = None,
    provider_factory: ProviderFactory | None = None,
    context_assembler_factory: ContextAssemblerFactory | None = None,
    resource_scope: RunResourceScope | None = None,
) -> ApplicationRuntime:
    """Build all application-owned services without opening environment resources."""

    resolved_config = config or load_config(config_path)
    service = observation_service or ObservationService()
    if profiles is None:
        raise ValueError(
            "application tool profiles must be composed and supplied by the outer entry point"
        )
    resolved_profiles = dict(profiles)
    if not resolved_profiles:
        raise ValueError("application requires at least one tool profile")
    profile_catalogs = {
        id(profile.catalog): profile.catalog for profile in resolved_profiles.values()
    }
    if catalog is None:
        if len(profile_catalogs) != 1:
            raise ValueError("all profiles must share one application ToolCatalog")
        catalog = next(iter(profile_catalogs.values()))
    elif any(profile.catalog is not catalog for profile in resolved_profiles.values()):
        raise ValueError("profiles must use the supplied application ToolCatalog")
    bus = event_bus or EventBus()
    execution_pipeline = pipeline or ToolExecutionPipeline(
        catalog,
        observation_service=service,
        public_event_sink=bus,
    )
    if execution_pipeline.catalog is not catalog:
        raise ValueError("pipeline must use the application ToolCatalog")
    execution_pipeline.validate_catalog()
    sessions = session_manager or SessionManager(
        session_root=Path(resolved_config.observability.session_dir).expanduser()
    )
    providers = provider_factory or _provider_factory(resolved_config)
    assemblers = context_assembler_factory or _context_factory(resolved_config)
    settings = SimpleNamespace(
        runtime_guards=resolved_config.runtime,
        context=resolved_config.context,
        provider_name=resolved_config.runtime_defaults.default_provider_name,
    )
    return ApplicationRuntime(
        catalog=catalog,
        profiles=resolved_profiles,
        pipeline=execution_pipeline,
        observation_service=service,
        event_bus=bus,
        session_manager=sessions,
        provider_factory=providers,
        context_assembler_factory=assemblers,
        settings=settings,
        resource_scope=resource_scope,
    )


def _provider_factory(config: HomeMasterConfig) -> ProviderFactory:
    def build(request: RunRequest, run_id: str) -> ResourceBinding:
        profile = config.get_provider(
            request.provider_name or config.runtime_defaults.default_provider_name,
            kind="chat",
        )
        return ResourceBinding.owned(
            f"provider:{run_id}",
            LLMClient(
                profile,
                timeout_s=config.provider_client.timeout_s,
                run_id=run_id,
            ),
            lifetime=ResourceLifetime.RUN,
        )

    return build


def _context_factory(config: HomeMasterConfig) -> ContextAssemblerFactory:
    system_prompt = load_prompt(config.prompts.agent_system_prompt)

    def build(request: RunRequest, provider: Any) -> ContextAssembler:
        profile = config.get_provider(
            request.provider_name or config.runtime_defaults.default_provider_name,
            kind="chat",
        )
        return ContextAssembler(
            provider=profile,
            policy=config.context,
            system_prompt=system_prompt,
            summary_client=provider,
        )

    return build


__all__ = ["create_application"]
