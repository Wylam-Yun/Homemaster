"""Interactive HomeMaster shell for GenericAgentRuntime."""

from __future__ import annotations

from pathlib import Path

import typer

from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import (
    load_session_json,
    resume_session,
    save_snapshot,
    session_snapshot_path,
)
from homemaster.agent.turn import compact_agent_context, new_session_id, run_agent_turn
from homemaster.cli.doctor import render_doctor_text, run_doctor
from homemaster.config import load_config
from homemaster.task_state.store import TaskStateStore


def run_interactive_shell(*, resume_session_id: str | None = None) -> None:
    _enable_line_editing()
    typer.echo("HomeMaster V1.6")
    report = run_doctor(live=False)
    if report.has_failures:
        typer.echo(render_doctor_text(report))
        typer.echo("本地体检存在 FAIL，先修复后再进入任务对话。")
        return
    typer.echo("输入任务开始对话。命令：/help、/new、/compact、/status、/events、/doctor、/exit。")

    if resume_session_id is not None:
        session_root = Path(load_config().observability.session_dir)
        snapshot_path = session_snapshot_path(session_root, resume_session_id)
        snapshot_payload = load_session_json(snapshot_path)
        session, agent_state, task_state_store = resume_session(snapshot_path)
        snapshot_model = str(snapshot_payload.get("model") or "")
        snapshot_system_prompt = str(snapshot_payload.get("system_prompt") or "")
        typer.echo(f"已恢复会话: {session.session_id}")
    else:
        session = AgentSession(session_id=new_session_id())
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
        if utterance == "/help":
            typer.echo(_render_help())
            continue
        if utterance == "/new":
            session = AgentSession(session_id=new_session_id())
            agent_state = None
            task_state_store = TaskStateStore(run_id=session.session_id)
            last_status = "idle"
            last_run_id = None
            last_trace_path = None
            typer.echo("新会话已创建。")
            continue
        if utterance == "/compact":
            run_id = new_session_id()
            try:
                compact_result = compact_agent_context(
                    session,
                    run_id=run_id,
                    progress=False,
                    agent_state=agent_state,
                    task_state_store=task_state_store,
                )
            except Exception as exc:
                last_status = "failed"
                typer.echo(f"上下文压缩失败：{exc}")
                continue
            agent_state = compact_result.agent_state
            last_status = compact_result.status
            last_run_id = compact_result.run_id
            last_trace_path = (
                str(compact_result.trace_path) if compact_result.trace_path else None
            )
            typer.echo(f"上下文压缩：{compact_result.message}")
            continue
        if utterance == "/doctor":
            typer.echo(render_doctor_text(run_doctor(live=False)))
            continue
        if utterance == "/status":
            typer.echo(f"状态：{last_status}")
            continue
        if utterance == "/debug":
            typer.echo(f"调试：run_id={last_run_id or '还没有运行任务'}")
            continue
        if utterance == "/events":
            if last_trace_path:
                typer.echo(f"上一轮 trace 文件：{last_trace_path}")
            else:
                typer.echo("还没有 trace。")
            continue

        run_id = new_session_id()
        try:
            result = run_agent_turn(
                session,
                utterance,
                run_id=run_id,
                progress=True,
                console_show_replies=False,
                agent_state=agent_state,
                task_state_store=task_state_store,
            )
        except Exception as exc:
            last_status = "failed"
            typer.echo(f"失败：{exc}")
            continue
        last_status = result.status
        last_run_id = result.run_id
        last_trace_path = str(result.trace_path) if result.trace_path else None
        typer.echo(f"模型回复：{result.final_reply}")


def _pause_active_task(task_state_store: TaskStateStore | None) -> None:
    if task_state_store is None:
        return
    from homemaster.task_state.models import TaskStatus

    snapshot = task_state_store.snapshot
    if snapshot is not None and snapshot.status == TaskStatus.ACTIVE:
        task_state_store.update_status(TaskStatus.PAUSED)


def _enable_line_editing() -> None:
    """Enable readline-backed editing for terminals that support it."""

    try:
        import readline  # noqa: F401
    except ImportError:
        return


def _render_help() -> str:
    return "\n".join([
        "可用命令：",
        "/new：新建会话，清空当前对话状态。",
        "/compact：立即压缩已有上下文，会按需调用 summary API。",
        "/status：查看上一轮状态。",
        "/events：查看上一轮 trace 文件路径，供调试和可观测性检查。",
        "/doctor：检查本地配置和依赖。",
        "/exit：退出 shell。",
    ])
