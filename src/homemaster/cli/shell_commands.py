"""Slash-command registry and local ``!`` shell execution for the interactive shell.

Commands are table-driven: ``CommandSpec`` entries live in ``REGISTRY``, dispatch
is a lookup (no hardcoded ``if utterance == ...`` ladder), ``/help`` is rendered
from the registry, and unknown ``/x`` input produces a did-you-mean hint.
"""

from __future__ import annotations

import difflib
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import typer
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from homemaster.agent.session import new_session_id
from homemaster.cli.doctor import render_doctor_text, run_doctor
from homemaster.cli.keymap import KEY_HELP_LINES

# An argument completer maps the current argument prefix to (value, meta) pairs.
ArgCompleter = Callable[[str], Iterable[tuple[str, str]]]
CommandHandler = Callable[["ShellContext", str], None]

# ``!cmd`` / ``{!cmd}`` guard rails: a local command is killed once it runs past
# the wall-clock timeout or once its captured output exceeds either bound.
# Kept as module constants so a config surface can override them later.
_LOCAL_CMD_TIMEOUT_S = 30.0
_LOCAL_CMD_OUTPUT_LIMIT = 64 * 1024  # characters of captured stdout+stderr
_LOCAL_CMD_OUTPUT_MAX_LINES = 1000


class ShellContext:
    """Mutable state shared between the input loop and slash-command handlers."""

    def __init__(
        self,
        *,
        application: Any,
        runner: Any,
        bundle: Any,
        session_id: str,
        echo: Callable[[str], None] = typer.echo,
    ) -> None:
        self.application = application
        self.runner = runner
        self.bundle = bundle
        self.echo = echo
        self.session_id = session_id
        self.backend: Any = None
        self.application_session: Any = None
        self.session_open = False
        self.last_status = "idle"
        self.last_run_id: str | None = None
        self.exit_reason: str | None = None
        # Late-bound by the interactive shell so handlers stay decoupled from
        # loop-local closures (session finalization, backend rebuild).
        self.close_session: Callable[[str], None] = lambda reason: None
        self.reset_backend: Callable[[], None] = lambda: None


@dataclass(frozen=True)
class CommandSpec:
    name: str
    run: CommandHandler
    aliases: tuple[str, ...] = ()
    usage: str = ""
    help: str = ""
    arg_completer: ArgCompleter | None = None


REGISTRY: list[CommandSpec] = []


def slash_command(
    name: str,
    *,
    aliases: Iterable[str] = (),
    usage: str = "",
    help: str = "",
    arg_completer: ArgCompleter | None = None,
) -> Callable[[CommandHandler], CommandHandler]:
    """Register a slash command handler ``fn(ctx, args)`` in REGISTRY."""

    def decorator(fn: CommandHandler) -> CommandHandler:
        REGISTRY.append(
            CommandSpec(
                name=name,
                run=fn,
                aliases=tuple(aliases),
                usage=usage,
                help=help,
                arg_completer=arg_completer,
            )
        )
        return fn

    return decorator


def find_command(name: str) -> CommandSpec | None:
    for spec in REGISTRY:
        if spec.name == name or name in spec.aliases:
            return spec
    return None


def suggest_command(name: str) -> str | None:
    pool = [spec.name for spec in REGISTRY]
    pool.extend(alias for spec in REGISTRY for alias in spec.aliases)
    matches = difflib.get_close_matches(name, pool, n=1, cutoff=0.6)
    return matches[0] if matches else None


def render_help() -> str:
    lines = ["Commands:"]
    for spec in REGISTRY:
        label = f"/{spec.name}"
        if spec.usage:
            label += f" {spec.usage}"
        suffix = ""
        if spec.aliases:
            suffix = " (aliases: " + ", ".join(f"/{a}" for a in spec.aliases) + ")"
        lines.append(f"{label}: {spec.help}{suffix}")
    lines.append("")
    lines.append("Keys:")
    lines.extend(f"  {line}" for line in KEY_HELP_LINES)
    return "\n".join(lines)


