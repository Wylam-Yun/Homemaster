"""AgentTurn — thin CLI-facing adapter over GenericAgentRuntime.

Provides run_single_turn() for one-shot CLI runs and run_agent_turn()
for multi-turn interactive shell sessions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from homemaster.agent.generic_runtime import GenericAgentRuntime, GenericRunResult
from homemaster.agent.normalized import RunContext
from homemaster.agent.session import AgentSession
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.providers.mimo_transport import MimoTransport
from homemaster.providers.transport import LLMTransport
from homemaster.tools.dispatcher import ToolDispatcher


@dataclass
class AgentTurnResult:
    """Result of an agent turn (single or multi-turn)."""

    run_id: str
    status: str
    final_reply: str
    trace_path: Path | None = None
    run_dir: Path | None = None
    tool_events: list[RuntimeEvent] = field(default_factory=list)


def _build_transport() -> LLMTransport:
    """Build a MimoTransport from default config."""
    from homemaster.runtime import DEFAULT_CONFIG_PATH, DEFAULT_PROVIDER_NAME, load_provider_config

    provider = load_provider_config(DEFAULT_CONFIG_PATH, provider_name=DEFAULT_PROVIDER_NAME)
    api_key = provider.api_keys[0]
    return MimoTransport(
        base_url=provider.base_url,
        model=provider.model,
        api_key=api_key,
        protocol=provider.protocol,
    )


def _build_tool_dispatcher_and_specs(
    run_id: str,
    *,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    runtime_memory_root: Path | None = None,
) -> tuple[ToolDispatcher, list[dict[str, object]]]:
    """Build a ToolDispatcher with all Home domain tools registered.

    Returns (dispatcher, tool_schemas) where tool_schemas are the
    generic_runtime.ToolSpec-compatible dicts for the model.
    """
    from homemaster.domain.home.tool_registry import build_home_tool_registry

    registry = build_home_tool_registry(
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=runtime_memory_root,
    )
    dispatcher = ToolDispatcher()
    tool_schemas: list[dict[str, object]] = []

    for name in registry.all_names():
        spec = registry.get(name)
        if spec is not None:
            dispatcher.register(spec)
            if spec.selectable_by_model:
                tool_schemas.append({
                    "name": spec.name,
                    "description": spec.description,
                    "input_schema": spec.input_schema,
                })

    return dispatcher, tool_schemas


def _build_run_context(
    run_id: str,
    session_id: str,
    *,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    event_sink: Any = None,
    turn_index: int = 0,
) -> RunContext:
    """Build a RunContext with explicit RuntimeSettings."""
    settings = RuntimeSettings(
        run_id=run_id,
        runtime_root=Path("/tmp/homemaster/runs"),
        debug_root=Path("/tmp/homemaster/debug"),
        results_root=Path("/tmp/homemaster/results"),
        world_path=world_path,
        memory_path=memory_path,
    )
    return RunContext(
        session_id=session_id,
        run_id=run_id,
        turn_index=turn_index,
        settings=settings,
        event_sink=event_sink,
    )


def _build_event_sink(
    *,
    run_id: str,
    progress: bool,
) -> tuple[Any, Path, Path]:
    """Build the trace sink for a run."""
    from homemaster.events.sinks import (
        ConsoleProgressEventSink,
        FanoutEventSink,
        JsonlEventSink,
    )

    run_dir = Path("/tmp/homemaster/runs") / run_id
    jsonl_sink = JsonlEventSink(run_dir)
    if progress:
        return (
            FanoutEventSink([jsonl_sink, ConsoleProgressEventSink()]),
            run_dir / "runtime_events.jsonl",
            run_dir,
        )
    return jsonl_sink, run_dir / "runtime_events.jsonl", run_dir


def run_single_turn(
    *,
    utterance: str,
    run_id: str | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    progress: bool = False,
) -> AgentTurnResult:
    """Execute a single agent turn: utterance → model → tools → reply.

    Creates a fresh session internally. Suitable for CLI `run` command.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    session = AgentSession(session_id=run_id)
    return run_agent_turn(
        session=session,
        text=utterance,
        run_id=run_id,
        world_path=world_path,
        memory_path=memory_path,
        progress=progress,
    )


def run_agent_turn(
    session: AgentSession,
    text: str,
    *,
    run_id: str | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    progress: bool = False,
) -> AgentTurnResult:
    """Execute one agent turn within an existing session.

    Used by the interactive shell for multi-turn conversations.
    """
    run_id = run_id or uuid.uuid4().hex[:12]

    transport = _build_transport()
    event_sink, trace_path, run_dir = _build_event_sink(run_id=run_id, progress=progress)
    dispatcher, tool_schemas = _build_tool_dispatcher_and_specs(
        run_id,
        world_path=world_path,
        memory_path=memory_path,
        runtime_memory_root=run_dir / "memory",
    )

    # Build RunContext and set it on the dispatcher for tool execution
    run_context = _build_run_context(
        run_id,
        session.session_id,
        world_path=world_path,
        memory_path=memory_path,
        event_sink=event_sink,
    )
    dispatcher.set_run_context(run_context)

    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=12,
    )

    result = runtime.run(
        session,
        text,
        tools=_to_tool_specs(tool_schemas),
        event_sink=event_sink,
        run_id=run_id,
        settings=run_context.settings,
    )

    return _to_turn_result(result, run_id, trace_path=trace_path, run_dir=run_dir)


def _to_tool_specs(
    schemas: list[dict[str, object]],
) -> list:
    """Convert tool schema dicts to generic_runtime.ToolSpec objects."""
    from homemaster.agent.generic_runtime import ToolSpec

    return [
        ToolSpec(
            name=s["name"],  # type: ignore[index]
            description=s.get("description", ""),  # type: ignore[union-attr]
            input_schema=s.get("input_schema", {}),  # type: ignore[union-attr]
        )
        for s in schemas
    ]


def _to_turn_result(
    result: GenericRunResult,
    run_id: str,
    *,
    trace_path: Path | None = None,
    run_dir: Path | None = None,
) -> AgentTurnResult:
    """Convert a GenericRunResult to an AgentTurnResult."""
    return AgentTurnResult(
        run_id=result.run_id,
        status=result.status,
        final_reply=result.final_reply,
        trace_path=trace_path,
        run_dir=run_dir,
        tool_events=result.events,
    )
