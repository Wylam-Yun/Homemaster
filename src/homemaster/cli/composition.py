"""Outer Home CLI composition for the V1.9 application runtime."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.adapters.profiles import EnvironmentToolProfile, build_home_profile
from homemaster.application import ApplicationRuntime, ResourceBinding, ResourceLifetime
from homemaster.application.factory import create_application
from homemaster.application.resources import RunResourceScope
from homemaster.artifacts import ToolOutputStore
from homemaster.config import HomeMasterConfig, load_config
from homemaster.events.bus import EventBus
from homemaster.events.sinks import (
    ConsoleEventSink,
    FanoutEventSink,
    JsonlTraceSink,
    MessagesLogSink,
)
from homemaster.extensions import (
    ExtensionApproval,
    ExtensionReloader,
    HookRunner,
    dispose_extension_generation,
    load_extension_generation,
    register_extension_tools_atomically,
)
from homemaster.mcp.adapter import build_mcp_registered_tools, register_mcp_tools_atomically
from homemaster.mcp.audit import McpAuditLog
from homemaster.mcp.client import Connector, McpClientManager
from homemaster.observations import ObservationCapture, ObservationService
from homemaster.skills.loader import load_skill_registry
from homemaster.skills.registry import SkillRegistry


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


class HomeCliBackend:
    """Borrowed structured state backend for Home's explicit observe tool."""

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

    def capture(self) -> ObservationCapture:
        self.event_sequence += 1
        return ObservationCapture(
            backend_id=self.backend_id,
            run_id=self.run_id,
            generation=self.generation,
            state_sequence=self.state_sequence,
            capture_event_sequence=self.event_sequence,
            media_type="application/json",
            content={
                "environment": "home",
                "state_sequence": self.state_sequence,
                "world_path": str(self.world_path) if self.world_path else None,
                "memory_path": str(self.memory_path) if self.memory_path else None,
            },
            evidence_ref=f"home/{self.run_id}/observation/{self.event_sequence}",
        )


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
) -> HomeApplicationBundle:
    """Compose one Home application without opening provider connections."""

    resolved = config or load_config()
    label = run_label or f"cli-{uuid.uuid4().hex[:12]}"
    run_dir = Path(resolved.runtime.runtime_root).expanduser() / label
    observation = ObservationService()
    profile = build_home_profile(
        observation_service=observation,
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=run_dir / "memory",
    )
    extension_runner: HookRunner | None = None
    extension_reloader: ExtensionReloader | None = None
    extension_generation = None
    if resolved.extensions.approvals:
        approvals = _extension_approvals(resolved)
        extension_generation = load_extension_generation(approvals, generation=1)
        try:
            enabled_extension_ids = register_extension_tools_atomically(
                profile.catalog,
                extension_generation,
            )
            profile = EnvironmentToolProfile(
                environment="home",
                catalog=profile.catalog,
                view=profile.catalog.freeze((*profile.enabled_tool_ids, *enabled_extension_ids)),
            )
        except BaseException:
            dispose_extension_generation(extension_generation)
            raise
        extension_runner = HookRunner(extension_generation)
        extension_reloader = ExtensionReloader(extension_runner)
    try:
        return _finish_home_application(
            resolved=resolved,
            label=label,
            profile=profile,
            observation=observation,
            extension_runner=extension_runner,
            extension_reloader=extension_reloader,
            progress=progress,
            verbose=verbose,
            quiet=quiet,
            console_show_replies=console_show_replies,
            mcp_connector=mcp_connector,
        )
    except BaseException:
        if extension_generation is not None:
            dispose_extension_generation(extension_generation)
        raise


def _finish_home_application(
    *,
    resolved: HomeMasterConfig,
    label: str,
    profile: EnvironmentToolProfile,
    observation: ObservationService,
    extension_runner: HookRunner | None,
    extension_reloader: ExtensionReloader | None,
    progress: bool,
    verbose: bool,
    quiet: bool,
    console_show_replies: bool,
    mcp_connector: Connector | None,
) -> HomeApplicationBundle:
    """Finish composition while the caller retains extension rollback ownership."""

    run_dir = Path(resolved.runtime.runtime_root).expanduser() / label
    skill_registry = (
        SkillRegistry() if resolved.mcp.servers else load_home_skills(resolved, profile)
    )
    bus = EventBus()
    scope = RunResourceScope()
    trace = JsonlTraceSink(run_dir)
    scope.bind(
        ResourceBinding.owned(
            "cli-trace",
            trace,
            lifetime=ResourceLifetime.APPLICATION,
        )
    )
    sinks: list[Any] = [trace, MessagesLogSink(run_dir)]
    if progress or verbose:
        sinks.append(
            ConsoleEventSink(
                verbose=verbose,
                quiet=quiet,
                show_replies=console_show_replies,
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
    application_starter = None
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
            mcp_ids = register_mcp_tools_atomically(application.catalog, registered)
            current = application.profiles["home"]
            final_profile = EnvironmentToolProfile(
                environment="home",
                catalog=application.catalog,
                view=application.catalog.freeze((*current.enabled_tool_ids, *mcp_ids)),
            )
            application.profiles["home"] = final_profile
            skill_registry.replace_with(load_home_skills(resolved, final_profile))

        application_starter = start_mcp
    application = create_application(
        config=resolved,
        profiles={"home": profile},
        catalog=profile.catalog,
        observation_service=observation,
        event_bus=bus,
        resource_scope=scope,
        application_starter=application_starter,
        extension_runner=extension_runner,
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


def load_home_skills(
    config: HomeMasterConfig,
    profile: Any,
    *,
    cwd: Path | None = None,
) -> SkillRegistry:
    """Load only skills whose tool names resolve in the frozen Home ToolView."""

    sources = config.skills
    return load_skill_registry(
        cwd=cwd or Path.cwd(),
        user_dirs=sources.user_dirs,
        project_dirs=sources.project_dirs,
        explicit_dirs=sources.explicit_dirs,
        allowed_tool_names=profile.model_tool_names,
        allow_project=sources.allow_project,
        allowed_builtin_overrides=sources.allowed_builtin_overrides,
    )


__all__ = [
    "HomeApplicationBundle",
    "HomeCliBackend",
    "create_home_application",
    "load_home_skills",
]
