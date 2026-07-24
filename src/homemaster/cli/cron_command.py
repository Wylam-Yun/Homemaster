"""Manage the detached HomeMaster Cron scheduler."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated

import typer

from homemaster.tools.openharness_runtime import HomeCronStore

cron_app = typer.Typer(add_completion=False, help="Manage the HomeMaster Cron scheduler.")


def _store(state_root: Path) -> HomeCronStore:
    return HomeCronStore(state_root.expanduser().resolve() / "cron")


@cron_app.command("start")
def start(
    state_root: Annotated[
        Path,
        typer.Option("--state-root", help="HomeMaster state root."),
    ] = Path("~/.homemaster"),
) -> None:
    store = _store(state_root)
    existing = store.scheduler_pid()
    if existing is not None:
        typer.echo(f"running pid={existing}")
        return
    store.state_dir.mkdir(parents=True, exist_ok=True)
    log = (store.state_dir / "scheduler.log").open("ab")
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "homemaster.cli.cron_worker",
                "--state-root",
                str(state_root.expanduser().resolve()),
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        log.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pid = store.scheduler_pid()
        if pid == process.pid:
            typer.echo(f"started pid={pid}")
            return
        if process.poll() is not None:
            raise RuntimeError(f"Cron scheduler exited with return code {process.returncode}")
        time.sleep(0.02)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError("Cron scheduler did not publish its PID before the deadline")


@cron_app.command("status")
def status(
    state_root: Annotated[Path, typer.Option("--state-root")] = Path("~/.homemaster"),
) -> None:
    pid = _store(state_root).scheduler_pid()
    if pid is None:
        typer.echo("stopped")
        raise typer.Exit(code=1)
    typer.echo(f"running pid={pid}")


@cron_app.command("stop")
def stop(
    state_root: Annotated[Path, typer.Option("--state-root")] = Path("~/.homemaster"),
) -> None:
    store = _store(state_root)
    pid = store.scheduler_pid()
    if pid is None:
        typer.echo("stopped")
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if store.scheduler_pid() is None:
            typer.echo(f"stopped pid={pid}")
            return
        time.sleep(0.02)
    raise RuntimeError(f"Cron scheduler pid={pid} did not stop before the deadline")


__all__ = ["cron_app"]
