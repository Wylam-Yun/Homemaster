"""HomeMaster text-file and search tools.

File mutation writes atomically and verifies bytes from a
fresh file descriptor while the per-path resource lease is still held.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from homemaster.tools.bash import BashExecutor
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

_IMPLEMENTATION_REFERENCE = "homemaster.tools.file_tools"


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
        if (
            not isinstance(path_value, str)
            or not isinstance(expected_bytes, int)
            or not isinstance(expected_sha256, str)
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


class SearchFilesExecutor:
    async def execute(
        self,
        arguments: Mapping[str, object],
        context: ToolExecutionContext,
    ) -> ToolExecutionResult:
        pattern = _string(arguments, "pattern")
        root = resolve_context_tool_path(context, _optional_string(arguments, "path") or ".")
        target = _string(arguments, "target", default="content")
        file_glob = _optional_string(arguments, "file_glob")
        case_sensitive = _boolean(arguments, "case_sensitive", default=True)
        include_hidden = _boolean(arguments, "include_hidden", default=True)
        respect_gitignore = _boolean(arguments, "respect_gitignore", default=False)
        limit = _integer(arguments, "limit", default=200)
        timeout_seconds = _integer(arguments, "timeout_seconds", default=60)
        if not root.exists():
            return _failure("search_root_missing", f"Search root does not exist: {root}")
        engine_path = _select_search_engine(target)
        if engine_path is None:
            required = "rg or grep" if target == "content" else "rg or find"
            return _failure(
                "search_program_unavailable",
                f"search_files requires {required} in the execution environment",
            )
        engine = Path(engine_path).name
        if respect_gitignore and engine != "rg":
            return _failure(
                "search_semantics_unsupported",
                f"{engine} cannot preserve respect_gitignore=true; use terminal or install rg",
            )
        command = _build_search_command(
            engine_path=engine_path,
            target=target,
            pattern=pattern,
            file_glob=file_glob,
            case_sensitive=case_sensitive,
            include_hidden=include_hidden,
            respect_gitignore=respect_gitignore,
            search_path=root.name if root.is_file() else ".",
        )
        cwd = root.parent if root.is_file() else root
        result = await BashExecutor().execute(
            {
                "command": command,
                "cwd": str(cwd),
                "timeout_seconds": timeout_seconds,
            },
            context,
        )
        returncode = result.data.get("returncode")
        if result.status is not ToolExecutionStatus.SUCCESS and returncode != 1:
            return ToolExecutionResult(
                status=result.status,
                text=result.text,
                data={**result.data, "engine": engine, "target": target, "path": str(root)},
                error=result.error,
                retryable=result.retryable,
                backend_attempted=result.backend_attempted,
            )
        raw_lines = [] if result.text == "(no output)" else result.text.splitlines()
        output_was_truncated = "...[truncated]..." in raw_lines
        raw_lines = [line for line in raw_lines if line != "...[truncated]..."]
        normalized = [_normalize_search_line(line) for line in raw_lines]
        matches = normalized[:limit]
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text="\n".join(matches) if matches else "(no matches)",
            data={
                "matches": matches,
                "path": str(root),
                "target": target,
                "engine": engine,
                "returncode": returncode,
                "timed_out": False,
                "truncated": output_was_truncated or len(normalized) > limit,
            },
            backend_attempted=True,
        )


def _select_search_engine(target: str) -> str | None:
    candidates = ("rg", "grep") if target == "content" else ("rg", "find")
    for candidate in candidates:
        path = shutil.which(candidate)
        if path is not None:
            return path
    return None


def _build_search_command(
    *,
    engine_path: str,
    target: str,
    pattern: str,
    file_glob: str | None,
    case_sensitive: bool,
    include_hidden: bool,
    respect_gitignore: bool,
    search_path: str,
) -> str:
    engine = Path(engine_path).name
    executable = shlex.quote(engine_path)
    quoted_pattern = shlex.quote(pattern)
    quoted_path = shlex.quote(search_path)
    if target == "content" and engine == "rg":
        parts = [
            executable,
            "--line-number",
            "--no-heading",
            "--with-filename",
            "--color",
            "never",
        ]
        if not case_sensitive:
            parts.append("--ignore-case")
        if include_hidden:
            parts.append("--hidden")
        if not respect_gitignore:
            parts.append("--no-ignore")
        if file_glob is not None:
            parts.extend(("--glob", shlex.quote(file_glob)))
        parts.extend(("--", quoted_pattern, quoted_path))
        return " ".join(parts)
    if target == "content":
        flags = "-RInH" if case_sensitive else "-RInHi"
        parts = [executable, flags, "-I"]
        if not include_hidden:
            parts.append("--exclude-dir=.*")
        if file_glob is not None:
            parts.append(f"--include={shlex.quote(file_glob)}")
        parts.extend(("--", quoted_pattern, quoted_path))
        return " ".join(parts)
    if engine == "rg":
        parts = [executable, "--files"]
        if include_hidden:
            parts.append("--hidden")
        if not respect_gitignore:
            parts.append("--no-ignore")
        parts.extend(("--glob", quoted_pattern, quoted_path))
        return " ".join(parts)
    parts = [executable, quoted_path]
    if not include_hidden:
        parts.extend(("-not", "-path", shlex.quote("*/.*")))
    parts.extend(("-type", "f", "-name", quoted_pattern))
    return " ".join(parts)


def _normalize_search_line(line: str) -> str:
    return line[2:] if line.startswith("./") else line


def build_file_tools() -> tuple[RegisteredTool, ...]:
    """Create the HomeMaster file and search registrations."""

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
        RegisteredTool(_search_files_definition(), SearchFilesExecutor()),
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


def _search_files_definition() -> ToolDefinition:
    return _definition(
        "search_files",
        (
            "Search file contents or find files by name. Use this instead of writing "
            "grep/rg/find/ls "
            "in terminal for ordinary searches. Content searches use a regular expression; file "
            "searches use a glob pattern. HomeMaster prefers rg and falls back to grep/find in the "
            "execution environment, records the actual engine, and applies a real timeout. Use "
            "terminal when you need to choose the exact program, command, or pipeline."
        ),
        {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex for content search or glob for file-name search",
                },
                "path": {"type": ["string", "null"], "default": None},
                "target": {
                    "type": "string",
                    "enum": ["content", "files"],
                    "default": "content",
                },
                "file_glob": {"type": ["string", "null"], "default": None},
                "case_sensitive": {"type": "boolean", "default": True},
                "include_hidden": {"type": "boolean", "default": True},
                "respect_gitignore": {"type": "boolean", "default": False},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 200},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 600,
                    "default": 60,
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        required_capabilities=("filesystem.read", "process.exec"),
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
        internal_id=f"homemaster.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=input_schema,
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(
            execution_proof=ExecutionProof.EXTERNAL_STATE if mutating else ExecutionProof.NONE
        ),
        provenance=ToolProvenance(
            source="homemaster",
            reference=f"{_IMPLEMENTATION_REFERENCE}:{name}",
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
