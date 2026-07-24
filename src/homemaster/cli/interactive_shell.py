"""Interactive Home shell over one long-lived V1.9 ApplicationRuntime."""

from __future__ import annotations

import asyncio

import typer

from homemaster.agent.turn import new_session_id
from homemaster.application import RunPolicy, RunRequest, RunStatus
from homemaster.benchmarking.coworker_demo.turn import run_coworker_turn
from homemaster.benchmarking.coworker_demo.types import CoworkerAttemptError, TicketRouteKind
from homemaster.cli.composition import HomeCliBackend, create_home_application
from homemaster.cli.coworker_router import route_coworker_ticket
from homemaster.cli.doctor import render_doctor_text, run_doctor
from homemaster.skills.commands import resolve_skill_command


def run_interactive_shell(
    *,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
) -> None:
    _enable_line_editing()
    typer.echo("HomeMaster V1.9")
    report = run_doctor(live=False)
    if report.has_failures:
        typer.echo(render_doctor_text(report))
        typer.echo("Local checks failed; fix them before starting a task session.")
        return
    if resume_session_id is not None and continue_latest:
        raise ValueError("--continue cannot be combined with --resume")

    bundle = create_home_application(run_label=f"shell-{new_session_id()}", progress=True)
    application = bundle.application
    backend = HomeCliBackend(world_path=None, memory_path=None)
    session_id = resume_session_id or new_session_id()
    session_open = False
    last_status = "idle"
    last_run_id: str | None = None

    with asyncio.Runner() as runner:
        async def ask_user(question: str) -> str:
            return await asyncio.to_thread(input, f"{question}\nanswer> ")

        try:
            if continue_latest:
                session_ids = application.session_manager.list_session_ids()
                if not session_ids:
                    raise FileNotFoundError("no persisted session is available to continue")
                session_id = session_ids[0]
            if resume_session_id is not None or continue_latest:
                runner.run(application.session_manager.resume(session_id))
                session_open = True
                typer.echo(f"Resumed session: {session_id}")
            typer.echo(
                "Enter a task. Commands: /help, /new, /compact, /status, /events, /doctor, /exit."
            )

            while True:
                try:
                    utterance = input("homemaster> ").strip()
                except EOFError:
                    typer.echo("Goodbye")
                    return
                except KeyboardInterrupt:
                    typer.echo("\nGoodbye")
                    return
                if not utterance:
                    continue
                if utterance == "/exit":
                    typer.echo("Goodbye")
                    return
                if utterance == "/help":
                    typer.echo(_render_help())
                    continue
                if utterance == "/new":
                    session_id = new_session_id()
                    backend = HomeCliBackend(world_path=None, memory_path=None)
                    session_open = False
                    last_status = "idle"
                    last_run_id = None
                    typer.echo("New session created.")
                    continue
                if utterance == "/compact":
                    if not session_open:
                        typer.echo("Context compaction: no active session.")
                        continue
                    try:
                        compact = runner.run(application.compact(session_id))
                    except Exception as exc:
                        last_status = "failed"
                        typer.echo(f"Context compaction failed: {exc}")
                        continue
                    last_status = "compacted" if compact.triggered else "noop"
                    typer.echo(
                        "Context compaction: "
                        f"status={last_status}, kind={compact.kind}, revision={compact.revision}"
                    )
                    continue
                if utterance == "/doctor":
                    typer.echo(render_doctor_text(run_doctor(live=False)))
                    continue
                if utterance == "/status":
                    if not session_open:
                        typer.echo("Status: idle")
                    else:
                        status = application.status(session_id)
                        typer.echo(
                            f"Status: {status.status}; generation={status.generation}; "
                            f"revision={status.revision}; active={str(status.active).lower()}"
                        )
                    continue
                if utterance == "/debug":
                    typer.echo(f"Debug: run_id={last_run_id or 'none'}")
                    continue
                if utterance == "/events":
                    typer.echo(f"Trace: {bundle.trace_path}")
                    continue

                try:
                    resolved_skill = resolve_skill_command(
                        utterance,
                        bundle.skill_registry,
                        session_id=session_id,
                    )
                except ValueError as exc:
                    last_status = "failed"
                    typer.echo(f"Skill invocation failed: {exc}")
                    continue

                ticket_route = route_coworker_ticket(utterance)
                if ticket_route.kind == TicketRouteKind.INVALID_TICKET_INTENT:
                    last_status = "failed"
                    typer.echo(f"Invalid change ticket: {ticket_route.message}")
                    continue
                if ticket_route.kind == TicketRouteKind.VALID_TICKET:
                    coworker = _run_coworker(ticket_route)
                    if coworker is None:
                        last_status = "failed"
                        continue
                    last_status = coworker.status
                    last_run_id = coworker.run_id
                    continue

                try:
                    result = runner.run(
                        application.run(
                            RunRequest(
                                text=(
                                    resolved_skill.prompt
                                    if resolved_skill is not None
                                    else utterance
                                ),
                                session_id=session_id,
                                profile="home",
                                model_override=(
                                    resolved_skill.model_override
                                    if resolved_skill is not None
                                    else None
                                ),
                                resume=session_open,
                                run_policy=RunPolicy(
                                    max_tool_iterations=(
                                        bundle.config.runtime.max_tool_iterations
                                    ),
                                ),
                                dependencies={
                                    "skill_registry": bundle.skill_registry,
                                    "ask_user_prompt": ask_user,
                                },
                                environment=backend,
                            )
                        )
                    )
                except KeyboardInterrupt:
                    if session_open:
                        application.cancel(session_id)
                    last_status = "cancelled"
                    typer.echo("Run cancelled.")
                    continue
                except Exception as exc:
                    last_status = "failed"
                    typer.echo(f"Run failed: {exc}")
                    continue
                session_open = True
                last_status = str(result.status)
                last_run_id = result.run_id
                if not getattr(bundle, "live_rendered", False):
                    typer.echo(f"Assistant: {result.final_reply}")
                if result.status is RunStatus.CANCELLED:
                    typer.echo("Run cancelled.")
        finally:
            runner.run(application.aclose())


def _run_coworker(ticket_route):
    try:
        result = run_coworker_turn(ticket_route)
    except CoworkerAttemptError as exc:
        typer.echo(f"Change execution failed: {exc.error_type}")
        typer.echo(f"Artifacts: {exc.run_root}")
        return None
    except Exception as exc:
        typer.echo(f"Change execution failed: {exc}")
        return None
    typer.echo(f"Assistant: {result.final_reply}")
    typer.echo(
        "Scores: "
        f"trajectory={result.trajectory_score:.1f}, "
        f"result={result.result_score:.1f}, overall={result.overall_score:.1f}"
    )
    typer.echo(f"Formal success: {result.formal_success}")
    typer.echo(f"Artifacts: {result.artifact_path}")
    if result.video_path:
        typer.echo(f"Video: {result.video_path}")
    return result


def _enable_line_editing() -> None:
    try:
        import readline  # noqa: F401
    except ImportError:
        return


def _render_help() -> str:
    return "\n".join(
        [
            "Commands:",
            "/new: start a new session.",
            "/compact: persist an immediate context compaction.",
            "/status: show typed application session status.",
            "/events: show the application trace path.",
            "/doctor: check local configuration and dependencies.",
            "/exit: close owned application resources and exit.",
        ]
    )


__all__ = ["run_interactive_shell"]
