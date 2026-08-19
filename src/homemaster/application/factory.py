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
)
from homemaster.application.session import SessionManager
from homemaster.artifacts import ArtifactPublisher
from homemaster.config import HomeMasterConfig, load_config
from homemaster.devices import (
    DeviceAuditLog,
    DeviceConnectionPool,
    DeviceLeaseManager,
    InMemoryDeviceEventStore,
)
from homemaster.events.bus import EventBus
from homemaster.extensions.hook_runner import HookRunner
from homemaster.permissions import PermissionChecker
from homemaster.prompts.loader import load_prompt
from homemaster.providers.llm_client import LLMClient
from homemaster.tools.base import ToolRegistry
from homemaster.tools.executor import ToolExecutor


def create_application(
    *,
    config: HomeMasterConfig | None = None,
    config_path: str | Path | None = None,
    registry: ToolRegistry,
    tool_executor: ToolExecutor | None = None,
    resource_manager: Any | None = None,
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
    session_end_handler: Any | None = None,
) -> ApplicationRuntime:
    """Build all application-owned services without opening environment resources."""

    resolved_config = config or load_config(config_path)
    if not isinstance(registry, ToolRegistry):
        raise TypeError("registry must be a ToolRegistry")
    configured_tool_names = set(resolved_config.permissions.allowed_tools) | set(
        resolved_config.permissions.denied_tools
    )
    unknown_tool_names = sorted(configured_tool_names - set(registry.all_names()))
    if unknown_tool_names:
        raise ValueError(f"permission config contains unknown tools: {unknown_tool_names}")
    bus = event_bus or EventBus()
    if resource_manager is None:
        device_events = InMemoryDeviceEventStore(
            DeviceAuditLog(
                Path(resolved_config.observability.trace_dir).expanduser() / "device_audit.jsonl"
            )
        )
        resource_manager = ApplicationResourceManager(event_store=device_events)
    resolved_tool_executor = tool_executor or ToolExecutor(
        registry,
        permission_checker=PermissionChecker(resolved_config.permissions),
        resource_manager=resource_manager,
    )
    if resolved_tool_executor.registry is not registry:
        raise ValueError("tool executor must use the application ToolRegistry")
    sessions = session_manager or SessionManager(
        session_root=Path(resolved_config.observability.session_dir).expanduser()
    )
    providers = provider_factory or _provider_factory(resolved_config)
    services = dict(application_services or {})
    assemblers = context_assembler_factory or _context_factory(resolved_config, services)
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
    if isinstance(resource_manager, DeviceLeaseManager):
        connections.bind_lease_manager(resource_manager)
    settings = SimpleNamespace(
        runtime_guards=resolved_config.runtime,
        context=resolved_config.context,
        provider_name=resolved_config.runtime_defaults.default_provider_name,
        device_connection_pool=connections,
        device_lease_manager=resource_manager,
        working_directory=Path.cwd().resolve(strict=True),
        application_services=services,
    )
    return ApplicationRuntime(
        registry=registry,
        tool_executor=resolved_tool_executor,
        event_bus=bus,
        session_manager=sessions,
        provider_factory=providers,
        context_assembler_factory=assemblers,
        settings=settings,
        resource_scope=scope,
        application_starter=application_starter,
        extension_runner=extension_runner,
        artifact_publisher=artifact_publisher,
        session_end_handler=session_end_handler,
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


def _context_factory(
    config: HomeMasterConfig,
    application_services: Mapping[str, object],
) -> ContextAssemblerFactory:
    system_prompt = load_prompt(config.prompts.agent_system_prompt)

    def build(request: RunRequest, provider: Any) -> ContextAssembler:
        profile = _resolve_chat_provider(config, request)
        return ContextAssembler(
            provider=profile,
            policy=config.context,
            system_prompt=system_prompt,
            summary_client=provider,
            skill_registry=application_services.get("skill_registry"),
            frozen_memory_context=application_services.get("frozen_memory_context"),
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
