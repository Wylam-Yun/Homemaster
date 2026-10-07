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
        typer.Option("--config", help="Path to the HomeMaster YAML configuration file."),
    ] = None,
) -> None:
    """Migrate legacy memory components into memory.data_root."""

    config = load_config(config_path)
    memory_mode = config.memory.mode
    # Humans should see which tier this run operates on before any JSON
    # receipt — under files mode the MindMemOS backend is intentionally
    # absent, so migration only covers file/evidence components.
    tier_note = (
        "full tier: MindMemOS backend is composed"
        if memory_mode == "full"
        else "files tier: local file memory only; MindMemOS backend is not composed"
    )
    typer.echo(f"memory.mode={memory_mode} ({tier_note})", err=True)
    try:
        manifest = MemoryMigrationCoordinator(config.memory).ensure_ready(auto_migrate=True)
    except MemoryMigrationError as exc:
        typer.echo(
            json.dumps(
                {
                    "status": "FAIL",
                    "memory_mode": memory_mode,
                    "code": exc.code,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            err=True,
        )
        raise typer.Exit(code=1) from exc
    typer.echo(
        json.dumps(
            {"status": "PASS", "memory_mode": memory_mode, "manifest": manifest},
            sort_keys=True,
        )
    )


__all__ = ["memory_app"]
