"""Home adapters for the locked OpenHarness text-file and search tools.

The public names and input shapes follow OpenHarness. File mutation differs at
the execution boundary: HomeMaster writes atomically and verifies bytes from a
fresh file descriptor while the per-path resource lease is still held.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from homemaster.tools.contracts import (
    ConcurrencyPolicy,
    ExecutionProof,
    RegisteredTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolExecutionError,
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolProvenance,
    VerificationPolicy,
    VerificationRecord,
    VerificationStatus,
)
from homemaster.tools.paths import path_resource_key, resolve_context_tool_path

_UPSTREAM_REFERENCE = "OpenHarness@9b2efd7:src/openharness/tools"


class ReadFileExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        path = resolve_context_tool_path(context, _string(arguments, "path"))
        if not path.exists():
            return _failure("file_not_found", f"File not found: {path}")
        if path.is_dir():
            return _failure("cannot_read_directory", f"Cannot read directory: {path}")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            return _failure("file_read_failed", f"Unable to read {path}: {exc}")
        if b"\x00" in raw:
            return _failure("binary_file", f"Binary file cannot be read as text: {path}")
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _failure("invalid_utf8", f"File is not UTF-8 text: {path}")
        offset = _integer(arguments, "offset", default=0)
        limit = _integer(arguments, "limit", default=200)
        lines = text.splitlines()
        selected = lines[offset : offset + limit]
        content = "\n".join(
            f"{offset + index + 1:>6}\t{line}" for index, line in enumerate(selected)
        )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=content or f"(no content in selected range for {path})",
            data={
                "path": str(path),
                "content": content,
                "offset": offset,
                "limit": limit,
                "has_more": offset + len(selected) < len(lines),
            },
        )


class WriteFileExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        path = resolve_context_tool_path(context, _string(arguments, "path"))
        content = _string(arguments, "content")
        create_directories = _boolean(arguments, "create_directories", default=True)
        try:
            _atomic_write(path, content.encode("utf-8"), create_directories=create_directories)
        except OSError as exc:
            return _unknown("file_write_failed", f"Unable to write {path}: {exc}")
        raw = content.encode("utf-8")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"Wrote {path}",
            data=_write_receipt(path, raw),
            evidence_refs=(f"filesystem/write/{hashlib.sha256(raw).hexdigest()}",),
            backend_attempted=True,
        )


class EditFileExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        path = resolve_context_tool_path(context, _string(arguments, "path"))
        old_str = _string(arguments, "old_str")
        new_str = _string(arguments, "new_str")
        replace_all = _boolean(arguments, "replace_all", default=False)
        if not path.exists():
            return _failure("file_not_found", f"File not found: {path}")
        if path.is_dir():
            return _failure("cannot_edit_directory", f"Cannot edit directory: {path}")
        try:
            original = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeDecodeError) as exc:
            return _failure("file_read_failed", f"Unable to read UTF-8 file {path}: {exc}")
        if old_str not in original:
            return _failure("old_str_not_found", "old_str was not found in the file")
        updated = original.replace(old_str, new_str, -1 if replace_all else 1)
        raw = updated.encode("utf-8")
        try:
            _atomic_write(path, raw, create_directories=False)
        except OSError as exc:
            return _unknown("file_edit_failed", f"Unable to update {path}: {exc}")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"Updated {path}",
            data=_write_receipt(path, raw),
            evidence_refs=(f"filesystem/edit/{hashlib.sha256(raw).hexdigest()}",),
            backend_attempted=True,
        )


class FileWriteVerifier:
    async def verify(
        self,
        result: ToolExecutionResult,
        context: ToolExecutionContext,
    ) -> VerificationRecord:
        path_value = result.data.get("path")
        expected_bytes = result.data.get("byte_count")
        expected_sha256 = result.data.get("sha256")
        if not isinstance(path_value, str) or not isinstance(expected_bytes, int) or not isinstance(
            expected_sha256, str
        ):
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="write receipt is incomplete",
                evidence_refs=result.evidence_refs,
            )
        path = resolve_context_tool_path(context, path_value)
        try:
            observed = path.read_bytes()
        except OSError as exc:
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail=f"independent readback failed: {exc}",
                evidence_refs=result.evidence_refs,
            )
        actual_sha256 = hashlib.sha256(observed).hexdigest()
        if len(observed) != expected_bytes or actual_sha256 != expected_sha256:
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="independent readback did not match the write receipt",
                evidence_refs=result.evidence_refs,
            )
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="independent file readback matched byte count and SHA-256",
            evidence_refs=result.evidence_refs,
        )


class GlobExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        pattern = _string(arguments, "pattern")
        root = resolve_context_tool_path(context, _optional_string(arguments, "root") or ".")
        limit = _integer(arguments, "limit", default=200)
        if not root.exists() or not root.is_dir():
            return ToolExecutionResult(
                status=ToolExecutionStatus.SUCCESS,
                text="(no matches)",
                data={"matches": []},
            )
        try:
            matches = sorted(
                str(path.relative_to(root))
                for path in root.glob(pattern)
                if path.exists()
            )[:limit]
        except (OSError, ValueError) as exc:
            return _failure("glob_failed", f"Unable to glob {root}: {exc}")
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="\n".join(matches) if matches else "(no matches)",
            data={"matches": matches, "root": str(root)},
        )


class GrepExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        pattern = _string(arguments, "pattern")
        root = resolve_context_tool_path(context, _optional_string(arguments, "root") or ".")
        file_glob = _string(arguments, "file_glob", default="**/*")
        case_sensitive = _boolean(arguments, "case_sensitive", default=True)
        limit = _integer(arguments, "limit", default=200)
        if not root.exists():
            return _failure("search_root_missing", f"Search root does not exist: {root}")
        try:
            expression = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return _failure("invalid_regex", f"invalid regex pattern {pattern!r}: {exc}")
        paths = [root] if root.is_file() else root.glob(file_glob)
        display_base = root.parent if root.is_file() else root
        matches: list[str] = []
        for path in paths:
            if len(matches) >= limit or not path.is_file():
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw:
                continue
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if expression.search(line):
                    matches.append(f"{path.relative_to(display_base)}:{line_number}:{line}")
                    if len(matches) >= limit:
                        break
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="\n".join(matches) if matches else "(no matches)",
            data={"matches": matches, "root": str(root)},
        )


def build_file_tools() -> tuple[RegisteredTool, ...]:
    """Create the Home registrations for OpenHarness file/search tools."""

    return (
        RegisteredTool(_read_definition(), ReadFileExecutor()),
        RegisteredTool(
            _write_definition(),
            WriteFileExecutor(),
            FileWriteVerifier(),
            resource_key_resolver=path_resource_key,
        ),
        RegisteredTool(
            _edit_definition(),
            EditFileExecutor(),
            FileWriteVerifier(),
            resource_key_resolver=path_resource_key,
        ),
        RegisteredTool(_glob_definition(), GlobExecutor()),
        RegisteredTool(_grep_definition(), GrepExecutor()),
    )


def _read_definition() -> ToolDefinition:
    return _definition(
        "read_file",
        "Read a text file from the local repository.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to read"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        required_capabilities=("filesystem.read",),
    )


def _write_definition() -> ToolDefinition:
    return _definition(
        "write_file",
        "Create or overwrite a text file in the local repository.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to write"},
                "content": {"type": "string", "description": "Full file contents"},
                "create_directories": {"type": "boolean", "default": True},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        mutating=True,
    )


def _edit_definition() -> ToolDefinition:
    return _definition(
        "edit_file",
        "Edit an existing file by replacing a string.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path of the file to edit"},
                "old_str": {"type": "string", "description": "Existing text to replace"},
                "new_str": {"type": "string", "description": "Replacement text"},
                "replace_all": {"type": "boolean", "default": False},
            },
            "required": ["path", "old_str", "new_str"],
            "additionalProperties": False,
        },
        mutating=True,
    )


def _glob_definition() -> ToolDefinition:
    return _definition(
        "glob",
        "List files matching a glob pattern.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern relative to root"},
                "root": {"type": ["string", "null"], "default": None},
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000, "default": 200},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        required_capabilities=("filesystem.read",),
    )


def _grep_definition() -> ToolDefinition:
    return _definition(
        "grep",
        "Search file contents with a regular expression.",
        {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression to search for"},
                "root": {"type": ["string", "null"], "default": None},
                "file_glob": {"type": "string", "default": "**/*"},
                "case_sensitive": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120, "default": 20},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        required_capabilities=("filesystem.read",),
    )


def _definition(
    name: str,
    description: str,
    input_schema: Mapping[str, object],
    *,
    mutating: bool = False,
    required_capabilities: tuple[str, ...] = (),
) -> ToolDefinition:
    return ToolDefinition(
        internal_id=f"openharness.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=input_schema,
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(
            execution_proof=ExecutionProof.EXTERNAL_STATE if mutating else ExecutionProof.NONE
        ),
        provenance=ToolProvenance(
            source="openharness",
            reference=f"{_UPSTREAM_REFERENCE}/{name}_tool.py",
        ),
        version="2.0.0",
        concurrency_policy=(
            ConcurrencyPolicy.RESOURCE_KEY if mutating else ConcurrencyPolicy.PARALLEL
        ),
        resource_key="filesystem:placeholder" if mutating else None,
        state_effects=("filesystem.write",) if mutating else (),
        required_capabilities=("filesystem.write",) if mutating else required_capabilities,
    )


def _atomic_write(path: Path, raw: bytes, *, create_directories: bool) -> None:
    if create_directories:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {path.parent}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def _sync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_DIRECTORY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_receipt(path: Path, raw: bytes) -> dict[str, object]:
    return {
        "path": str(path),
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _failure(code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        error=ToolExecutionError(code, message),
    )


def _unknown(code: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.OUTCOME_UNKNOWN,
        error=ToolExecutionError(code, message),
        backend_attempted=True,
    )


def _string(arguments: Mapping[str, object], name: str, *, default: str | None = None) -> str:
    value = arguments.get(name, default)
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _optional_string(arguments: Mapping[str, object], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    return value


def _integer(arguments: Mapping[str, object], name: str, *, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _boolean(arguments: Mapping[str, object], name: str, *, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


__all__ = ["build_file_tools"]