def dispatch_slash_command(utterance: str, ctx: ShellContext) -> bool:
    """Run a registered ``/`` command.

    Returns True when a registry entry handled the line. Returns False when the
    leading token is not a registered command so the caller can still try skill
    resolution or the unknown-command hint.
    """

    if not utterance.startswith("/"):
        return False
    name, _, args = utterance[1:].partition(" ")
    name = name.strip()
    if not name:
        return False
    spec = find_command(name)
    if spec is None:
        return False
    spec.run(ctx, args)
    return True


def echo_unknown_command_hint(name: str, ctx: ShellContext) -> None:
    suggestion = suggest_command(name)
    if suggestion is not None:
        ctx.echo(f"Unknown command /{name}. Did you mean /{suggestion}?")
    else:
        ctx.echo(f"Unknown command /{name}. Type /help for the command list.")


def _kill_local_command(proc: subprocess.Popen[str]) -> None:
    """Kill ``proc`` — on POSIX the whole process group goes with it so
    grandchildren (``yes``, ``tail -f`` …) cannot outlive the ``!`` command."""

    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_local_command(command: str, *, console: Console | None = None) -> int:
    """Stream ``command`` through the local shell; output never enters context.

    The command is killed once it runs longer than ``_LOCAL_CMD_TIMEOUT_S``
    (panel gets a ``[timed out]`` marker) or once its captured output exceeds
    ``_LOCAL_CMD_OUTPUT_LIMIT`` characters / ``_LOCAL_CMD_OUTPUT_MAX_LINES``
    lines (panel gets a ``[truncated]`` marker).
    """

    console = console or Console()
    proc = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=os.name == "posix",
    )
    assert proc.stdout is not None

    lines: list[str] = []
    captured = 0
    timed_out = False
    truncated = False

    # A timer does the kill because a quiet command (``sleep``) blocks the read
    # loop in readline() — the deadline cannot be enforced from inside the loop.
    def on_timeout() -> None:
        nonlocal timed_out
        if proc.poll() is None:
            timed_out = True
            _kill_local_command(proc)

    timer = threading.Timer(_LOCAL_CMD_TIMEOUT_S, on_timeout)
    timer.daemon = True
    timer.start()

    def over_limit() -> bool:
        return captured >= _LOCAL_CMD_OUTPUT_LIMIT or len(lines) >= _LOCAL_CMD_OUTPUT_MAX_LINES

    def marker() -> str | None:
        if timed_out:
            return "[timed out]"
        if truncated:
            return "[truncated]"
        return None

    try:
        if console.is_terminal:
            with Live(console=console, refresh_per_second=8) as live:
                for chunk in proc.stdout:
                    lines.append(chunk)
                    captured += len(chunk)
                    live.update(
                        Panel(
                            Text("".join(lines)),
                            title=Text(f"$ {command}"),
                            border_style="cyan",
                        )
                    )
                    if over_limit():
                        truncated = True
                        _kill_local_command(proc)
                        break
                code = proc.wait()
                note = marker()
                live.update(
                    Panel(
                        Text("".join(lines) + (f"\n{note}\n" if note else "")),
                        title=Text(f"$ {command}"),
                        # Text() wrapper: a str subtitle is markup-parsed and
                        # "[timed out]" would render as an empty string.
                        subtitle=Text(note) if note is not None else f"exit {code}",
                        border_style="cyan" if code == 0 and note is None else "red",
                    )
                )
            return code
        console.print(f"[bold]$ {escape(command)}[/]")
        for chunk in proc.stdout:
            lines.append(chunk)
            captured += len(chunk)
            console.print(chunk, end="", markup=False, highlight=False)
            if over_limit():
                truncated = True
                _kill_local_command(proc)
                break
        code = proc.wait()
        note = marker()
        if note is not None:
            console.print(note, style="red", markup=False, highlight=False)
        if code != 0:
            console.print(f"[red]exit {code}[/red]")
        return code
    finally:
        timer.cancel()


_BANG_INLINE = re.compile(r"\{!([^{}]*)\}")


