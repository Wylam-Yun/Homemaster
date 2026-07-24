"""Single composition root for the V1.9 application runtime."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.context import ContextAssembler
from homemaster.application.contracts import ResourceBinding, ResourceLifetime, RunRequest
from homemaster.application.resources import ApplicationResourceManager, RunResourceScope
from homemaster.application.runtime import (
    ApplicationRuntime,
    ApplicationStarter,
    ContextAssemblerFactory,
    ProviderFactory,
    ToolProfile,
)
from homemaster.application.session import SessionManager
from homemaster.artifacts import ArtifactPublisher
from homemaster.config import HomeMasterConfig, configured_sensitive_values, load_config
from homemaster.devices import (
    DeviceAuditLog,
    DeviceConnectionPool,
    DeviceLeaseManager,
    InMemoryDeviceEventStore,
)
from homemaster.events.bus import EventBus
from homemaster.extensions import HookRunner
from homemaster.permissions import HomePermissionPolicy
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
    event_bus: EventBus | None = None,
    session_manager: SessionManager | None = None,
    provider_factory: ProviderFactory | None = None,
    context_assembler_factory: ContextAssemblerFactory | None = None,
    resource_scope: RunResourceScope | None = None,
    application_starter: ApplicationStarter | None = None,
    device_connection_pool: DeviceConnectionPool | None = None,
    extension_runner: HookRunner | None = None,
    artifact_publisher: ArtifactPublisher | None = None,
    application_services: Mapping[str, object] | None = None,
) -> ApplicationRuntime:
    """Build all application-owned services without opening environment resources."""

    resolved_config = config or load_config(config_path)
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
    if pipeline is None:
        device_events = InMemoryDeviceEventStore(
            DeviceAuditLog(
                Path(resolved_config.observability.trace_dir).expanduser() / "device_audit.jsonl"
            )
        )
        execution_pipeline = ToolExecutionPipeline(
            catalog,
            permission_policy=HomePermissionPolicy(resolved_config.permissions),
            resource_manager=ApplicationResourceManager(event_store=device_events),
            public_event_sink=bus,
        )
    else:
        execution_pipeline = pipeline
    if execution_pipeline.catalog is not catalog:
        raise ValueError("pipeline must use the application ToolCatalog")
    execution_pipeline.validate_catalog()
    sessions = session_manager or SessionManager(
        session_root=Path(resolved_config.observability.session_dir).expanduser()
    )
    providers = provider_factory or _provider_factory(resolved_config)
    assemblers = context_assembler_factory or _context_factory(resolved_config)
    scope = resource_scope or RunResourceScope()
    existing_connections = scope.get("device-connection-pool")
    if existing_connections is not None:
        connections = existing_connections.resource
        if not isinstance(connections, DeviceConnectionPool):
            raise TypeError("device-connection-pool resource has an invalid type")
        if device_connection_pool is not None and connections is not device_connection_pool:
            raise ValueError("device connection pool conflicts with the supplied resource scope")
    else:
        connections = device_connection_pool or DeviceConnectionPool()
        scope.bind(
            ResourceBinding.owned(
                "device-connection-pool",
                connections,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
    if isinstance(execution_pipeline.resource_manager, DeviceLeaseManager):
        connections.bind_lease_manager(execution_pipeline.resource_manager)
    settings = SimpleNamespace(
        runtime_guards=resolved_config.runtime,
        context=resolved_config.context,
        provider_name=resolved_config.runtime_defaults.default_provider_name,
        device_connection_pool=connections,
        device_lease_manager=execution_pipeline.resource_manager,
        public_sensitive_values=configured_sensitive_values(resolved_config),
        working_directory=Path.cwd().resolve(strict=True),
        application_services=dict(application_services or {}),
    )
    return ApplicationRuntime(
        catalog=catalog,
        profiles=resolved_profiles,
        pipeline=execution_pipeline,
        event_bus=bus,
        session_manager=sessions,
        provider_factory=providers,
        context_assembler_factory=assemblers,
        settings=settings,
        resource_scope=scope,
        application_starter=application_starter,
        extension_runner=extension_runner,
        artifact_publisher=artifact_publisher,
    )


def _provider_factory(config: HomeMasterConfig) -> ProviderFactory:
    def build(request: RunRequest, run_id: str) -> ResourceBinding:
        profile = _resolve_chat_provider(config, request)
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
        profile = _resolve_chat_provider(config, request)
        return ContextAssembler(
            provider=profile,
            policy=config.context,
            system_prompt=system_prompt,
            summary_client=provider,
            skill_registry=request.dependencies.get("skill_registry"),
        )

    return build


def _resolve_chat_provider(config: HomeMasterConfig, request: RunRequest) -> Any:
    if request.model_override is None:
        return config.get_provider(
            request.provider_name or config.runtime_defaults.default_provider_name,
            kind="chat",
        )
    target = request.model_override.casefold()
    matches = [
        profile
        for profile in config.providers.items
        if profile.kind == "chat"
        and (profile.name.casefold() == target or profile.model.casefold() == target)
    ]
    unique = {profile.name.casefold(): profile for profile in matches}
    if len(unique) != 1:
        raise ValueError(
            f"skill model {request.model_override!r} must map to exactly one "
            "configured chat provider"
        )
    profile = next(iter(unique.values()))
    if (
        request.provider_name is not None
        and profile.name.casefold() != request.provider_name.casefold()
    ):
        raise ValueError("skill model override conflicts with the explicitly selected provider")
    return profile


__all__ = ["create_application"]
