"""Outer Home CLI composition for the V1.9 application runtime."""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from homemaster.adapters.profiles import (
    build_tool_registry,
)
from homemaster.application import (
    ApplicationRuntime,
    ResourceBinding,
    ResourceLifetime,
    SessionManager,
)
from homemaster.application.factory import create_application
from homemaster.application.resources import RunResourceScope
from homemaster.artifacts import ArtifactPublisher, ToolOutputStore
from homemaster.channels.feishu_groups import FeishuGroupOperations, build_feishu_group_tools
from homemaster.cli.live_output import RichStreamEventSink
from homemaster.cli.rich_renderer import RichOutputRenderer
from homemaster.config import HomeMasterConfig, load_config
from homemaster.events.bus import EventBus
from homemaster.events.sinks import (
    FanoutEventSink,
    JsonlTraceSink,
    MessagesLogSink,
)
from homemaster.events.third_party_logging import ThirdPartyLogCapture
from homemaster.experience import (
    DreamingCoordinator,
    DreamingStateStore,
    SessionFinalizationController,
    SessionFinalizer,
)
from homemaster.extensions.contracts import ExtensionApproval
from homemaster.extensions.hook_runner import HookRunner
from homemaster.mcp.adapter import build_mcp_registered_tools, register_mcp_tools_atomically
from homemaster.mcp.audit import McpAuditLog
from homemaster.mcp.client import Connector, McpClientManager
from homemaster.memory.add_queue import MemoryAddQueue
from homemaster.memory.context_service import FrozenMemoryContextService
from homemaster.memory.enrichment_queue import MemoryEnrichmentQueue
from homemaster.memory.evidence import MemoryEvidenceLedger
from homemaster.memory.file_store import FileMemoryStore
from homemaster.memory.managed_neo4j import ManagedNeo4jRuntime
from homemaster.memory.migration import MemoryMigrationCoordinator
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.permissions import PermissionMode, PermissionSettingsConfig
from homemaster.prompts.loader import PromptId
from homemaster.skills.loader import load_skill_registry
from homemaster.skills.registry import SkillRegistry
from homemaster.tools.adapters import from_registered_tool
from homemaster.tools.base import ToolRegistry
from homemaster.tools.runtime_services import HomeToolServices

if TYPE_CHECKING:
    from homemaster.extensions.reloader import ExtensionReloader


@dataclass(frozen=True)
class HomeApplicationBundle:
    application: ApplicationRuntime
    config: HomeMasterConfig
    run_dir: Path
    trace_path: Path
    skill_registry: SkillRegistry
    mcp_manager: McpClientManager | None = None
    mcp_audit_path: Path | None = None
    extension_runner: HookRunner | None = None
    extension_reloader: ExtensionReloader | None = None
    live_rendered: bool = False
    tool_services: HomeToolServices | None = None
    mindmemos: EmbeddedMindMemOS | None = None
    memory_add_queue: MemoryAddQueue | None = None
    memory_enrichment_queue: MemoryEnrichmentQueue | None = None
    dreaming_coordinator: DreamingCoordinator | None = None
    session_finalization: SessionFinalizationController | None = None


class HomeCliBackend:
    """Borrowed Home backend, including the current desktop screenshot source."""

    def __init__(
        self,
        *,
        world_path: Path | None,
        memory_path: Path | None,
    ) -> None:
        self.backend_id = f"home-cli:{uuid.uuid4().hex[:12]}"
        self.run_id = "unbound"
        self.generation = 0
        self.state_sequence = 0
        self.event_sequence = 0
        self.world_path = world_path
        self.memory_path = memory_path

    def bind_application_run(self, run_id: str, generation: int) -> None:
        self.run_id = run_id
        self.generation = generation

    def advance(self) -> None:
        self.state_sequence += 1
        self.event_sequence += 1

    async def screenshot(self) -> bytes:
        return await asyncio.to_thread(self._capture_display_png)

    @staticmethod
    def _capture_display_png() -> bytes:
        from PIL import ImageGrab

        display = os.environ.get("DISPLAY")
        kwargs = {"xdisplay": display} if display else {}
        image = ImageGrab.grab(**kwargs)
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()


