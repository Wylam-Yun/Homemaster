"""Canonical path handling shared by tool permission, execution, and verification."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from homemaster.tools.contracts import ToolExecutionContext


class ToolPathError(ValueError):
    """A tool path could not be resolved into a canonical local path."""


def resolve_working_directory(value: str | Path) -> Path:
    """Return the existing canonical directory locked for one application."""

    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ToolPathError(f"working directory is unavailable: {value}") from exc
    if not path.is_dir():
        raise ToolPathError(f"working directory is not a directory: {path}")
    return path


def resolve_tool_path(working_directory: Path, raw_path: str) -> Path:
    """Resolve a user path against the immutable application directory.

    Absolute paths remain absolute so the permission policy can make the
    deployment's protected-path and deny-rule decision. Relative paths are
    anchored to the directory captured during application composition, never
    the process current directory at execution time.
    """

    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        raise ToolPathError("tool path must be a non-empty string without NUL bytes")
    base = resolve_working_directory(working_directory)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ToolPathError(f"tool path cannot be resolved: {raw_path}") from exc


def resolve_context_tool_path(context: ToolExecutionContext, raw_path: str) -> Path:
    """Resolve a tool path from the immutable execution context."""

    return resolve_tool_path(context.working_directory, raw_path)


def path_resource_key(
    arguments: Mapping[str, object],
    context: ToolExecutionContext,
    *,
    argument_name: str = "path",
) -> str:
    """Return the application-wide transaction key for one canonical path."""

    raw_path = arguments.get(argument_name)
    if not isinstance(raw_path, str):
        raise ToolPathError(f"{argument_name} must be a path string")
    return f"filesystem:{resolve_context_tool_path(context, raw_path).as_posix()}"


__all__ = [
    "ToolPathError",
    "path_resource_key",
    "resolve_context_tool_path",
    "resolve_tool_path",
    "resolve_working_directory",
]
