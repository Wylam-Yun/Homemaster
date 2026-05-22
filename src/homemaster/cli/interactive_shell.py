"""Interactive HomeMaster shell for GenericAgentRuntime."""

from __future__ import annotations

import uuid

import typer

from homemaster.agent.session import AgentSession
from homemaster.agent.turn import run_agent_turn
from homemaster.cli.doctor import render_doctor_text, run_doctor


def run_interactive_shell() -> None:
    typer.echo("HomeMaster V1.4")
    report = run_doctor(live=False)
    if report.has_failures:
        typer.echo(render_doctor_text(report))
        typer.echo("本地体检存在 FAIL，先修复后再进入任务对话。")
        return
    typer.echo("输入自然语言任务，或输入 /new、/status、/debug、/events、/doctor、/exit。")

    session = AgentSession(session_id=uuid.uuid4().hex[:8])
    last_status = "idle"
    last_run_id: str | None = None
    last_trace_path: str | None = None

    while True:
        try:
            utterance = input("homemaster> ").strip()
        except EOFError:
            typer.echo("再见")
            return
        if not utterance:
            continue
        if utterance == "/exit":
            typer.echo("再见")
            return
        if utterance == "/new":
            session = AgentSession(session_id=uuid.uuid4().hex[:8])
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