def create_home_application(
    *,
    config: HomeMasterConfig | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    run_label: str | None = None,
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    console_show_replies: bool = True,
    mcp_connector: Connector | None = None,
    event_sink: Any | None = None,
    feishu_group_operations: FeishuGroupOperations | None = None,
    tool_environment: Literal["local_robot", "alfworld", "coworker", "browser"] | None = (
        "local_robot"
    ),
    runtime_root: Path | None = None,
    session_root: Path | None = None,
    memory_tenant_id: str = "local",
    session_finalizer_trace_path: Path | None = None,
    permission_mode: PermissionMode | None = None,
    confirmation_handler: Any | None = None,
) -> HomeApplicationBundle:
    """Compose one Home application without opening provider connections."""

    resolved = config or load_config()
    permission_settings = resolved.permissions
    if permission_mode is not None:
        if not isinstance(permission_mode, PermissionMode):
            raise TypeError("permission_mode must be PermissionMode or None")
        payload = permission_settings.model_dump(mode="python")
        payload["mode"] = permission_mode
        permission_settings = PermissionSettingsConfig.model_validate(payload)
    if tool_environment == "browser":
        resolved = resolved.model_copy(
            update={
                "prompts": resolved.prompts.model_copy(
                    update={"agent_system_prompt": PromptId.BROWSER_GATEWAY.value}
                )
            }
        )
    label = run_label or f"cli-{uuid.uuid4().hex[:12]}"
    run_dir = (
        runtime_root.expanduser().resolve()
        if runtime_root is not None
        else Path(resolved.runtime.runtime_root).expanduser() / label
    )
    registry = build_tool_registry(
        environment=tool_environment,
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=run_dir / "memory",
        memory_enabled=resolved.memory.enabled,
    )
    if feishu_group_operations is not None:
        group_tools = build_feishu_group_tools(feishu_group_operations)
        registry.register_many([from_registered_tool(tool) for tool in group_tools])
    extension_runner: HookRunner | None = None
    extension_reloader: ExtensionReloader | None = None
    extension_generation = None
    extension_disposer = None
    if resolved.extensions.approvals:
        from homemaster.extensions.loader import (
            dispose_extension_generation,
            load_extension_generation,
            register_extension_tools_atomically,
        )
        from homemaster.extensions.reloader import ExtensionReloader

        extension_disposer = dispose_extension_generation
        approvals = _extension_approvals(resolved)
        extension_generation = load_extension_generation(approvals, generation=1)
        try:
            register_extension_tools_atomically(registry, extension_generation)
        except BaseException:
            dispose_extension_generation(extension_generation)
            raise
        extension_runner = HookRunner(extension_generation)
        extension_reloader = ExtensionReloader(extension_runner)
    try:
        return _finish_home_application(
            resolved=resolved,
            label=label,
            registry=registry,
            extension_runner=extension_runner,
            extension_reloader=extension_reloader,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
            console_show_replies=console_show_replies,
            mcp_connector=mcp_connector,
            event_sink=event_sink,
            feishu_group_operations=feishu_group_operations,
            run_dir=run_dir,
            session_root=session_root,
            memory_tenant_id=memory_tenant_id,
            session_finalizer_trace_path=session_finalizer_trace_path,
            permission_settings=permission_settings,
            confirmation_handler=confirmation_handler,
        )
    except BaseException:
        if extension_generation is not None and extension_disposer is not None:
            extension_disposer(extension_generation)
        raise


