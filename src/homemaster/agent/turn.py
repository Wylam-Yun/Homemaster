"""AgentTurn — thin CLI-facing adapter over GenericAgentRuntime.

Provides run_single_turn() for one-shot CLI runs and run_agent_turn()
for multi-turn interactive shell sessions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homemaster.agent.context import ContextAssembler
from homemaster.agent.generic_runtime import GenericAgentRuntime, GenericRunResult
from homemaster.agent.normalized import RunContext
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.config import HOMEMASTER_CONFIG_PATH, load_config
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.providers.llm_client import LLMClient
from homemaster.task_state.store import TaskStateStore
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


def _build_transport(
    *,
    config_path: Path | None = None,
    provider_name: str | None = None,
) -> LLMClient:
    """Build an SDK-backed LLMClient from unified config."""

    config = load_config(config_path)
    provider = config.get_provider(provider_name, kind="chat")
    return LLMClient(provider, timeout_s=config.provider_client.timeout_s)


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
    from homemaster.domain.tool_registry import build_home_tool_registry

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
    task_state_store: TaskStateStore | None = None,
) -> RunContext:
    """Build a RunContext with explicit run-scoped settings."""

    config_path = HOMEMASTER_CONFIG_PATH if HOMEMASTER_CONFIG_PATH.exists() else None
    config = load_config(config_path)
    runtime = config.runtime
    settings = SimpleNamespace(
        run_id=run_id,
        max_turns=12,
        runtime_root=runtime.runtime_root,
        debug_root=runtime.debug_root,
        results_root=runtime.results_root,
        provider_name=config.runtime_defaults.default_provider_name,
        embedding_provider_name=config.runtime_defaults.default_embedding_provider_name,
        config_path=config.config_path or HOMEMASTER_CONFIG_PATH,
        memory_path=memory_path,
        context=config.context,
        runtime_guards=config.runtime,
        prompts=config.prompts,
        observability=config.observability,
    )
    return RunContext(
        session_id=session_id,
        run_id=run_id,
        turn_index=turn_index,
        settings=settings,
        event_sink=event_sink,
        deps={"task_state_store": task_state_store or TaskStateStore(run_id=run_id)},
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
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
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
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
) -> AgentTurnResult:
    """Execute one agent turn within an existing session.

    Used by the interactive shell for multi-turn conversations.
    """
    from homemaster.prompts.loader import load_prompt

    run_id = run_id or uuid.uuid4().hex[:12]

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
        task_state_store=task_state_store,
    )
    dispatcher.set_run_context(run_context)

    # Load system prompt and build context assembler
    system_prompt = load_prompt(run_context.settings.prompts.agent_system_prompt)
    config = load_config(run_context.settings.config_path)
    provider_profile = config.get_provider(
        run_context.settings.provider_name,
        kind="chat",
    )
    try:
        transport = _build_transport(
            config_path=run_context.settings.config_path,
            provider_name=run_context.settings.provider_name,
        )
    except TypeError:
        # Some tests monkeypatch the factory with a no-arg fake transport.
        transport = _build_transport()
    context_assembler = ContextAssembler(
        provider=provider_profile,
        policy=run_context.settings.context,
        system_prompt=system_prompt,
        summary_client=transport,
    )

    runtime = GenericAgentRuntime(
        transport=transport,
        tool_executor=dispatcher,
        max_tool_iterations=run_context.settings.runtime_guards.max_tool_iterations,
        context_assembler=context_assembler,
        system_prompt=system_prompt,
    )

    result = runtime.run(
        session,
        text,
        tools=_to_tool_specs(tool_schemas),
        event_sink=event_sink,
        run_id=run_id,
        settings=run_context.settings,
        agent_state=agent_state,
        task_state_store=run_context.deps.get("task_state_store"),
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
