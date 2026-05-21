"""CLI error classification and rendering."""

from __future__ import annotations

import logging

import typer

from homemaster.embedding_client import EmbeddingClientError
from homemaster.llm_client import LLMClientError
from homemaster.runtime import RuntimeConfigError
from homemaster.task_runner import HomeMasterRunError

logger = logging.getLogger(__name__)


def render_error_and_exit(exc: Exception) -> None:
    """Classify exception, render to stderr, raise typer.Exit with appropriate code.

    typer.BadParameter / usage errors are handled by Typer (exit 2) before
    reaching this function — no explicit handling needed here.
    """
    if isinstance(exc, RuntimeConfigError):
        typer.echo(f"config_failed: {exc}", err=True)
        raise typer.Exit(code=2)
    if isinstance(exc, HomeMasterRunError):
        typer.echo(f"run_failed: {exc}", err=True)
        raise typer.Exit(code=1)
    if isinstance(exc, (LLMClientError, EmbeddingClientError)):
        typer.echo(f"run_failed: {exc}", err=True)
        raise typer.Exit(code=1)
    # Unexpected — log traceback, show generic message
    logger.exception("Unexpected error")
    typer.echo(f"internal_error: {type(exc).__name__}: {exc}", err=True)
    raise typer.Exit(code=1)