def _finish_home_application(
    *,
    resolved: HomeMasterConfig,
    label: str,
    registry: ToolRegistry,
    extension_runner: HookRunner | None,
    extension_reloader: ExtensionReloader | None,
    progress: bool,
    verbose: bool,
    quiet: bool,
    console_show_replies: bool,
    mcp_connector: Connector | None,
    event_sink: Any | None,
    feishu_group_operations: FeishuGroupOperations | None,
    run_dir: Path,
    session_root: Path | None,
    memory_tenant_id: str,
    session_finalizer_trace_path: Path | None,
    permission_settings: PermissionSettingsConfig,
    confirmation_handler: Any | None,
) -> HomeApplicationBundle:
    """Finish composition while the caller retains extension rollback ownership."""

    artifact_publisher: ArtifactPublisher | None = None
    skill_registry = load_home_skills(resolved)
    bus = EventBus()
    scope = RunResourceScope()
    if feishu_group_operations is not None:
        scope.bind(
            ResourceBinding.owned(
                "feishu-api-service",
                feishu_group_operations.api_service,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
    if resolved.gateway.enabled and resolved.gateway.feishu.enabled:
        gateway_store = ToolOutputStore(
            run_dir / "gateway-artifacts",
            quota_bytes=256 * 1024 * 1024,
            ttl_seconds=3600,
        )
        scope.bind(
            ResourceBinding.owned(
                "gateway-tool-output-store",
                gateway_store,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
        artifact_publisher = ArtifactPublisher(gateway_store)
    trace = JsonlTraceSink(run_dir)
    scope.bind(
        ResourceBinding.owned(
            "cli-trace",
            trace,
            lifetime=ResourceLifetime.APPLICATION,
        )
    )
    sinks: list[Any] = [trace, MessagesLogSink(run_dir)]
    live_rendered = False
    if event_sink is not None:
        sinks.append(event_sink)
    if progress or verbose:
        if not quiet:
            rich_sink = RichStreamEventSink(RichOutputRenderer())
            sinks.append(rich_sink)
            live_rendered = True
            scope.bind(
                ResourceBinding.owned(
                    "cli-rich-output",
                    rich_sink,
                    lifetime=ResourceLifetime.APPLICATION,
                )
            )
    unsubscribe = bus.subscribe(FanoutEventSink(sinks).emit)
    scope.bind(
        ResourceBinding.owned(
            "cli-event-subscription",
            unsubscribe,
            lifetime=ResourceLifetime.APPLICATION,
            release=lambda callback: callback(),
        )
    )
    mcp_manager: McpClientManager | None = None
    mcp_audit_path: Path | None = None
    starter_steps: list[Any] = []
    third_party_logs = ThirdPartyLogCapture(run_dir / "third_party.log")
    scope.bind(
        ResourceBinding.owned(
            "third-party-log-capture",
            third_party_logs,
            lifetime=ResourceLifetime.APPLICATION,
        )
    )

    async def start_third_party_logs(_application: ApplicationRuntime) -> None:
        third_party_logs.start()

    starter_steps.append(start_third_party_logs)
    service_state_root = Path(resolved.observability.session_dir).expanduser().resolve().parent
    tool_services = HomeToolServices(resolved, state_root=service_state_root)
    scope.bind(
        ResourceBinding.owned(
            "homemaster-tool-services",
            tool_services,
            lifetime=ResourceLifetime.APPLICATION,
        )
    )
    file_memory_store: FileMemoryStore | None = None
    frozen_memory_context: FrozenMemoryContextService | None = None
    memory_evidence_ledger: MemoryEvidenceLedger | None = None
    managed_neo4j: ManagedNeo4jRuntime | None = None
    mindmemos: EmbeddedMindMemOS | None = None
    memory_add_queue: MemoryAddQueue | None = None
    memory_enrichment_queue: MemoryEnrichmentQueue | None = None
    dreaming_coordinator: DreamingCoordinator | None = None
    memory_migration: MemoryMigrationCoordinator | None = None
    if resolved.memory.enabled:
        memory_migration = MemoryMigrationCoordinator(resolved.memory)
        file_memory_store = FileMemoryStore(resolved.memory)
        frozen_memory_context = FrozenMemoryContextService(file_memory_store)
        memory_evidence_ledger = MemoryEvidenceLedger(resolved.memory.evidence_db_path)
        if resolved.memory.neo4j.mode == "managed_local":
            managed_neo4j = ManagedNeo4jRuntime(resolved.memory)
        mindmemos = EmbeddedMindMemOS(resolved)
        memory_add_queue = MemoryAddQueue(
            mindmemos,
            audit_path=resolved.memory.data_root / "mindmemos" / "add_jobs.jsonl",
        )
        memory_enrichment_queue = MemoryEnrichmentQueue(
            mindmemos,
            audit_path=resolved.memory.data_root / "mindmemos" / "enrichment_jobs.jsonl",
            concurrency=2,
        )
        dreaming_coordinator = DreamingCoordinator(
            store=DreamingStateStore(
                resolved.memory.data_root,
                threshold=resolved.memory.dreaming_memory_threshold,
            ),
            mindmemos=mindmemos,
            event_sink=bus,
        )
        mindmemos._third_party_logs = third_party_logs
        scope.bind(
            ResourceBinding.owned(
                "file-memory-store",
                file_memory_store,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
        scope.bind(
            ResourceBinding.owned(
                "memory-evidence-ledger",
                memory_evidence_ledger,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
        if managed_neo4j is not None:
            scope.bind(
                ResourceBinding.owned(
                    "managed-neo4j",
                    managed_neo4j,
                    lifetime=ResourceLifetime.APPLICATION,
                )
            )
        scope.bind(
            ResourceBinding.owned(
                "embedded-mindmemos",
                mindmemos,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
        scope.bind(
            ResourceBinding.owned(
                "memory-add-queue",
                memory_add_queue,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
        scope.bind(
            ResourceBinding.owned(
                "memory-enrichment-queue",
                memory_enrichment_queue,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )

        async def start_file_memory(_application: ApplicationRuntime) -> None:
            assert file_memory_store is not None
            assert memory_evidence_ledger is not None
            assert mindmemos is not None
            assert memory_migration is not None
            memory_migration.ensure_ready(auto_migrate=True)
            file_memory_store.start()
            memory_evidence_ledger.start()
            if managed_neo4j is not None:
                await managed_neo4j.start()
            await mindmemos.start()
            if managed_neo4j is not None and not mindmemos.available:
                cause = mindmemos.unavailable_cause or "unknown startup failure"
                raise RuntimeError(f"Embedded MindMemOS is unavailable: {cause}")
            await memory_add_queue.start()
            assert memory_enrichment_queue is not None
            await memory_enrichment_queue.start()
            from mindmemos.typing import MemoryRequestContext

            assert dreaming_coordinator is not None
            await dreaming_coordinator.retry_pending(
                project_id="local",
                user_id="local",
                context_template=MemoryRequestContext(
                    request_id="startup-dreaming-recovery",
                    account_id="local",
                    project_id="local",
                    api_key_uuid="embedded-local",
                    user_id="local",
                    app_id="homemaster",
                    session_id=None,
                    agent_id="homemaster",
                ),
            )

        starter_steps.append(start_file_memory)
    if resolved.mcp.servers:
        mcp_audit_path = run_dir / "mcp_audit.jsonl"
        audit_log = McpAuditLog(mcp_audit_path)
        mcp_manager = McpClientManager(
            resolved.mcp.servers,
            connector=mcp_connector,
            connect_timeout_s=resolved.mcp.connect_timeout_s,
            call_timeout_s=resolved.mcp.call_timeout_s,
            audit_sink=audit_log,
        )
        scope.bind(
            ResourceBinding.owned(
                "mcp-manager",
                mcp_manager,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )

        async def start_mcp(application: ApplicationRuntime) -> None:
            assert mcp_manager is not None
            await mcp_manager.connect_all()
            store = ToolOutputStore(
                Path(resolved.mcp.artifact_root),
                quota_bytes=resolved.mcp.artifact_quota_bytes,
                ttl_seconds=resolved.mcp.artifact_ttl_seconds,
            )
            application.resource_scope.bind(
                ResourceBinding.owned(
                    "mcp-tool-output-store",
                    store,
                    lifetime=ResourceLifetime.APPLICATION,
                )
            )
            registered = build_mcp_registered_tools(
                mcp_manager,
                store,
                preview_chars=resolved.mcp.preview_chars,
            )
            register_mcp_tools_atomically(application.registry, registered)

        starter_steps.append(start_mcp)

    finalizer = (
        SessionFinalizer(
            trace_path=session_finalizer_trace_path or run_dir / "runtime_events.jsonl",
            data_root=resolved.memory.data_root,
            mindmemos=mindmemos,
            memory_tenant_id=memory_tenant_id,
            dreaming_coordinator=dreaming_coordinator,
            event_sink=bus,
        )
        if mindmemos is not None and memory_add_queue is not None
        else None
    )

    session_finalization = (
        SessionFinalizationController(
            finalizer,
            memory_add_queue,
            ready=lambda: mindmemos.available,
        )
        if finalizer is not None and memory_add_queue is not None
        else None
    )
    session_end_handler = session_finalization.enqueue if session_finalization is not None else None

    async def start_application_services(application: ApplicationRuntime) -> None:
        for starter in starter_steps:
            await starter(application)

    application_starter = start_application_services if starter_steps else None
    application = create_application(
        config=resolved,
        registry=registry,
        event_bus=bus,
        session_manager=(
            SessionManager(session_root=session_root.expanduser().resolve())
            if session_root is not None
            else None
        ),
        resource_scope=scope,
        application_starter=application_starter,
        extension_runner=extension_runner,
        artifact_publisher=artifact_publisher,
        application_services={
            "skill_registry": skill_registry,
            "tool_services": tool_services,
            "task_manager": tool_services.tasks,
            "cron_store": tool_services.cron,
            "team_registry": tool_services.teams,
            "plan_mode": tool_services.plan_mode,
            "home_config": tool_services.config,
            **(
                {
                    "file_memory_store": file_memory_store,
                    "frozen_memory_context": frozen_memory_context,
                    "memory_evidence_ledger": memory_evidence_ledger,
                    "mindmemos": mindmemos,
                    "memory_add_queue": memory_add_queue,
                    "memory_enrichment_queue": memory_enrichment_queue,
                    "dreaming_coordinator": dreaming_coordinator,
                    "memory_migration": memory_migration,
                    **({"managed_neo4j": managed_neo4j} if managed_neo4j is not None else {}),
                    "memory_audit_path": Path(resolved.observability.trace_dir).expanduser()
                    / "memory_operations.jsonl",
                }
                if file_memory_store is not None
                and frozen_memory_context is not None
                and memory_evidence_ledger is not None
                and mindmemos is not None
                else {}
            ),
            **_image_provider_services(resolved),
            **({"mcp_manager": mcp_manager} if mcp_manager is not None else {}),
        },
        session_end_handler=session_end_handler,
        permission_settings=permission_settings,
        confirmation_handler=confirmation_handler,
    )
    return HomeApplicationBundle(
        application=application,
        config=resolved,
        run_dir=run_dir,
        trace_path=run_dir / "runtime_events.jsonl",
        skill_registry=skill_registry,
        mcp_manager=mcp_manager,
        mcp_audit_path=mcp_audit_path,
        extension_runner=extension_runner,
        extension_reloader=extension_reloader,
        live_rendered=live_rendered,
        tool_services=tool_services,
        mindmemos=mindmemos,
        memory_add_queue=memory_add_queue,
        memory_enrichment_queue=memory_enrichment_queue,
        dreaming_coordinator=dreaming_coordinator,
        session_finalization=session_finalization,
    )


def _image_provider_services(config: HomeMasterConfig) -> dict[str, object]:
    try:
        provider = config.get_provider(
            config.runtime_defaults.default_provider_name,
            kind="chat",
        )
    except Exception:
        return {}
    api_key = provider.api_keys[0] if provider.api_keys else ""
    common = {
        "model": provider.model,
        "api_key": api_key,
        "base_url": provider.base_url,
    }
    return {
        "vision_model_config": dict(common),
        "image_generation_config": {**common, "provider": "openai"},
    }


def _extension_approvals(config: HomeMasterConfig) -> tuple[ExtensionApproval, ...]:
    config_dir = config.config_path.parent if config.config_path is not None else Path.cwd()
    approvals: list[ExtensionApproval] = []
    for value in config.extensions.approvals:
        manifest_path = value.manifest_path.expanduser()
        if not manifest_path.is_absolute():
            manifest_path = config_dir / manifest_path
        approvals.append(
            ExtensionApproval(
                manifest_path=manifest_path,
                extension_id=value.extension_id,
                version=value.version,
                expected_sha256=value.expected_sha256,
                granted_capabilities=value.granted_capabilities,
                enabled_tool_ids=value.enabled_tool_ids,
            )
        )
    return tuple(approvals)


def load_home_skills(
    config: HomeMasterConfig,
    *,
    cwd: Path | None = None,
) -> SkillRegistry:
    """Load HomeMaster's independent, dynamically refreshable Skill sources."""

    sources = config.skills
    discovery_cwd = cwd or Path.cwd()

    def discover() -> SkillRegistry:
        return load_skill_registry(
            cwd=discovery_cwd,
            user_dirs=sources.user_dirs,
            project_dirs=sources.project_dirs,
            explicit_dirs=sources.explicit_dirs,
            allow_project=sources.allow_project,
            plugin_roots=sources.plugin_roots,
            enabled_plugins=sources.enabled_plugins,
            allow_project_plugin_skills=sources.allow_project_plugin_skills,
            allowed_builtin_overrides=sources.allowed_builtin_overrides,
        )

    registry = discover()
    registry.set_refresher(discover)
    return registry


__all__ = [
    "HomeApplicationBundle",
    "HomeCliBackend",
    "create_home_application",
    "load_home_skills",
]