def interpolate_bang_output(text: str) -> str | None:
    """Replace ``{!cmd}`` placeholders with the command's stdout.

    stderr goes to the process stderr and never enters the substituted text.
    Returns None when a command exceeds ``_LOCAL_CMD_TIMEOUT_S`` so the caller
    can drop the line without leaking TimeoutExpired into the input loop.
    """

    failed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal failed
        if failed:
            return ""
        command = match.group(1).strip()
        if not command:
            return ""
        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=_LOCAL_CMD_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"{{!{command}}} timed out after {_LOCAL_CMD_TIMEOUT_S:.0f}s\n")
            failed = True
            return ""
        except OSError as exc:
            sys.stderr.write(f"{{!{command}}} failed: {exc}\n")
            return ""
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        if completed.returncode != 0:
            sys.stderr.write(f"{{!{command}}} exited with code {completed.returncode}\n")
        stdout = completed.stdout
        if len(stdout) > _LOCAL_CMD_OUTPUT_LIMIT:
            stdout = stdout[:_LOCAL_CMD_OUTPUT_LIMIT].rstrip("\n") + "\n[truncated]"
        return stdout.rstrip("\n")

    result = _BANG_INLINE.sub(replace, text)
    return None if failed else result


def _command_name_completions(prefix: str) -> Iterable[tuple[str, str]]:
    for spec in REGISTRY:
        for candidate in (spec.name, *spec.aliases):
            if candidate.startswith(prefix):
                yield candidate, spec.help


@slash_command("new", help="start a new session.")
def _cmd_new(ctx: ShellContext, args: str) -> None:
    del args
    ctx.close_session("new_session")
    ctx.session_id = new_session_id()
    ctx.reset_backend()
    ctx.session_open = False
    ctx.last_status = "idle"
    ctx.last_run_id = None
    ctx.echo("New session created.")


@slash_command("compact", help="persist an immediate context compaction.")
def _cmd_compact(ctx: ShellContext, args: str) -> None:
    del args
    if not ctx.session_open:
        ctx.echo("Context compaction: no active session.")
        return
    try:
        compact = ctx.runner.run(ctx.application.compact(ctx.session_id))
    except Exception as exc:
        ctx.last_status = "failed"
        ctx.echo(f"Context compaction failed: {exc}")
        return
    ctx.last_status = "compacted" if compact.triggered else "noop"
    ctx.echo(
        "Context compaction: "
        f"status={ctx.last_status}, kind={compact.kind}, revision={compact.revision}"
    )


@slash_command("status", help="show typed application session status.")
def _cmd_status(ctx: ShellContext, args: str) -> None:
    del args
    if not ctx.session_open:
        ctx.echo("Status: idle")
        return
    status = ctx.application.status(ctx.session_id)
    ctx.echo(
        f"Status: {status.status}; generation={status.generation}; "
        f"revision={status.revision}; active={str(status.active).lower()}"
    )


@slash_command("events", help="show the application trace path.")
def _cmd_events(ctx: ShellContext, args: str) -> None:
    del args
    ctx.echo(f"Trace: {ctx.bundle.trace_path}")


@slash_command("doctor", help="check local configuration and dependencies.")
def _cmd_doctor(ctx: ShellContext, args: str) -> None:
    del args
    ctx.echo(render_doctor_text(run_doctor(live=False)))


@slash_command("debug", help="show the last run id.")
def _cmd_debug(ctx: ShellContext, args: str) -> None:
    del args
    ctx.echo(f"Debug: run_id={ctx.last_run_id or 'none'}")


@slash_command(
    "help",
    aliases=("?",),
    usage="[command]",
    help="show this help.",
    arg_completer=_command_name_completions,
)
def _cmd_help(ctx: ShellContext, args: str) -> None:
    del args
    ctx.echo(render_help())


@slash_command(
    "exit",
    aliases=("quit", "q"),
    help="close owned application resources and exit.",
)
def _cmd_exit(ctx: ShellContext, args: str) -> None:
    del args
    ctx.exit_reason = "user_exit"


__all__ = [
    "REGISTRY",
    "ArgCompleter",
    "CommandSpec",
    "ShellContext",
    "dispatch_slash_command",
    "echo_unknown_command_hint",
    "find_command",
    "interpolate_bang_output",
    "render_help",
    "run_local_command",
    "slash_command",
    "suggest_command",
]
