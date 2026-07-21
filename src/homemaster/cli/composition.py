"""Outer Home CLI composition for the V1.9 application runtime."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.adapters.profiles import build_home_profile
from homemaster.application import ApplicationRuntime, ResourceBinding, ResourceLifetime
from homemaster.application.factory import create_application
from homemaster.application.resources import RunResourceScope
from homemaster.config import HomeMasterConfig, load_config
from homemaster.events.bus import EventBus
from homemaster.events.sinks import (
    ConsoleEventSink,
    FanoutEventSink,
    JsonlTraceSink,
    MessagesLogSink,
)
from homemaster.observations import ObservationCapture, ObservationService


@dataclass(frozen=True)
class HomeApplicationBundle:
    application: ApplicationRuntime
    config: HomeMasterConfig
    run_dir: Path
    trace_path: Path


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
    application = create_application(
        config=resolved,
        profiles={"home": profile},
        catalog=profile.catalog,
        observation_service=observation,
        event_bus=bus,
        resource_scope=scope,
    )
    return HomeApplicationBundle(
        application=application,
        config=resolved,
        run_dir=run_dir,
        trace_path=run_dir / "runtime_events.jsonl",
    )


__all__ = ["HomeApplicationBundle", "HomeCliBackend", "create_home_application"]
