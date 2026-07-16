"""Session management CLI commands."""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import typer

from homemaster.agent.session_persistence import (
    list_sessions,
    load_session_json,
    session_snapshot_path,
)
from homemaster.config import load_config

session_app = typer.Typer(add_completion=False, help="Manage persisted sessions.")


@session_app.command("list")
def list_command() -> None:
    """List persisted sessions."""

    session_root = _session_root()
    sessions = list_sessions(session_root)
    if not sessions:
        typer.echo(f"no sessions found under {session_root.expanduser()}")
        return
    typer.echo("SESSION ID                          CREATED              ITER  TOKENS   STATUS")
    for info in sessions:
        typer.echo(
            f"{info.session_id:<35} "
            f"{_fmt_time(info.created_at):<20} "
            f"{info.iteration_index:<5} "
            f"{_fmt_tokens(info.total_tokens):<8} "
            f"{info.status}"
        )


@session_app.command("show")
def show_command(session_id: str) -> None:
    """Show session metadata."""

    payload = _load_payload(session_id)
    agent_state = payload.get("agent_state") or {}
    task_state = payload.get("task_state") or {}
    task_snapshot = task_state.get("snapshot") if isinstance(task_state, dict) else None
    summary = {
        "session_id": payload.get("session_id"),
        "created_at": payload.get("created_at"),
        "saved_at": payload.get("saved_at"),
        "model": payload.get("model"),
        "status": agent_state.get("status") if isinstance(agent_state, dict) else None,
        "iteration_index": (
            agent_state.get("iteration_index") if isinstance(agent_state, dict) else None
        ),
        "message_count": len(payload.get("messages") or []),
        "task_status": (task_snapshot.get("status") if isinstance(task_snapshot, dict) else None),
    }
    typer.echo(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


@session_app.command("delete")
def delete_command(session_id: str) -> None:
    """Delete a persisted session directory."""

    path = session_snapshot_path(_session_root(), session_id)
    if not path.exists():
        raise typer.BadParameter(f"session snapshot not found: {path}")
    shutil.rmtree(path.parent)
    typer.echo(f"deleted: {path.parent}")


@session_app.command("clean")
def clean_command(
    older_than: str = typer.Option(..., "--older-than", help="Age such as 30d or 12h."),
) -> None:
    """Delete sessions older than the requested age."""

    cutoff = time.time() - _parse_age_seconds(older_than)
    deleted = 0
    for info in list_sessions(_session_root()):
        if info.saved_at and info.saved_at < cutoff:
            shutil.rmtree(info.path.parent)
            deleted += 1
    typer.echo(f"deleted: {deleted}")


@session_app.command("export")
def export_command(
    session_id: str,
    export_format: str = typer.Option("markdown", "--format", help="markdown or json."),
) -> None:
    """Export a session transcript."""

    payload = _load_payload(session_id)
    if export_format == "json":
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    if export_format != "markdown":
        raise typer.BadParameter("--format must be markdown or json")
    lines = [f"# HomeMaster Session {payload.get('session_id')}"]
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "unknown")
        text = "\n".join(
            str(block.get("text"))
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("text")
        )
        if text:
            lines.append(f"\n## {role}\n{text}")
    typer.echo("\n".join(lines))


def _session_root() -> Path:
    return Path(load_config().observability.session_dir).expanduser()


def _load_payload(session_id: str) -> dict:
    path = session_snapshot_path(_session_root(), session_id)
    if not path.exists():
        raise typer.BadParameter(f"session snapshot not found: {path}")
    return load_session_json(path)


def _fmt_time(timestamp: float) -> str:
    if not timestamp:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))


def _fmt_tokens(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens // 1000}K"
    return str(tokens)


def _parse_age_seconds(value: str) -> float:
    if len(value) < 2:
        raise typer.BadParameter("age must look like 30d, 12h, or 60m")
    unit = value[-1].lower()
    try:
        amount = float(value[:-1])
    except ValueError as exc:
        raise typer.BadParameter("age must start with a number") from exc
    multipliers = {"d": 86400, "h": 3600, "m": 60}
    if unit not in multipliers:
        raise typer.BadParameter("age unit must be d, h, or m")
    return amount * multipliers[unit]
