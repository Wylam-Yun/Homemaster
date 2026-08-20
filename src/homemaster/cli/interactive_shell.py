"""Interactive Home shell over one long-lived V1.9 ApplicationRuntime."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager

import typer

from homemaster.agent.turn import new_session_id
from homemaster.application import RunPolicy, RunRequest, RunStatus
from homemaster.cli.composition import HomeCliBackend, create_home_application
from homemaster.cli.confirmation import CliConfirmationHandler, CliPermissionMode
from homemaster.cli.doctor import render_doctor_text, run_doctor
from homemaster.skills.commands import resolve_skill_command
from homemaster.tools.contracts import PermissionSubject


def run_interactive_shell(
    *,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    debug: bool = False,
    permission_mode: CliPermissionMode = CliPermissionMode.FULL_AUTO,
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

    if not isinstance(permission_mode, CliPermissionMode):
        raise TypeError("permission_mode must be CliPermissionMode")
    confirmation_handler = (
        CliConfirmationHandler() if permission_mode is CliPermissionMode.CONFIRM else None
    )
    bundle = create_home_application(
        run_label=f"shell-{new_session_id()}",
        progress=True,
        permission_mode=permission_mode.policy_mode,
        confirmation_handler=confirmation_handler,
    )
    application = bundle.application
    backend = HomeCliBackend(world_path=None, memory_path=None)
    session_id = resume_session_id or new_session_id()
    session_open = False
    last_status = "idle"
    last_run_id: str | None = None
    application_session = None
    permission_subject = _interactive_permission_subject(permission_mode)

    with asyncio.Runner() as runner:

        async def ask_user(question: str) -> str:
            return await asyncio.to_thread(input, f"{question}\nanswer> ")

        def end_session(reason: str) -> None:
            nonlocal application_session
            if not session_open or application_session is None:
                return
            receipt = application_session.close(exit_reason=reason)
            application_session = None
            if receipt is not None:
                typer.echo(
                    f"[experience] Queued session finalization {session_id} ({receipt.job_id})"
                )

        def finalize_for_exit(reason: str) -> None:
            with _ignore_sigint_during_cleanup(
                "[experience] Finalization in progress; Ctrl+C ignored until memory work completes."
            ):
                end_session(reason)

        try:
            if continue_latest:
                session_ids = application.session_manager.list_session_ids()
                if not session_ids:
                    raise FileNotFoundError("no persisted session is available to continue")
                session_id = session_ids[0]
            if resume_session_id is not None or continue_latest:
                runner.run(application.session_manager.resume(session_id))
                session_open = True
                application_session = application.session(session_id)
                typer.echo(f"Resumed session: {session_id}")
            typer.echo(
                "Enter a task. Commands: /help, /new, /compact, /status, /events, /doctor, /exit."
            )

            while True:
                try:
                    utterance = input("homemaster> ").strip()
                except EOFError:
                    finalize_for_exit("eof")
                    typer.echo("Goodbye")
                    return
                except KeyboardInterrupt:
                    finalize_for_exit("shell_interrupt")
                    typer.echo("\nGoodbye")
                    return
                if not utterance:
                    continue
                if utterance == "/exit":
                    finalize_for_exit("user_exit")
                    typer.echo("Goodbye")
                    return
                if utterance == "/help":
                    typer.echo(_render_help())
                    continue
                if utterance == "/new":
                    end_session("new_session")
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

                try:
                    if application_session is None:
                        application_session = application.session(session_id)
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
                                    max_tool_iterations=(bundle.config.runtime.max_tool_iterations),
                                ),
                                permission_subject=permission_subject,
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
            with _ignore_sigint_during_cleanup(
                "Shutdown in progress; Ctrl+C ignored until cleanup completes."
            ):
                runner.run(application.aclose())


@contextmanager
def _ignore_sigint_during_cleanup(message: str) -> Iterator[None]:
    previous_handler = signal.getsignal(signal.SIGINT)
    notice_emitted = False

    def ignore_sigint(signum, frame) -> None:
        del signum, frame
        nonlocal notice_emitted
        if not notice_emitted:
            typer.echo(message)
            notice_emitted = True

    signal.signal(signal.SIGINT, ignore_sigint)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous_handler)


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


def _interactive_permission_subject(mode: CliPermissionMode) -> PermissionSubject:
    subject = RunRequest(text="interactive permission subject").permission_subject
    capabilities = subject.capabilities
    if mode is CliPermissionMode.CONFIRM:
        capabilities = tuple(value for value in capabilities if value != "tool.auto")
    return PermissionSubject(
        subject_id=subject.subject_id,
        channel=subject.channel,
        roles=subject.roles,
        tenant_id=subject.tenant_id,
        capabilities=capabilities,
    )


__all__ = ["run_interactive_shell"]
