"""Adapters for OpenHarness core tools without application service dependencies."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
import hashlib
import json
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


class BriefExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        del context
        text = _string(arguments, "text").strip()
        maximum = _integer(arguments, "max_chars", default=200)
        output = text if len(text) <= maximum else text[:maximum].rstrip() + "..."
        return _success(output, {"text": output})


class SleepExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        del context
        seconds = _number(arguments, "seconds", default=1.0)
        await asyncio.sleep(seconds)
        return _success(f"Slept for {seconds} seconds", {"seconds": seconds})


class ToolSearchExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        query = _string(arguments, "query").lower()
        matches = [
            manifest
            for manifest in context.tool_view.manifests()
            if query in str(manifest["name"]).lower()
            or query in str(manifest["description"]).lower()
        ]
        if not matches:
            return _success("(no matches)", {"matches": []})
        lines = [f"{item['name']}: {item['description']}" for item in matches]
        return _success("\n".join(lines), {"matches": matches})


class TodoWriteExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        item = _string(arguments, "item")
        checked = _boolean(arguments, "checked", default=False)
        path = resolve_context_tool_path(context, _string(arguments, "path", default="TODO.md"))
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else "# TODO\n"
            unchecked = f"- [ ] {item}"
            completed = f"- [x] {item}"
            target = completed if checked else unchecked
            if unchecked in existing and checked:
                updated = existing.replace(unchecked, completed, 1)
            elif target in existing:
                updated = existing
            else:
                updated = existing.rstrip() + f"\n{target}\n"
            raw = updated.encode("utf-8")
            _atomic_write(path, raw)
        except (OSError, UnicodeDecodeError) as exc:
            return _failure(
                "todo_write_failed", f"Unable to update TODO file: {exc}", attempted=True
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"Updated {path}",
            data={"path": str(path), "sha256": _sha(raw), "byte_count": len(raw)},
            evidence_refs=(f"filesystem/todo/{_sha(raw)}",),
            backend_attempted=True,
        )


class NotebookEditExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        path = resolve_context_tool_path(context, _string(arguments, "path"))
        index = _integer(arguments, "cell_index", default=0)
        source = _string(arguments, "new_source")
        cell_type = _string(arguments, "cell_type", default="code")
        mode = _string(arguments, "mode", default="replace")
        create_if_missing = _boolean(arguments, "create_if_missing", default=True)
        try:
            notebook = _load_notebook(path, create_if_missing=create_if_missing)
            if notebook is None:
                return _failure(
                    "notebook_not_found", f"Notebook not found: {path}", attempted=False
                )
            cells = notebook.setdefault("cells", [])
            if not isinstance(cells, list):
                return _failure(
                    "invalid_notebook", "notebook cells must be an array", attempted=False
                )
            while len(cells) <= index:
                cells.append(_empty_cell(cell_type))
            cell = cells[index]
            if not isinstance(cell, dict):
                return _failure(
                    "invalid_notebook", "target notebook cell is not an object", attempted=False
                )
            cell["cell_type"] = cell_type
            cell.setdefault("metadata", {})
            if cell_type == "code":
                cell.setdefault("outputs", [])
                cell.setdefault("execution_count", None)
            old = _normalize_source(cell.get("source", ""))
            cell["source"] = source if mode == "replace" else f"{old}{source}"
            raw = (json.dumps(notebook, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            _atomic_write(path, raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _failure(
                "notebook_write_failed", f"Unable to update notebook: {exc}", attempted=True
            )
        cell_source = _normalize_source(cell["source"])
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"Updated notebook cell {index} in {path}",
            data={
                "path": str(path),
                "cell_index": index,
                "cell_type": cell_type,
                "source_sha256": _sha(cell_source.encode("utf-8")),
                "file_sha256": _sha(raw),
            },
            evidence_refs=(f"filesystem/notebook/{_sha(raw)}",),
            backend_attempted=True,
        )


class NotebookVerifier:
    async def verify(
        self, result: ToolExecutionResult, context: ToolExecutionContext
    ) -> VerificationRecord:
        try:
            path = resolve_context_tool_path(context, _string(result.data, "path"))
            index = _integer(result.data, "cell_index", default=0)
            expected_type = _string(result.data, "cell_type")
            expected_sha = _string(result.data, "source_sha256")
            notebook = json.loads(path.read_text(encoding="utf-8"))
            cell = notebook["cells"][index]
            observed_source = _normalize_source(cell["source"])
        except (OSError, KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail=f"notebook readback failed: {exc}",
                evidence_refs=result.evidence_refs,
            )
        if (
            cell.get("cell_type") != expected_type
            or _sha(observed_source.encode("utf-8")) != expected_sha
        ):
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="notebook cell did not match receipt",
                evidence_refs=result.evidence_refs,
            )
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="independent notebook JSON readback matched",
            evidence_refs=result.evidence_refs,
        )


class EnterWorktreeExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        branch = _string(arguments, "branch")
        path_value = _optional_string(arguments, "path")
        create_branch = _boolean(arguments, "create_branch", default=True)
        base_ref = _string(arguments, "base_ref", default="HEAD")
        root, error = await _git_output(context.working_directory, "rev-parse", "--show-toplevel")
        if error is not None:
            return _failure(
                "not_git_repository", "enter_worktree requires a git repository", attempted=True
            )
        assert root is not None
        repo_root = Path(root)
        worktree = _worktree_path(context, repo_root, branch, path_value)
        command = ["git", "worktree", "add"]
        if create_branch:
            command.extend(["-b", branch, str(worktree), base_ref])
        else:
            command.extend([str(worktree), branch])
        returncode, output = await _run_git(command, repo_root)
        if returncode != 0:
            return _failure(
                "worktree_create_failed", output or "git worktree add failed", attempted=True
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=f"{output or 'Created worktree'}\nPath: {worktree}",
            data={"path": str(worktree), "operation": "entered"},
            evidence_refs=(f"process/worktree/{_sha(str(worktree).encode('utf-8'))}",),
            backend_attempted=True,
        )


class ExitWorktreeExecutor:
    async def execute(
        self, arguments: Mapping[str, object], context: ToolExecutionContext
    ) -> ToolExecutionResult:
        path = resolve_context_tool_path(context, _string(arguments, "path"))
        returncode, output = await _run_git(
            ["git", "worktree", "remove", "--force", str(path)], context.working_directory
        )
        if returncode != 0:
            return _failure(
                "worktree_remove_failed", output or "git worktree remove failed", attempted=True
            )
        return ToolExecutionResult(
            status=ToolExecutionStatus.SUCCESS,
            text=output or f"Removed worktree {path}",
            data={"path": str(path), "operation": "exited"},
            evidence_refs=(f"process/worktree/{_sha(str(path).encode('utf-8'))}",),
            backend_attempted=True,
        )


class WorktreeVerifier:
    async def verify(
        self, result: ToolExecutionResult, context: ToolExecutionContext
    ) -> VerificationRecord:
        path = Path(_string(result.data, "path"))
        operation = _string(result.data, "operation")
        returncode, output = await _run_git(
            ["git", "worktree", "list", "--porcelain"], context.working_directory
        )
        if returncode != 0:
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="git worktree list failed",
                evidence_refs=result.evidence_refs,
            )
        known_paths = {
            line.removeprefix("worktree ")
            for line in output.splitlines()
            if line.startswith("worktree ")
        }
        exists = str(path) in known_paths
        if operation == "entered" and path.is_dir() and exists:
            return VerificationRecord(
                status=VerificationStatus.PASSED,
                detail="git listed the created worktree",
                evidence_refs=result.evidence_refs,
            )
        if operation == "exited" and not path.exists() and not exists:
            return VerificationRecord(
                status=VerificationStatus.PASSED,
                detail="git no longer listed the worktree",
                evidence_refs=result.evidence_refs,
            )
        return VerificationRecord(
            status=VerificationStatus.FAILED,
            detail="worktree terminal state did not match receipt",
            evidence_refs=result.evidence_refs,
        )


async def _run_git(command: list[str], cwd: Path) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    return process.returncode, output.decode("utf-8", errors="replace").strip()


async def _git_output(cwd: Path, *arguments: str) -> tuple[str | None, str | None]:
    returncode, output = await _run_git(["git", *arguments], cwd)
    return (output, None) if returncode == 0 else (None, output)


def _worktree_path(
    context: ToolExecutionContext, repo_root: Path, branch: str, path: str | None
) -> Path:
    if path is not None:
        return resolve_context_tool_path(context, path)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-") or "worktree"
    return repo_root / ".homemaster" / "worktrees" / slug


def _load_notebook(path: Path, *, create_if_missing: bool) -> dict[str, object] | None:
    if path.exists():
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("notebook root must be an object")
        return value
    if not create_if_missing:
        return None
    return {
        "cells": [],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _empty_cell(cell_type: str) -> dict[str, object]:
    if cell_type == "markdown":
        return {"cell_type": "markdown", "metadata": {}, "source": ""}
    return {
        "cell_type": "code",
        "metadata": {},
        "source": "",
        "outputs": [],
        "execution_count": None,
    }


def _normalize_source(value: object) -> str:
    return "".join(value) if isinstance(value, list) else str(value)


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _success(text: str, data: Mapping[str, object]) -> ToolExecutionResult:
    return ToolExecutionResult(status=ToolExecutionStatus.SUCCESS, text=text, data=data)


def _failure(code: str, message: str, *, attempted: bool) -> ToolExecutionResult:
    return ToolExecutionResult(
        status=ToolExecutionStatus.FAILURE,
        error=ToolExecutionError(code, message),
        backend_attempted=attempted,
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


def _number(arguments: Mapping[str, object], name: str, *, default: float) -> float:
    value = arguments.get(name, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    return float(value)


def _boolean(arguments: Mapping[str, object], name: str, *, default: bool) -> bool:
    value = arguments.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _definition(
    name: str,
    description: str,
    schema: Mapping[str, object],
    *,
    mutating: bool = False,
    capabilities: tuple[str, ...] = (),
    concurrency: ConcurrencyPolicy = ConcurrencyPolicy.PARALLEL,
) -> ToolDefinition:
    return ToolDefinition(
        internal_id=f"openharness.{name}.v1",
        model_alias=name,
        description=description,
        input_schema=schema,
        output_schema={"type": "object"},
        verification_policy=VerificationPolicy(
            execution_proof=ExecutionProof.EXTERNAL_STATE if mutating else ExecutionProof.NONE
        ),
        provenance=ToolProvenance(
            source="openharness", reference=f"{_UPSTREAM_REFERENCE}/{name}_tool.py"
        ),
        version="2.0.0",
        concurrency_policy=concurrency,
        resource_key="filesystem:placeholder"
        if concurrency is ConcurrencyPolicy.RESOURCE_KEY
        else None,
        state_effects=("filesystem.write",)
        if mutating and "filesystem.write" in capabilities
        else (("process.exec",) if mutating else ()),
        required_capabilities=capabilities,
    )


def _todo_resource_key(arguments: Mapping[str, object], context: ToolExecutionContext) -> str:
    return path_resource_key({"path": _string(arguments, "path", default="TODO.md")}, context)


def build_core_tools() -> tuple[RegisteredTool, ...]:
    return (
        RegisteredTool(
            _definition(
                "brief",
                "Shorten a piece of text for compact display.",
                {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to shorten"},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 20,
                            "maximum": 2000,
                            "default": 200,
                        },
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            ),
            BriefExecutor(),
        ),
        RegisteredTool(
            _definition(
                "sleep",
                "Sleep for a short duration.",
                {
                    "type": "object",
                    "properties": {
                        "seconds": {"type": "number", "minimum": 0, "maximum": 30, "default": 1.0}
                    },
                    "additionalProperties": False,
                },
            ),
            SleepExecutor(),
        ),
        RegisteredTool(
            _definition(
                "tool_search",
                "Search the available tool list by name or description.",
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Substring to search in tool names and descriptions",
                        }
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            ToolSearchExecutor(),
        ),
        RegisteredTool(
            _definition(
                "todo_write",
                "Add a new TODO item or mark an existing one as done in a markdown checklist file.",
                {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string", "description": "TODO item text"},
                        "checked": {"type": "boolean", "default": False},
                        "path": {"type": "string", "default": "TODO.md"},
                    },
                    "required": ["item"],
                    "additionalProperties": False,
                },
                mutating=True,
                capabilities=("filesystem.write",),
                concurrency=ConcurrencyPolicy.RESOURCE_KEY,
            ),
            TodoWriteExecutor(),
            FileHashVerifier(),
            resource_key_resolver=_todo_resource_key,
        ),
        RegisteredTool(
            _definition(
                "notebook_edit",
                "Create or edit a Jupyter notebook cell.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to the .ipynb file"},
                        "cell_index": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Zero-based cell index",
                        },
                        "new_source": {
                            "type": "string",
                            "description": "Replacement or appended source for the target cell",
                        },
                        "cell_type": {
                            "type": "string",
                            "enum": ["code", "markdown"],
                            "default": "code",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["replace", "append"],
                            "default": "replace",
                        },
                        "create_if_missing": {"type": "boolean", "default": True},
                    },
                    "required": ["path", "cell_index", "new_source"],
                    "additionalProperties": False,
                },
                mutating=True,
                capabilities=("filesystem.write",),
                concurrency=ConcurrencyPolicy.RESOURCE_KEY,
            ),
            NotebookEditExecutor(),
            NotebookVerifier(),
            resource_key_resolver=path_resource_key,
        ),
        RegisteredTool(
            _definition(
                "enter_worktree",
                "Create a git worktree and return its path.",
                {
                    "type": "object",
                    "properties": {
                        "branch": {
                            "type": "string",
                            "description": "Target branch name for the worktree",
                        },
                        "path": {
                            "type": ["string", "null"],
                            "description": "Optional worktree path",
                        },
                        "create_branch": {"type": "boolean", "default": True},
                        "base_ref": {
                            "type": "string",
                            "default": "HEAD",
                            "description": "Base ref when creating a new branch",
                        },
                    },
                    "required": ["branch"],
                    "additionalProperties": False,
                },
                mutating=True,
                capabilities=("process.exec",),
                concurrency=ConcurrencyPolicy.SERIALIZED,
            ),
            EnterWorktreeExecutor(),
            WorktreeVerifier(),
        ),
        RegisteredTool(
            _definition(
                "exit_worktree",
                "Remove a git worktree by path.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Worktree path to remove"}
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                mutating=True,
                capabilities=("process.exec",),
                concurrency=ConcurrencyPolicy.SERIALIZED,
            ),
            ExitWorktreeExecutor(),
            WorktreeVerifier(),
        ),
    )


class FileHashVerifier:
    async def verify(
        self, result: ToolExecutionResult, context: ToolExecutionContext
    ) -> VerificationRecord:
        try:
            path = resolve_context_tool_path(context, _string(result.data, "path"))
            observed = path.read_bytes()
        except (OSError, ValueError) as exc:
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail=f"TODO readback failed: {exc}",
                evidence_refs=result.evidence_refs,
            )
        if len(observed) != _integer(result.data, "byte_count", default=0) or _sha(
            observed
        ) != _string(result.data, "sha256"):
            return VerificationRecord(
                status=VerificationStatus.FAILED,
                detail="TODO readback did not match write receipt",
                evidence_refs=result.evidence_refs,
            )
        return VerificationRecord(
            status=VerificationStatus.PASSED,
            detail="independent TODO readback matched",
            evidence_refs=result.evidence_refs,
        )


__all__ = ["build_core_tools"]
