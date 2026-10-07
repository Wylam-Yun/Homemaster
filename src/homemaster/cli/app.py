"""Typer CLI entrypoint for HomeMaster.

Entry points after the web-first rearrangement:

* bare ``homemaster`` — run the Web Console in the foreground and open a
  browser (headless/SSH → print the URL and an SSH-tunnel hint instead);
* ``homemaster shell`` — interactive thin-client shell over REST+WS
  (``ensure_server`` auto-spawns a loopback server unless ``HOMEMASTER_SERVER``
  points at a managed one); ``shell --local`` keeps the in-process shell;
* ``homemaster run`` / ``-p`` — one-shot thin-client run streaming WebEvents;
  ``--local`` keeps the in-process compose path, and compose-time flags
  (``--world``/``--memory``/``--alfworld``/``--browser``) are legal only there;
* ``serve``/``doctor``/``memory``/``session``/``cron``/``benchmark-*`` stay
  local machine-management commands.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer

import homemaster.cli.doctor as _doctor_module
from homemaster.cli.benchmark_locomo import handle_benchmark_locomo
from homemaster.cli.child_worker import run_child_worker
from homemaster.cli.confirmation import CliPermissionMode
from homemaster.cli.cron_command import cron_app
from homemaster.cli.doctor import doctor_report_to_json, render_doctor_text, run_doctor
from homemaster.cli.dry_run import build_dry_run_preview
from homemaster.cli.errors import render_error_and_exit
from homemaster.cli.gateway_command import run_gateway
from homemaster.cli.interactive_shell import run_interactive_shell
from homemaster.cli.memory_command import memory_app
from homemaster.cli.remote_shell import run_remote_shell
from homemaster.cli.renderers import parse_output_format, render_dry_run
from homemaster.cli.run_command import (
    handle_print,
    handle_print_remote,
    handle_run,
    handle_run_remote,
)
from homemaster.cli.server_process import probe_server
from homemaster.cli.session_command import session_app
from homemaster.config import load_config, pin_default_config_path
from homemaster.events.logger import setup_logging
from homemaster.web.serve import run_web_server, validate_bind_host, validate_port_available

app = typer.Typer(
    add_completion=False,
    help="HomeMaster — generic agent loop CLI (web-first: bare = serve + browser).",
)
app.add_typer(session_app, name="session")
app.add_typer(cron_app, name="cron")
app.add_typer(memory_app, name="memory")

_LOCAL_ONLY = "--local"
_SERVER_ENV = "HOMEMASTER_SERVER"


def _reject_local_only(flag: str, local: bool) -> None:
    if not local:
        raise typer.BadParameter(
            f"{flag} only works with {_LOCAL_ONLY} (in-process compose)"
        )


@app.command("child-worker", hidden=True)
def child_worker_command(
    model: Annotated[str | None, typer.Option("--model")] = None,
) -> None:
    raise typer.Exit(code=run_child_worker(model=model))


def _open_browser_when_ready(url: str, *, timeout_s: float = 15.0) -> None:
    """Open the console once the server answers; runs on a daemon thread."""

    import time

    import httpx

    def worker() -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if httpx.get(url, timeout=1.0).status_code < 500:
                    webbrowser.open(url)
                    return
            except (httpx.HTTPError, OSError):
                time.sleep(0.25)
        webbrowser.open(url)

    threading.Thread(target=worker, name="homemaster-browser-open", daemon=True).start()


def _headless() -> bool:
    return bool(os.environ.get("SSH_CONNECTION")) and not os.environ.get("DISPLAY")


def _serve_foreground(
    *,
    host: str,
    port: int,
    config_path: Path | None,
    open_browser: bool,
) -> None:
    """Bare entrypoint: foreground serve + browser handoff (openclaw onboard)."""

    validated = validate_bind_host(host)
    url = f"http://{validated}:{port}"
    alive, _meta = probe_server(url)
    if alive:
        # A server already owns the port — bare `homemaster` means "get me to
        # the console", so open the existing one instead of dying on EADDRINUSE.
        if open_browser and not _headless():
            typer.echo(f"HomeMaster is already running: {url} (opening browser)")
            webbrowser.open(url)
        else:
            typer.echo(f"HomeMaster is already running: {url}")
            if _headless():
                typer.echo(f"  ssh -L {port}:127.0.0.1:{port} <this-host> to open locally.")
        return
    validate_port_available(validated, port)
    if not open_browser:
        typer.echo(f"HomeMaster Web Console: {url}")
    elif _headless():
        typer.echo(f"HomeMaster Web Console: {url}")
        typer.echo("SSH session without a display — forward the port, e.g.:")
        typer.echo(f"  ssh -L {port}:127.0.0.1:{port} <this-host>")
        typer.echo(f"then open {url} locally.")
    else:
        typer.echo(f"HomeMaster Web Console: {url} (opening browser)")
        _open_browser_when_ready(url)
    run_web_server(host=validated, port=port, config_path=config_path)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable DEBUG logs and experience finalizer details."),
    ] = False,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the HomeMaster YAML configuration file."),
    ] = None,
    host: Annotated[
        str,
        typer.Option("--host", help="Loopback address for the bare-serve Web Console."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Port for the bare-serve Web Console."),
    ] = 8000,
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Serve in the foreground without opening a browser."),
    ] = False,
    server: Annotated[
        str | None,
        typer.Option(
            "--server",
            help=f"HomeMaster server URL for thin-client commands (or {_SERVER_ENV}).",
        ),
    ] = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="Compose in-process instead of using a server."),
    ] = False,
    print_prompt: Annotated[
        str | None,
        typer.Option("--print", "-p", help="Print one response and exit (thin client)."),
    ] = None,
    output_format: Annotated[
        str | None,
        typer.Option(
            "--output-format",
            help="Output format for print or dry-run: text, json, or stream-json.",
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview local resolution without external I/O."),
    ] = False,
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session."),
    ] = None,
    continue_latest: Annotated[
        bool,
        typer.Option("--continue", help="Resume the latest persisted session."),
    ] = False,
    probe: Annotated[
        bool,
        typer.Option("--probe", help="Probe configured external discovery during dry-run."),
    ] = False,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider-name", help="Select one configured chat provider."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the selected provider model for this request."),
    ] = None,
    run_label: Annotated[
        str | None,
        typer.Option("--run-label", help="Stable artifact directory label (--local runs)."),
    ] = None,
    browser: Annotated[
        bool,
        typer.Option(
            "--browser",
            help="Use the configured Ant browser environment (--local one-shot runs).",
        ),
    ] = False,
) -> None:
    """Bare: serve the Web Console + open it. ``-p``/``--dry-run`` run one shot."""

    if ctx.invoked_subcommand is not None:
        if any(
            (
                print_prompt is not None,
                output_format is not None,
                dry_run,
                resume_session_id is not None,
                continue_latest,
                probe,
                provider_name is not None,
                model is not None,
                run_label is not None,
                browser,
                local,
                server is not None,
                no_browser,
                host != "127.0.0.1",
                port != 8000,
            )
        ):
            raise typer.BadParameter(
                "top-level options are only valid without a subcommand; "
                "pass them to the subcommand directly"
            )
        if config_path is not None:
            raise typer.BadParameter(
                "global --config is only valid without a subcommand; "
                "pass --config to the subcommand directly"
            )
        return
    if (print_prompt is not None or dry_run) and (
        host != "127.0.0.1" or port != 8000 or no_browser
    ):
        raise typer.BadParameter(
            "--host/--port/--no-browser only apply to bare 'homemaster' serve"
        )
    try:
        if dry_run:
            _run_dry_run(
                print_prompt=print_prompt,
                output_format=output_format,
                config_path=config_path,
                probe=probe,
                provider_name=provider_name,
                model=model,
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                run_label=run_label,
                browser=browser,
                local=local,
                server=server,
            )
            return
        if print_prompt is not None:
            _run_print(
                prompt=print_prompt,
                output_format=output_format,
                local=local,
                server=server,
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                provider_name=provider_name,
                model=model,
                config_path=config_path,
                run_label=run_label,
                browser=browser,
            )
            return
        _validate_bare(
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            probe=probe,
            provider_name=provider_name,
            model=model,
            output_format=output_format,
            run_label=run_label,
            browser=browser,
            local=local,
            server=server,
        )
        setup_logging(level="DEBUG" if debug else "INFO")
        _serve_foreground(
            host=host,
            port=port,
            config_path=config_path,
            open_browser=not no_browser,
        )
    except (typer.Exit, typer.BadParameter, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


def _run_dry_run(
    *,
    print_prompt: str | None,
    output_format: str | None,
    config_path: Path | None,
    probe: bool,
    provider_name: str | None,
    model: str | None,
    resume_session_id: str | None,
    continue_latest: bool,
    run_label: str | None,
    browser: bool,
    local: bool,
    server: str | None,
) -> None:
    if resume_session_id is not None or continue_latest:
        raise typer.BadParameter("--resume/--continue are not valid with --dry-run")
    if run_label is not None:
        raise typer.BadParameter("--run-label is not valid with --dry-run")
    if browser:
        raise typer.BadParameter("--browser cannot be combined with --dry-run")
    if local:
        raise typer.BadParameter("--local is implied by --dry-run; drop the flag")
    if server is not None:
        raise typer.BadParameter("--server is not valid with --dry-run")
    resolved_format = parse_output_format(output_format)
    prompt = print_prompt.strip() if print_prompt is not None else None
    if print_prompt is not None and not prompt:
        raise typer.BadParameter("-p/--print requires a non-empty prompt")
    preview = build_dry_run_preview(
        prompt=prompt,
        config_path=config_path,
        probe=probe,
        provider_name=provider_name,
        model=model,
    )
    typer.echo(render_dry_run(preview, resolved_format))


def _run_print(
    *,
    prompt: str,
    output_format: str | None,
    local: bool,
    server: str | None,
    resume_session_id: str | None,
    continue_latest: bool,
    provider_name: str | None,
    model: str | None,
    config_path: Path | None,
    run_label: str | None,
    browser: bool,
) -> None:
    resolved_format = parse_output_format(output_format)
    text = prompt.strip()
    if not text:
        raise typer.BadParameter("-p/--print requires a non-empty prompt")
    if resume_session_id is not None and continue_latest:
        raise typer.BadParameter("--continue cannot be combined with --resume")
    if run_label is not None and not local:
        _reject_local_only("--run-label", local)
    if browser and not local:
        _reject_local_only("--browser", local)
    if server is not None and local:
        raise typer.BadParameter("--server cannot be combined with --local")
    if local:
        handle_print(
            prompt=text,
            output_format=resolved_format,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            provider_name=provider_name,
            model=model,
            config_path=config_path,
            tool_environment="browser" if browser else None,
            run_label=run_label,
        )
        return
    handle_print_remote(
        prompt=text,
        output_format=resolved_format,
        server=server,
        resume_session_id=resume_session_id,
        continue_latest=continue_latest,
        provider_name=provider_name,
        model=model,
        config_path=config_path,
    )


def _validate_bare(
    *,
    resume_session_id: str | None,
    continue_latest: bool,
    probe: bool,
    provider_name: str | None,
    model: str | None,
    output_format: str | None,
    run_label: str | None,
    browser: bool,
    local: bool,
    server: str | None,
) -> None:
    if resume_session_id is not None or continue_latest:
        raise typer.BadParameter(
            "--resume/--continue apply to 'homemaster shell' or '-p'; "
            "use 'homemaster shell --resume <id>'"
        )
    if provider_name is not None or model is not None:
        raise typer.BadParameter("--provider-name/--model require --print or --dry-run")
    if output_format is not None:
        raise typer.BadParameter("--output-format requires --print or --dry-run")
    if probe:
        raise typer.BadParameter("--probe is only valid with --dry-run")
    if run_label is not None:
        raise typer.BadParameter("--run-label requires -p --local")
    if browser:
        raise typer.BadParameter("--browser requires --print --local or 'gateway --browser'")
    if local:
        raise typer.BadParameter("--local only applies to -p/run/shell; bare serve is remote-free")
    if server is not None:
        raise typer.BadParameter(
            "--server selects a thin-client target; bare 'homemaster' is the server"
        )


@app.command("run")
def run_command(
    utterance: Annotated[
        str | None,
        typer.Option("--utterance", help="Chinese user instruction to execute."),
    ] = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="Compose in-process instead of using a server."),
    ] = False,
    server: Annotated[
        str | None,
        typer.Option("--server", help="HomeMaster server URL (or HOMEMASTER_SERVER)."),
    ] = None,
    world_path: Annotated[
        Path | None,
        typer.Option("--world", help="Optional world.json override (--local only)."),
    ] = None,
    memory_path: Annotated[
        Path | None,
        typer.Option("--memory", help="Optional base memory.json override (--local only)."),
    ] = None,
    browser: Annotated[
        bool,
        typer.Option("--browser", help="Browser tool environment (--local only)."),
    ] = False,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable run id for traces (--local only)."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level (DEBUG/INFO/WARNING/ERROR)."),
    ] = "INFO",
    progress: Annotated[
        bool,
        typer.Option("--progress/--no-progress", help="Show high-level progress events on stderr."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Show full thinking and tool result output on stderr."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            help="Suppress console event output; stdout still prints the result.",
        ),
    ] = False,
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session id."),
    ] = None,
    continue_latest: Annotated[
        bool,
        typer.Option("--continue", help="Resume the latest persisted session."),
    ] = False,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider-name", help="Select one configured chat provider."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the selected provider model for this run."),
    ] = None,
    output_format: Annotated[
        str | None,
        typer.Option("--output-format", help="text, json, or stream-json."),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Config for --local compose or spawned server."),
    ] = None,
) -> None:
    """Run one HomeMaster agent task through the thin client (or --local)."""

    try:
        for flag, value in (
            ("--world", world_path),
            ("--memory", memory_path),
            ("--run-id", run_id),
        ):
            if value is not None:
                _reject_local_only(flag, local)
        if browser:
            _reject_local_only("--browser", local)
        if server is not None and local:
            raise typer.BadParameter("--server cannot be combined with --local")
        if local:
            handle_run(
                utterance=utterance,
                world_path=world_path,
                memory_path=memory_path,
                run_id=run_id,
                log_level=log_level,
                progress=progress,
                verbose=verbose,
                quiet=quiet,
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                provider_name=provider_name,
                model=model,
                tool_environment="browser" if browser else None,
                config_path=config_path,
            )
            return
        setup_logging(level=log_level)
        handle_run_remote(
            utterance=utterance,
            server=server,
            config_path=config_path,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
            provider_name=provider_name,
            model=model,
            output_format=parse_output_format(output_format),
            quiet=quiet,
            verbose=verbose,
        )
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("auth")
def auth_command_entry(
    print_only: Annotated[
        bool,
        typer.Option("--print-only", help="Print the config snippet without writing."),
    ] = False,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Target config (default: $HOMEMASTER_HOME/config.yaml)."),
    ] = None,
) -> None:
    """Interactive provider credential wizard (writes $HOMEMASTER_HOME/config.yaml)."""
    from homemaster.cli.auth import auth_command

    raise typer.Exit(code=auth_command(print_only=print_only, config_path=config_path))


@app.command("doctor")
def doctor_command(
    live: Annotated[
        bool,
        typer.Option("--live", help="Run live chat and MemoryEmbedding provider smoke checks."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print machine-readable JSON."),
    ] = False,
    alfworld: Annotated[
        bool,
        typer.Option("--alfworld", help="Check the optional ALFWorld worker binding and IPC."),
    ] = False,
) -> None:
    """Check HomeMaster local environment and optional live providers."""
    try:
        setup_logging()
        report = run_doctor(live=live, alfworld=alfworld)
        if json_output:
            typer.echo(doctor_report_to_json(report))
        else:
            typer.echo(render_doctor_text(report))
        if report.has_failures:
            raise typer.Exit(code=1)
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("shell")
def shell_command(
    resume_session_id: Annotated[
        str | None,
        typer.Option("--resume", help="Resume the specified persisted session id."),
    ] = None,
    continue_latest: Annotated[
        bool,
        typer.Option("--continue", help="Resume the latest persisted session."),
    ] = False,
    server: Annotated[
        str | None,
        typer.Option("--server", help="HomeMaster server URL (or HOMEMASTER_SERVER)."),
    ] = None,
    local: Annotated[
        bool,
        typer.Option("--local", help="In-process shell via compose_application."),
    ] = False,
    permission_mode: Annotated[
        CliPermissionMode,
        typer.Option(
            "--permission-mode",
            help="Interactive tool policy (--local only): full_auto, confirm, or plan.",
        ),
    ] = CliPermissionMode.FULL_AUTO,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Config for --local compose or spawned server."),
    ] = None,
) -> None:
    """Launch the thin-client interactive shell (server-backed)."""

    if permission_mode is not CliPermissionMode.FULL_AUTO and not local:
        raise typer.BadParameter("--permission-mode only works with --local")
    if server is not None and local:
        raise typer.BadParameter("--server cannot be combined with --local")
    try:
        if local:
            if config_path is not None:
                _doctor_module.HOMEMASTER_CONFIG_PATH = pin_default_config_path(config_path)
            run_interactive_shell(
                resume_session_id=resume_session_id,
                continue_latest=continue_latest,
                permission_mode=permission_mode,
            )
            return
        code = run_remote_shell(
            server=server,
            config_path=config_path,
            resume_session_id=resume_session_id,
            continue_latest=continue_latest,
        )
        if code:
            raise typer.Exit(code=code)
    except (typer.Exit, typer.BadParameter, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("gateway")
def gateway_command(
    alfworld: Annotated[
        bool,
        typer.Option("--alfworld", help="Use the configured fixed ALFWorld environment."),
    ] = False,
    browser: Annotated[
        bool,
        typer.Option("--browser", help="Use the configured Ant browser environment."),
    ] = False,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the HomeMaster YAML configuration file."),
    ] = None,
) -> None:
    """Run the configured Feishu/Lark WebSocket Gateway."""
    try:
        if alfworld and browser:
            raise typer.BadParameter("--alfworld and --browser are mutually exclusive")
        run_gateway(
            load_config(config_path),
            environment="alfworld" if alfworld else "browser" if browser else None,
        )
    except (typer.Exit, typer.BadParameter, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("serve")
def serve_command(
    host: Annotated[
        str,
        typer.Option("--host", help="Loopback address for the local Web Console."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", min=1, max=65535, help="Local Web Console port."),
    ] = 8000,
    alfworld: Annotated[
        bool,
        typer.Option(
            "--alfworld",
            help="Use the configured fixed ALFWorld environment in the Web Console.",
        ),
    ] = False,
    browser: Annotated[
        bool,
        typer.Option(
            "--browser",
            help="Use the configured browser environment in the Web Console.",
        ),
    ] = False,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the HomeMaster YAML configuration file."),
    ] = None,
) -> None:
    """Run the loopback-only HomeMaster Web Console."""

    try:
        if alfworld and browser:
            raise typer.BadParameter("--alfworld and --browser are mutually exclusive")
        run_web_server(
            host=host,
            port=port,
            environment="alfworld" if alfworld else "browser" if browser else None,
            config_path=config_path,
        )
    except (ValueError, typer.BadParameter) as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=2) from exc
    except (typer.Exit, SystemExit):
        raise
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("benchmark-alfworld")
def benchmark_alfworld_command(
    alfworld_root: Annotated[
        Path,
        typer.Option("--alfworld-root", help="Path to the local ALFWorld repository."),
    ],
    alfworld_config: Annotated[
        Path,
        typer.Option("--alfworld-config", help="Path to ALFWorld YAML config."),
    ],
    trace_root: Annotated[
        Path,
        typer.Option("--trace-root", help="Output directory for benchmark traces."),
    ] = Path("/tmp/homemaster/alfworld"),
    env_type: Annotated[
        str,
        typer.Option("--env-type", help="ALFWorld environment type."),
    ] = "AlfredThorEnv",
    split: Annotated[
        str,
        typer.Option("--split", help="train, valid_seen, or valid_unseen."),
    ] = "valid_seen",
    episodes: Annotated[
        int,
        typer.Option("--episodes", help="Number of episodes to run."),
    ] = 1,
    memory_mode: Annotated[
        str,
        typer.Option("--memory-mode", help="disabled, readonly, or full."),
    ] = "disabled",
    max_invalid_actions: Annotated[
        int,
        typer.Option("--max-invalid-actions", help="Fail after this many invalid actions."),
    ] = 100,
    max_env_steps: Annotated[
        int,
        typer.Option(
            "--max-env-steps",
            help="Fail after this many ALFWorld environment action steps.",
        ),
    ] = 50,
    max_tool_iterations: Annotated[
        int,
        typer.Option("--max-tool-iterations", help="Maximum HomeMaster tool iterations."),
    ] = 200,
    provider_config: Annotated[
        Path | None,
        typer.Option("--api-config", help="Optional provider config JSON override."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option(
            "--provider-name",
            help="Optional provider name override; defaults to the API config default.",
        ),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable benchmark run id."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
    observation_mode: Annotated[
        str,
        typer.Option("--observation-mode", help="visual_eval or textual_debug."),
    ] = "visual_eval",
    trial_manifest: Annotated[
        Path | None,
        typer.Option(
            "--trial-manifest",
            help="Optional ordered trial-selection manifest for reproducible AlfredThorEnv runs.",
        ),
    ] = None,
) -> None:
    """Run HomeMaster on ALFWorld benchmark episodes."""
    try:
        from homemaster.alfworld.tracing import split_trace_bucket
        from homemaster.cli.benchmark_alfworld import handle_benchmark_alfworld

        summary = handle_benchmark_alfworld(
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            trace_root=trace_root,
            env_type=env_type,
            split=split,
            episodes=episodes,
            memory_mode=memory_mode,
            max_invalid_actions=max_invalid_actions,
            max_env_steps=max_env_steps,
            max_tool_iterations=max_tool_iterations,
            provider_config=provider_config,
            provider_name=provider_name,
            run_id=run_id,
            log_level=log_level,
            observation_mode=observation_mode,
            trial_manifest=trial_manifest,
        )
        metrics = summary.to_dict()
        typer.echo(f"run_id: {summary.run_id}")
        typer.echo(f"episodes: {len(summary.episodes)}")
        typer.echo(f"success_rate: {summary.success_rate:.3f}")
        typer.echo(f"raw_success_rate: {float(metrics['raw_success_rate']):.3f}")
        typer.echo(f"agent_scored_episodes: {metrics['agent_scored_episodes']}")
        typer.echo(
            f"agent_success_rate_on_valid: {float(metrics['agent_success_rate_on_valid']):.3f}"
        )
        typer.echo(f"harness_invalid_episodes: {metrics['harness_invalid_episodes']}")
        typer.echo(f"harness_valid_coverage: {float(metrics['harness_valid_coverage']):.3f}")
        typer.echo(f"evaluation_valid_coverage: {float(metrics['evaluation_valid_coverage']):.3f}")
        typer.echo(f"harness_coverage: {float(metrics['harness_coverage']):.3f}")
        typer.echo(f"provider_availability: {float(metrics['provider_availability']):.3f}")
        typer.echo(f"runtime_availability: {float(metrics['runtime_availability']):.3f}")
        typer.echo(f"cancelled_episodes: {int(metrics['cancelled_episodes'])}")
        typer.echo(
            f"formal_score_available: {str(bool(metrics['formal_score_available'])).lower()}"
        )
        typer.echo(f"trace_root: {trace_root / split_trace_bucket(split) / summary.run_id}")
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("benchmark-locomo")
def benchmark_locomo_command(
    data_file: Annotated[
        Path,
        typer.Option("--data-file", help="Path to LoCoMo locomo10.json."),
    ] = Path("../locomo/data/locomo10.json"),
    sample_id: Annotated[
        str,
        typer.Option("--sample-id", help="LoCoMo conversation sample id."),
    ] = "conv-26",
    focal_speaker: Annotated[
        str,
        typer.Option("--focal-speaker", help="Person name used as the memory user id."),
    ] = "Caroline",
    max_source_turns: Annotated[
        int,
        typer.Option("--max-source-turns", help="Maximum original dialogue turns to ingest."),
    ] = 100,
    qa_probes: Annotated[
        int,
        typer.Option("--qa-probes", help="Answerable LoCoMo questions to run without scoring."),
    ] = 10,
    run_deadline_seconds: Annotated[
        float,
        typer.Option(
            "--run-deadline-seconds",
            help="Maximum wall time for each HomeMaster source or QA run.",
        ),
    ] = 300.0,
    trace_root: Annotated[
        Path,
        typer.Option("--trace-root", help="Output directory for LoCoMo benchmark runs."),
    ] = Path("/tmp/homemaster/locomo"),
    memory_data_root: Annotated[
        Path | None,
        typer.Option(
            "--memory-data-root",
            help="Separate MindMemOS data root to avoid an embedded Qdrant lock conflict.",
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the HomeMaster YAML configuration file."),
    ] = None,
    provider_name: Annotated[
        str | None,
        typer.Option("--provider-name", help="Optional configured chat provider name."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Optional chat model override."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Stable output run id; directory must not exist."),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
) -> None:
    """Replay LoCoMo through HomeMaster memory, feedback, and dreaming."""
    try:
        summary = handle_benchmark_locomo(
            data_file=data_file,
            sample_id=sample_id,
            focal_speaker=focal_speaker,
            max_source_turns=max_source_turns,
            qa_probes=qa_probes,
            run_deadline_seconds=run_deadline_seconds,
            trace_root=trace_root,
            memory_data_root=memory_data_root,
            config_path=config_path,
            provider_name=provider_name,
            model_override=model,
            run_id=run_id,
            log_level=log_level,
        )
        typer.echo(f"run_id: {summary['run_id']}")
        typer.echo(f"status: {summary['status']}")
        typer.echo(f"sample_id: {summary['sample_id']}")
        typer.echo(f"source_turns: {summary['source_turn_count']}")
        typer.echo(f"source_sessions: {summary['source_session_count']}")
        typer.echo(f"qa_probes: {summary['qa_probe_count']}")
        typer.echo("feature_counts: " + json.dumps(summary["feature_counts"], ensure_ascii=False))
        summary_path = trace_root.expanduser().resolve() / str(summary["run_id"]) / "summary.json"
        typer.echo(f"summary: {summary_path}")
    except Exception as exc:
        render_error_and_exit(exc)


@app.command("benchmark-alfworld-taskset")
def benchmark_alfworld_taskset_command(
    taskset_config: Annotated[
        Path,
        typer.Option(
            "--taskset-config",
            help="Path to alfworld_tasksets.yaml (long-horizon task chain definition).",
        ),
    ],
    alfworld_root: Annotated[
        Path,
        typer.Option("--alfworld-root", help="Path to the local ALFWorld repository."),
    ],
    alfworld_config: Annotated[
        Path,
        typer.Option("--alfworld-config", help="Path to ALFWorld YAML config (eval_config.yaml)."),
    ],
    log_level: Annotated[
        str,
        typer.Option("--log-level", help="Logging level."),
    ] = "INFO",
) -> None:
    """Run HomeMaster on long-horizon ALFWorld tasksets (one persistent scene per taskset)."""
    try:
        from homemaster.cli.benchmark_alfworld import handle_benchmark_alfworld_taskset

        summary = handle_benchmark_alfworld_taskset(
            taskset_config=taskset_config,
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            log_level=log_level,
        )
        metrics = summary.to_dict()
        typer.echo(f"run_id: {summary.run_id}")
        typer.echo(f"tasksets: {len(summary.taskset_results)}")
        typer.echo(f"agent_scored_tasksets: {metrics['agent_scored_tasksets']}")
        typer.echo(
            f"agent_success_rate_on_valid: {float(metrics['agent_success_rate_on_valid']):.3f}"
        )
        typer.echo(f"harness_invalid_tasksets: {metrics['harness_invalid_tasksets']}")
        typer.echo(f"harness_valid_coverage: {float(metrics['harness_valid_coverage']):.3f}")
        typer.echo(
            f"formal_score_available: {str(bool(metrics['formal_score_available'])).lower()}"
        )
        typer.echo(f"not_run_subtasks: {metrics['not_run_subtasks']}")
        for ts in summary.taskset_results:
            typer.echo(
                f"  [{ts.difficulty}] {ts.taskset_id} (FloorPlan{ts.floorplan}): "
                f"chain_success={ts.chain_success} "
                f"subtask_success_rate={ts.success_rate:.3f} "
                f"chain_completed={ts.chain_completed_count}/{len(ts.subtasks)}"
            )
    except Exception as exc:
        render_error_and_exit(exc)


if __name__ == "__main__":
    app()
