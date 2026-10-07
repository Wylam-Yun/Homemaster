"""Public application composition for the HomeMaster runtime."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from homemaster.alfworld.trajectory_memory import AlfworldTrajectoryWriter
from homemaster.application import (
    ApplicationRuntime,
    ResourceBinding,
    ResourceLifetime,
    SessionManager,
)
from homemaster.application.composition.observability import compose_observability_sinks
from homemaster.application.composition.profiles import resolve_tool_environment
from homemaster.application.composition.providers import image_provider_services
from homemaster.application.composition.skills import compose_skill_registry
from homemaster.application.composition.tools import compose_tool_registry
from homemaster.application.factory import create_application
from homemaster.application.resources import RunResourceScope
from homemaster.artifacts import ArtifactPublisher, ToolOutputStore
from homemaster.channels.feishu_groups import FeishuGroupOperations, build_feishu_group_tools
from homemaster.cli.live_output import RichStreamEventSink
from homemaster.cli.rich_renderer import RichOutputRenderer
from homemaster.config import HomeMasterConfig, load_config
from homemaster.domain.home_backend import HomeBackendReceipt, HomeWorldBackend
from homemaster.events.bus import EventBus
from homemaster.events.sinks import FanoutEventSink
from homemaster.events.third_party_logging import ThirdPartyLogCapture
from homemaster.experience import (
    AlfworldCompileJobService,
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
    trajectory_writer: AlfworldTrajectoryWriter | None = None
    alfworld_compile_jobs: AlfworldCompileJobService | None = None
    memory_enrichment_queue: MemoryEnrichmentQueue | None = None
    dreaming_coordinator: DreamingCoordinator | None = None
    session_finalization: SessionFinalizationController | None = None


@dataclass(frozen=True)
class ApplicationCompositionRequest:
    """Inputs needed to assemble one application instance.

    The optional keyword form remains accepted by ``compose_application`` while
    existing entry adapters migrate to this request object.
    """

    config: HomeMasterConfig | None = None
    profile: Literal["local_robot", "browser", "alfworld"] = "local_robot"
    runtime_root: Path | None = None
    session_root: Path | None = None
    world_path: Path | None = None
    memory_path: Path | None = None
    # Hard kill-switch for every memory tier, including the dependency-free
    # files tier.  ``memory.enabled`` cannot express this: under
    # ``mode="files"`` it is a projected ``False`` while the tier stays on.
    # The ALFWorld benchmark's ``--memory-mode disabled`` uses it to compose
    # no memory services and no memory tools at all.
    memory_off: bool = False
    memory_tenant_id: str = "local"
    event_sink: Any | None = None
    mcp_connector: Connector | None = None
    permission_mode: PermissionMode | None = None
    confirmation_handler: Any | None = None
    publish_artifacts: bool = False
    run_label: str | None = None
    progress: bool = False
    verbose: bool = False
    quiet: bool = False
    console_show_replies: bool = True
    feishu_group_operations: FeishuGroupOperations | None = None
    session_finalizer_trace_path: Path | None = None


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
        self.world = HomeWorldBackend(world_path) if world_path is not None else None

    def bind_application_run(self, run_id: str, generation: int) -> None:
        self.run_id = run_id
        self.generation = generation

    def advance(self) -> None:
        self.state_sequence += 1
        self.event_sequence += 1

    def go_to(self, target: str) -> HomeBackendReceipt:
        if self.world is None:
            raise RuntimeError("HomeWorld backend is not configured")
        receipt = self.world.go_to(target)
        self.advance()
        return receipt

    def manipulate(
        self, *, action: str, target: str, receptacle: str | None = None
    ) -> HomeBackendReceipt:
        if self.world is None:
            raise RuntimeError("HomeWorld backend is not configured")
        receipt = self.world.manipulate(action=action, target=target, receptacle=receptacle)
        self.advance()
        return receipt

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


def compose_application(
    request: ApplicationCompositionRequest | None = None,
    *,
    config: HomeMasterConfig | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    memory_off: bool = False,
    run_label: str | None = None,
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    console_show_replies: bool = True,
    mcp_connector: Connector | None = None,
    event_sink: Any | None = None,
    feishu_group_operations: FeishuGroupOperations | None = None,
    tool_environment: Literal["local_robot", "alfworld", "browser"] | None = ("local_robot"),
    runtime_root: Path | None = None,
    session_root: Path | None = None,
    memory_tenant_id: str = "local",
    session_finalizer_trace_path: Path | None = None,
    permission_mode: PermissionMode | None = None,
    confirmation_handler: Any | None = None,
    publish_artifacts: bool = False,
) -> HomeApplicationBundle:
    """Compose one Home application without opening provider connections."""

    if request is not None:
        config = request.config
        world_path = request.world_path
        memory_path = request.memory_path
        memory_off = request.memory_off
        run_label = request.run_label
        progress = request.progress
        verbose = request.verbose
        quiet = request.quiet
        console_show_replies = request.console_show_replies
        mcp_connector = request.mcp_connector
        event_sink = request.event_sink
        feishu_group_operations = request.feishu_group_operations
        tool_environment = request.profile
        runtime_root = request.runtime_root
        session_root = request.session_root
        memory_tenant_id = request.memory_tenant_id
        session_finalizer_trace_path = request.session_finalizer_trace_path
        permission_mode = request.permission_mode
        confirmation_handler = request.confirmation_handler
        publish_artifacts = request.publish_artifacts

    resolved = config or load_config()
    effective_tool_environment = resolve_tool_environment(resolved, tool_environment)
    permission_settings = resolved.permissions
    if permission_mode is not None:
        if not isinstance(permission_mode, PermissionMode):
            raise TypeError("permission_mode must be PermissionMode or None")
        payload = permission_settings.model_dump(mode="python")
        payload["mode"] = permission_mode
        permission_settings = PermissionSettingsConfig.model_validate(payload)
    if effective_tool_environment == "browser":
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
    registry = compose_tool_registry(
        environment=effective_tool_environment,
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=run_dir / "memory",
        memory_enabled=(
            (resolved.memory.enabled or resolved.memory.mode == "files")
            and not memory_off
        ),
        memory_tier=resolved.memory.mode,
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
        bundle = _finish_home_application(
            resolved=resolved,
            label=label,
            registry=registry,
            memory_off=memory_off,
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
            publish_artifacts=publish_artifacts,
        )
        if effective_tool_environment == "browser":
            from homemaster.browser.application import create_browser_application

            return replace(
                bundle,
                application=create_browser_application(
                    bundle.application,
                    resolved.browser_gateway,
                    run_dir=bundle.run_dir,
                ),
            )
        return bundle
    except BaseException:
        if extension_generation is not None and extension_disposer is not None:
            extension_disposer(extension_generation)
        raise


def _finish_home_application(
    *,
    resolved: HomeMasterConfig,
    label: str,
    registry: ToolRegistry,
    memory_off: bool,
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
    publish_artifacts: bool,
) -> HomeApplicationBundle:
    """Finish composition while the caller retains extension rollback ownership."""

    artifact_publisher: ArtifactPublisher | None = None
    skill_registry = _load_public_skill_registry(resolved)
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
    gateway_artifacts = resolved.gateway.enabled and resolved.gateway.feishu.enabled
    if publish_artifacts or gateway_artifacts:
        gateway_store = ToolOutputStore(
            run_dir / ("gateway-artifacts" if gateway_artifacts else "web-artifacts"),
            quota_bytes=256 * 1024 * 1024,
            ttl_seconds=3600,
        )
        scope.bind(
            ResourceBinding.owned(
                "gateway-tool-output-store" if gateway_artifacts else "web-tool-output-store",
                gateway_store,
                lifetime=ResourceLifetime.APPLICATION,
            )
        )
        artifact_publisher = ArtifactPublisher(gateway_store)
    trace, messages_log = compose_observability_sinks(run_dir)
    scope.bind(
        ResourceBinding.owned(
            "cli-trace",
            trace,
            lifetime=ResourceLifetime.APPLICATION,
        )
    )
    sinks: list[Any] = [trace, messages_log]
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
    trajectory_writer: AlfworldTrajectoryWriter | None = None
    alfworld_compile_jobs: AlfworldCompileJobService | None = None
    memory_enrichment_queue: MemoryEnrichmentQueue | None = None
    dreaming_coordinator: DreamingCoordinator | None = None
    memory_migration: MemoryMigrationCoordinator | None = None
    # W1 memory tiers: the files tier (mode="files") runs purely on local file
    # memory — store, frozen prompt context, and the dependency-free evidence
    # ledger — and never touches MindMemOS/Neo4j/Qdrant/spaCy.  The full tier
    # (mode="full" + enabled) layers migration, managed Neo4j, embedded
    # MindMemOS, the add/enrichment queues, and dreaming on top of it.
    # ``memory_off`` is the explicit kill-switch for both tiers (the ALFWorld
    # benchmark's --memory-mode disabled): clearing the projected
    # ``memory.enabled`` flag alone cannot disable the files tier.
    memory_files_tier = (
        resolved.memory.mode == "files" or resolved.memory.enabled
    ) and not memory_off
    memory_full_tier = (
        memory_files_tier and resolved.memory.mode == "full" and resolved.memory.enabled
    )
    if memory_files_tier:
        file_memory_store = FileMemoryStore(resolved.memory)
        frozen_memory_context = FrozenMemoryContextService(file_memory_store)
        memory_evidence_ledger = MemoryEvidenceLedger(resolved.memory.evidence_db_path)
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
        if memory_full_tier:
            memory_migration = MemoryMigrationCoordinator(resolved.memory)
            managed_neo4j = ManagedNeo4jRuntime(resolved.memory)
            mindmemos = EmbeddedMindMemOS(resolved)
            memory_add_queue = MemoryAddQueue(
                mindmemos,
                audit_path=resolved.memory.data_root / "mindmemos" / "add_jobs.jsonl",
            )
            trajectory_writer = AlfworldTrajectoryWriter(
                mindmemos, memory_add_queue, event_sink=bus, tenant_id=memory_tenant_id
            )
            alfworld_compile_jobs = AlfworldCompileJobService(
                mindmemos,
                memory_add_queue,
                jobs_root=resolved.memory.data_root / "alfworld-compilations",
                event_sink=bus,
                tenant_id=memory_tenant_id,
            )
            trajectory_writer.bind_auto_compile(alfworld_compile_jobs)
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

        async def start_memory_services(_application: ApplicationRuntime) -> None:
            assert file_memory_store is not None
            assert memory_evidence_ledger is not None
            if memory_full_tier:
                assert memory_migration is not None
                memory_migration.ensure_ready(auto_migrate=True)
            file_memory_store.start()
            memory_evidence_ledger.start()
            if memory_full_tier:
                assert managed_neo4j is not None
                assert mindmemos is not None
                assert memory_add_queue is not None
                await managed_neo4j.start()
                await mindmemos.start()
                if not mindmemos.available:
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

        starter_steps.append(start_memory_services)
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

    # Session finalization and trajectory finalization exist only on the full
    # MindMemOS tier; files mode leaves them as None (runner.py guards on the
    # trajectory writer being present before enqueueing).
    finalizer = (
        SessionFinalizer(
            trace_path=session_finalizer_trace_path or run_dir / "runtime_events.jsonl",
            data_root=resolved.memory.data_root,
            mindmemos=mindmemos,
            memory_tenant_id=memory_tenant_id,
            dreaming_coordinator=dreaming_coordinator,
            event_sink=bus,
        )
        if resolved.memory.mode == "full"
        and mindmemos is not None
        and memory_add_queue is not None
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
            "session_allows": tool_services.session_allows,
            "home_config": tool_services.config,
            **(
                {
                    "file_memory_store": file_memory_store,
                    "frozen_memory_context": frozen_memory_context,
                    "memory_evidence_ledger": memory_evidence_ledger,
                    "memory_audit_path": Path(resolved.observability.trace_dir).expanduser()
                    / "memory_operations.jsonl",
                    # The full MindMemOS tier is gated on the configured mode,
                    # not on MindMemOS being present: files mode exposes the
                    # file-memory services above without MindMemOS.
                    **(
                        {
                            "mindmemos": mindmemos,
                            "memory_add_queue": memory_add_queue,
                            "trajectory_writer": trajectory_writer,
                            "alfworld_compile_jobs": alfworld_compile_jobs,
                            "memory_enrichment_queue": memory_enrichment_queue,
                            "dreaming_coordinator": dreaming_coordinator,
                            "memory_migration": memory_migration,
                            "managed_neo4j": managed_neo4j,
                        }
                        if resolved.memory.mode == "full" and mindmemos is not None
                        else {}
                    ),
                }
                if file_memory_store is not None
                and frozen_memory_context is not None
                and memory_evidence_ledger is not None
                else {}
            ),
            **image_provider_services(resolved),
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
        trajectory_writer=trajectory_writer,
        alfworld_compile_jobs=alfworld_compile_jobs,
        memory_enrichment_queue=memory_enrichment_queue,
        dreaming_coordinator=dreaming_coordinator,
        session_finalization=session_finalization,
    )


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


load_home_skills = compose_skill_registry


def _load_public_skill_registry(config: HomeMasterConfig) -> SkillRegistry:
    """Honor the public composition loader seam used by rollback tests."""

    public_module = sys.modules.get("homemaster.application.composition")
    loader = getattr(public_module, "load_home_skills", load_home_skills)
    return loader(config)


__all__ = [
    "ApplicationCompositionRequest",
    "HomeApplicationBundle",
    "HomeCliBackend",
    "compose_application",
    "load_home_skills",
]
