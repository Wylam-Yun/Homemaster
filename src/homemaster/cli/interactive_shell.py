"""Interactive Home shell over one long-lived V1.9 ApplicationRuntime."""

from __future__ import annotations

import asyncio
import importlib.metadata
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import typer

from homemaster.agent.session import new_session_id
from homemaster.application import RunPolicy, RunRequest, RunStatus
from homemaster.application.composition import HomeCliBackend, compose_application
from homemaster.cli.confirmation import CliConfirmationHandler, CliPermissionMode
from homemaster.cli.doctor import render_doctor_text, run_doctor
from homemaster.skills.commands import resolve_skill_command
from homemaster.tools.contracts import PermissionSubject


def _shell_version() -> str:
    try:
        return importlib.metadata.version("homemaster")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def _build_prompt_reader(ctx) -> Callable[[], str] | None:
    """Return the prompt_toolkit reader, or None to keep ``input()`` fallback."""

    from homemaster.cli.prompt_loop import interactive_prompt_supported

    if not interactive_prompt_supported():
        return None
    from homemaster.cli.prompt_loop import ShellPrompt

    def model_name() -> str | None:
        try:
            return ctx.bundle.config.get_provider().model
        except Exception:
            return None

    def context_usage() -> str | None:
        # TODO(v35): no usage/token field is exposed on SessionStatus,
        # ApplicationSession, or RunResult yet — wire the toolbar ctx= display
        # once the session surface publishes per-session context usage.
        return None

    prompt = ShellPrompt(model_name=model_name, context_usage=context_usage)
    return prompt.read


def run_interactive_shell(
    *,
    resume_session_id: str | None = None,
    continue_latest: bool = False,
    debug: bool = False,
    permission_mode: CliPermissionMode = CliPermissionMode.FULL_AUTO,
) -> None:
    from homemaster.cli.shell_commands import (
        ShellContext,
        dispatch_slash_command,
        echo_unknown_command_hint,
        interpolate_bang_output,
        run_local_command,
    )

    _enable_line_editing()
    typer.echo(f"HomeMaster {_shell_version()}")
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
    bundle = compose_application(
        run_label=f"shell-{new_session_id()}",
        progress=True,
        permission_mode=permission_mode.policy_mode,
        confirmation_handler=confirmation_handler,
    )
    application = bundle.application
    if confirmation_handler is not None:
        tool_executor = getattr(application, "tool_executor", None)
        permission_store = getattr(tool_executor, "permission_store", None)
        if permission_store is not None:
            confirmation_handler.bind_store(permission_store)
    session_id = resume_session_id or new_session_id()
    permission_subject = _interactive_permission_subject(permission_mode)

    with asyncio.Runner() as runner:
        ctx = ShellContext(
            application=application,
            runner=runner,
            bundle=bundle,
            session_id=session_id,
        )
        ctx.backend = HomeCliBackend(world_path=None, memory_path=None)

        async def ask_user(question: str) -> str:
            return await asyncio.to_thread(input, f"{question}\nanswer> ")

        def end_session(reason: str) -> None:
            if not ctx.session_open or ctx.application_session is None:
                return
            receipt = ctx.application_session.close(exit_reason=reason)
            ctx.application_session = None
            if receipt is not None:
                typer.echo(
                    f"[experience] Queued session finalization {ctx.session_id} ({receipt.job_id})"
                )

        def reset_backend() -> None:
            ctx.backend = HomeCliBackend(world_path=None, memory_path=None)

        ctx.close_session = end_session
        ctx.reset_backend = reset_backend

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
                ctx.session_id = session_ids[0]
            if resume_session_id is not None or continue_latest:
                runner.run(application.session_manager.resume(ctx.session_id))
                ctx.session_open = True
                ctx.application_session = application.session(ctx.session_id)
                typer.echo(f"Resumed session: {ctx.session_id}")
            typer.echo(
                "Enter a task. Commands: /help, /new, /compact, /status, /events, /doctor, /exit."
            )

            read_prompt = _build_prompt_reader(ctx)

            while True:
                try:
                    if read_prompt is not None:
                        utterance = read_prompt().strip()
                    else:
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
                if utterance.startswith("!"):
                    command = utterance[1:].strip()
                    if command:
                        run_local_command(command)
                    continue
                interpolated = interpolate_bang_output(utterance)
                if interpolated is None:
                    # A {!cmd} placeholder timed out; stderr already explains.
                    continue
                utterance = interpolated
                if dispatch_slash_command(utterance, ctx):
                    if ctx.exit_reason is not None:
                        finalize_for_exit(ctx.exit_reason)
                        typer.echo("Goodbye")
                        return
                    continue

                try:
                    resolved_skill = resolve_skill_command(
                        utterance,
                        bundle.skill_registry,
                        session_id=ctx.session_id,
                    )
                except ValueError as exc:
                    ctx.last_status = "failed"
                    typer.echo(f"Skill invocation failed: {exc}")
                    continue
                if resolved_skill is None and utterance.startswith("/"):
                    name = utterance[1:].partition(" ")[0].strip()
                    if name:
                        echo_unknown_command_hint(name, ctx)
                        continue

                try:
                    if ctx.application_session is None:
                        ctx.application_session = application.session(ctx.session_id)
                    result = runner.run(
                        application.run(
                            RunRequest(
                                text=(
                                    resolved_skill.prompt
                                    if resolved_skill is not None
                                    else utterance
                                ),
                                session_id=ctx.session_id,
                                profile="home",
                                model_override=(
                                    resolved_skill.model_override
                                    if resolved_skill is not None
                                    else None
                                ),
                                resume=ctx.session_open,
                                run_policy=RunPolicy(
                                    max_tool_iterations=(bundle.config.runtime.max_tool_iterations),
                                ),
                                permission_subject=permission_subject,
                                dependencies={
                                    "skill_registry": bundle.skill_registry,
                                    "ask_user_prompt": ask_user,
                                },
                                environment=ctx.backend,
                            )
                        )
                    )
                except KeyboardInterrupt:
                    if ctx.session_open:
                        application.cancel(ctx.session_id)
                    ctx.last_status = "cancelled"
                    typer.echo("Run cancelled.")
                    continue
                except Exception as exc:
                    ctx.last_status = "failed"
                    typer.echo(f"Run failed: {exc}")
                    continue
                ctx.session_open = True
                ctx.last_status = str(result.status)
                ctx.last_run_id = result.run_id
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
