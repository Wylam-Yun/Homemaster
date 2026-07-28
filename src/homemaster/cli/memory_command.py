"""Explicit memory data migration command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from homemaster.config import load_config
from homemaster.memory.migration import MemoryMigrationCoordinator, MemoryMigrationError

memory_app = typer.Typer(add_completion=False, help="Manage persistent HomeMaster memory data.")


@memory_app.command("migrate")
def migrate_memory(
    config_path: Annotated[
        Path | None,
        typer.Option("--config", help="Path to the ignored HomeMaster YAML configuration."),
    ] = None,
) -> None:
    """Migrate legacy memory components into memory.data_root."""

    config = load_config(config_path)
    try:
        manifest = MemoryMigrationCoordinator(config.memory).ensure_ready(auto_migrate=True)
    except MemoryMigrationError as exc:
        typer.echo(
            json.dumps({"status": "FAIL", "code": exc.code, "message": str(exc)}, sort_keys=True),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"status": "PASS", "manifest": manifest}, sort_keys=True))


__all__ = ["memory_app"]
