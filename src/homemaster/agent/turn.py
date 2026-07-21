"""Thin compatibility wrappers over :class:`ApplicationRuntime`."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.application import ApplicationRuntime, RunRequest, RunResult
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.task_state.store import TaskStateStore


@dataclass
class AgentTurnResult:
    run_id: str
    status: str
    final_reply: str
    trace_path: Path | None = None
    run_dir: Path | None = None
    tool_events: list[RuntimeEvent] = field(default_factory=list)


@dataclass
class AgentCompactResult:
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


def run_single_turn(
    *,
    application: ApplicationRuntime,
    utterance: str,
    session_id: str | None = None,
    provider_name: str | None = None,
    environment: object | None = None,
    trace_path: Path | None = None,
    run_dir: Path | None = None,
    **compatibility: Any,
) -> AgentTurnResult:
    """Submit one typed request; outer callers own application composition."""

    del compatibility
    result = asyncio.run(
        application.run(
            RunRequest(
                text=utterance,
                session_id=session_id,
                profile="home",
                provider_name=provider_name,
                environment=environment,
            )
        )
    )
    return _to_turn_result(result, trace_path=trace_path, run_dir=run_dir)


def run_agent_turn(
    session: AgentSession,
    text: str,
    *,
    application: ApplicationRuntime,
    provider_name: str | None = None,
    environment: object | None = None,
    force_compact: bool = False,
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
    trace_path: Path | None = None,
    run_dir: Path | None = None,
    **compatibility: Any,
) -> AgentTurnResult:
    """Submit a turn for an existing compatibility ``AgentSession``."""

    del compatibility

    async def execute() -> RunResult:
        await _attach_compatibility_session(
            application,
            session,
            agent_state=agent_state,
            task_state_store=task_state_store,
        )
        if force_compact:
            await application.compact(session.session_id)
        return await application.run(
            RunRequest(
                text=text,
                session_id=session.session_id,
                profile="home",
                provider_name=provider_name,
                resume=True,
                environment=environment,
            )
        )

    return _to_turn_result(
        asyncio.run(execute()),
        trace_path=trace_path,
        run_dir=run_dir,
    )


def compact_agent_context(
    session: AgentSession,
    *,
    application: ApplicationRuntime,
    agent_state: AgentState | None = None,
    task_state_store: TaskStateStore | None = None,
    trace_path: Path | None = None,
    run_dir: Path | None = None,
    **compatibility: Any,
) -> AgentCompactResult:
    """Forward manual compaction to the owning application session."""

    del compatibility

    async def execute():
        runtime = await _attach_compatibility_session(
            application,
            session,
            agent_state=agent_state,
            task_state_store=task_state_store,
        )
        result = await application.compact(session.session_id)
        return runtime, result

    runtime, result = asyncio.run(execute())
    record = runtime.agent_state.last_compaction
    return AgentCompactResult(
        run_id=runtime.agent_state.run_id or f"compact-{result.generation}",
        status="compacted" if result.triggered else "noop",
        message=(
            f"context compacted: {record.before_tokens} -> {record.after_tokens} tokens"
            if result.triggered and record is not None
            else "no compactable context"
        ),
        trace_path=trace_path,
        run_dir=run_dir,
        compaction_triggered=result.triggered,
        compaction_kind=result.kind,
        before_tokens=record.before_tokens if record is not None else 0,
        after_tokens=record.after_tokens if record is not None else 0,
        agent_state=runtime.agent_state,
    )


async def _attach_compatibility_session(
    application: ApplicationRuntime,
    session: AgentSession,
    *,
    agent_state: AgentState | None,
    task_state_store: TaskStateStore | None,
):
    try:
        return application.session_manager.get(session.session_id)
    except KeyError:
        runtime = await application.session_manager.open_or_resume(session.session_id)
        runtime.session = session
        if agent_state is not None:
            runtime.agent_state = agent_state
        if task_state_store is not None:
            runtime.task_state_store = task_state_store
        return runtime


def _to_turn_result(
    result: RunResult,
    *,
    trace_path: Path | None,
    run_dir: Path | None,
) -> AgentTurnResult:
    return AgentTurnResult(
        run_id=result.run_id,
        status=str(result.status),
        final_reply=result.final_reply,
        trace_path=trace_path,
        run_dir=run_dir,
        tool_events=list(result.events),
    )


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]


__all__ = [
    "AgentCompactResult",
    "AgentTurnResult",
    "compact_agent_context",
    "new_session_id",
    "run_agent_turn",
    "run_single_turn",
]
