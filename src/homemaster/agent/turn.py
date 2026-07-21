"""AgentTurn — thin CLI-facing adapter over GenericAgentRuntime.

Provides run_single_turn() for one-shot CLI runs and run_agent_turn()
for multi-turn interactive shell sessions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass
class AgentCompactResult:
    """Result of an immediate context compaction command."""

    run_id: str
    status: str
    message: str
    trace_path: Path | None = None
    run_dir: Path | None = None
    compaction_triggered: bool = False
    compaction_kind: str = "none"
    before_tokens: int = 0
    after_tokens: int = 0
    agent_state: AgentState | None = None


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
    verbose: bool = False,
    quiet: bool = False,
    console_show_replies: bool = True,
) -> tuple[Any, Path, Path]:
    """Build the trace sink for a run."""
    from homemaster.events.sinks import (
        ConsoleEventSink,
        FanoutEventSink,
        JsonlTraceSink,
        MessagesLogSink,
        VerboseConsoleEventSink,
    )

    run_dir = Path("/tmp/homemaster/runs") / run_id
    trace_sink = JsonlTraceSink(run_dir)
    message_sink = MessagesLogSink(run_dir)
    sinks = [trace_sink, message_sink]
    if progress or verbose:
        console_sink = (
            VerboseConsoleEventSink(show_replies=console_show_replies)
            if verbose
            else ConsoleEventSink(quiet=quiet, show_replies=console_show_replies)
        )
        sinks.append(console_sink)
        return (
            FanoutEventSink(sinks),
            run_dir / "runtime_events.jsonl",
            run_dir,
        )
    return FanoutEventSink(sinks), run_dir / "runtime_events.jsonl", run_dir


def run_single_turn(
    *,
    utterance: str,
    run_id: str | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    console_show_replies: bool = True,
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
) -> AgentTurnResult:
    """Execute a single agent turn: utterance → model → tools → reply.

    Creates a fresh session internally. Suitable for CLI `run` command.
    """
    run_id = run_id or uuid.uuid4().hex[:12]
    session = AgentSession(session_id=new_session_id())
    return run_agent_turn(
        session=session,
        text=utterance,
        run_id=run_id,
        world_path=world_path,
        memory_path=memory_path,
        progress=progress,
        verbose=verbose,
        quiet=quiet,
        console_show_replies=console_show_replies,
    )


def run_agent_turn(
    session: AgentSession,
    text: str,
    *,
    run_id: str | None = None,
    world_path: Path | None = None,
    memory_path: Path | None = None,
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    console_show_replies: bool = True,
    force_compact: bool = False,
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
) -> AgentTurnResult:
    """Execute one agent turn within an existing session.

    Used by the interactive shell for multi-turn conversations.
    """
    from homemaster.prompts.loader import load_prompt

    run_id = run_id or uuid.uuid4().hex[:12]

    event_sink, trace_path, run_dir = _build_event_sink(
        run_id=run_id,
        progress=progress,
        verbose=verbose,
        quiet=quiet,
        console_show_replies=console_show_replies,
    )
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
        force_compact="manual" if force_compact else None,
    )

    return _to_turn_result(result, run_id, trace_path=trace_path, run_dir=run_dir)


def compact_agent_context(
    session: AgentSession,
    *,
    run_id: str | None = None,
    progress: bool = False,
    verbose: bool = False,
    quiet: bool = False,
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
) -> AgentCompactResult:
    """Immediately compact the current session context without running a user turn."""
    from homemaster.prompts.loader import load_prompt

    run_id = run_id or uuid.uuid4().hex[:12]
    event_sink, trace_path, run_dir = _build_event_sink(
        run_id=run_id,
        progress=progress,
        verbose=verbose,
        quiet=quiet,
        console_show_replies=False,
    )
    run_context = _build_run_context(
        run_id,
        session.session_id,
        event_sink=event_sink,
        task_state_store=task_state_store,
    )
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
        transport = _build_transport()
    context_assembler = ContextAssembler(
        provider=provider_profile,
        policy=run_context.settings.context,
        system_prompt=system_prompt,
        summary_client=transport,
    )
    if agent_state is None:
        agent_state = AgentState(
            run_id=run_id,
            session_id=session.session_id,
            max_tool_iterations=run_context.settings.runtime_guards.max_tool_iterations,
        )
    else:
        agent_state.run_id = run_id
        agent_state.session_id = session.session_id
        agent_state.max_tool_iterations = run_context.settings.runtime_guards.max_tool_iterations

    composed = context_assembler.prepare(
        session=session,
        agent_state=agent_state,
        task_state_store=run_context.deps.get("task_state_store"),
        tools=[],
        force_compact="manual",
    )
    record = agent_state.last_compaction
    if composed.metrics.compaction_triggered and record is not None:
        event_sink.emit(RuntimeEvent(
            type="context.compaction",
            session_id=session.session_id,
            run_id=run_id,
            turn_index=0,
            payload={
                "trigger": "manual",
                "kind": composed.metrics.compaction_kind,
                "before_tokens": record.before_tokens,
                "after_tokens": record.after_tokens,
            },
        ))
        return AgentCompactResult(
            run_id=run_id,
            status="compacted",
            message=(
                f"已压缩上下文：{record.before_tokens} -> {record.after_tokens} tokens"
            ),
            trace_path=trace_path,
            run_dir=run_dir,
            compaction_triggered=True,
            compaction_kind=composed.metrics.compaction_kind,
            before_tokens=record.before_tokens,
            after_tokens=record.after_tokens,
            agent_state=agent_state,
        )

    event_sink.emit(RuntimeEvent(
        type="runtime.turn_completed",
        session_id=session.session_id,
        run_id=run_id,
        turn_index=0,
        payload={"status": "noop", "reason": "no_compactable_context"},
    ))
    return AgentCompactResult(
        run_id=run_id,
        status="noop",
        message="没有可压缩的旧上下文。",
        trace_path=trace_path,
        run_dir=run_dir,
        agent_state=agent_state,
    )


def new_session_id() -> str:
    """Return a human-readable resumable session id."""

    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


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
