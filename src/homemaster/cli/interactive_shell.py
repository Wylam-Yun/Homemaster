"""Interactive HomeMaster shell for GenericAgentRuntime."""

from __future__ import annotations

import uuid
from pathlib import Path

import typer

from homemaster.agent.session_persistence import (
    load_session_json,
    resume_session,
    save_snapshot,
    session_snapshot_path,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.turn import run_agent_turn
from homemaster.cli.doctor import render_doctor_text, run_doctor
from homemaster.config import load_config
from homemaster.task_state.store import TaskStateStore


def run_interactive_shell(*, resume_session_id: str | None = None) -> None:
    typer.echo("HomeMaster V1.6")
    report = run_doctor(live=False)
    if report.has_failures:
        typer.echo(render_doctor_text(report))
        typer.echo("本地体检存在 FAIL，先修复后再进入任务对话。")
        return
    typer.echo("输入自然语言任务，或输入 /new、/status、/debug、/events、/doctor、/exit。")

    if resume_session_id is not None:
        session_root = Path(load_config().observability.session_dir)
        snapshot_path = session_snapshot_path(session_root, resume_session_id)
        snapshot_payload = load_session_json(snapshot_path)
        session, agent_state, task_state_store = resume_session(snapshot_path)
        snapshot_model = str(snapshot_payload.get("model") or "")
        snapshot_system_prompt = str(snapshot_payload.get("system_prompt") or "")
        typer.echo(f"已恢复会话: {session.session_id}")
    else:
        session = AgentSession(session_id=uuid.uuid4().hex[:8])
        agent_state = None
        task_state_store = TaskStateStore(run_id=session.session_id)
        snapshot_path = None
        snapshot_model = ""
        snapshot_system_prompt = ""
    last_status = "idle"
    last_run_id: str | None = None
    last_trace_path: str | None = None

    while True:
        try:
            utterance = input("homemaster> ").strip()
        except EOFError:
            typer.echo("再见")
            return
        except KeyboardInterrupt:
            _pause_active_task(task_state_store)
            if snapshot_path is not None and agent_state is not None:
                save_snapshot(
                    session=session,
                    agent_state=agent_state,
                    task_state_store=task_state_store,
                    path=snapshot_path,
                    model=snapshot_model,
                    system_prompt=snapshot_system_prompt,
                )
            typer.echo("\n再见")
            return
        if not utterance:
            continue
        if utterance == "/exit":
            typer.echo("再见")
            return
        if utterance == "/new":
            session = AgentSession(session_id=uuid.uuid4().hex[:8])
            agent_state = None
            task_state_store = TaskStateStore(run_id=session.session_id)
            last_status = "idle"
            last_run_id = None
            last_trace_path = None
            typer.echo("新会话已创建。")
            continue
        if utterance == "/doctor":
            typer.echo(render_doctor_text(run_doctor(live=False)))
            continue
        if utterance == "/status":
            typer.echo(f"status: {last_status}")
            continue
        if utterance == "/debug":
            typer.echo(f"run_id: {last_run_id or 'no task has run yet'}")
            continue
        if utterance == "/events":
            typer.echo(f"trace: {last_trace_path or 'no trace yet'}")
            continue

        run_id = uuid.uuid4().hex[:12]
        try:
            result = run_agent_turn(
                session,
                utterance,
                run_id=run_id,
                progress=True,
                agent_state=agent_state,
                task_state_store=task_state_store,
            )
        except Exception as exc:
            last_status = "failed"
            typer.echo(f"failed: {exc}")
            continue
        last_status = result.status
        last_run_id = result.run_id
        last_trace_path = str(result.trace_path) if result.trace_path else None
        typer.echo(f"assistant: {result.final_reply}")
        typer.echo(f"status: {result.status}")
        if result.trace_path:
            typer.echo(f"trace: {result.trace_path}")


def _pause_active_task(task_state_store: TaskStateStore | None) -> None:
    if task_state_store is None:
        return
    from homemaster.task_state.models import TaskStatus

    snapshot = task_state_store.snapshot
    if snapshot is not None and snapshot.status == TaskStatus.ACTIVE:
        task_state_store.update_status(TaskStatus.PAUSED)
