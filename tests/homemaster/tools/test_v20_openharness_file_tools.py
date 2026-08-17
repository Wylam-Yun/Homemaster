"""External filesystem gates for the V2.0 OpenHarness file-tool adapter."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from homemaster.tools.contracts import ToolExecutionStatus
from tests.homemaster.tools.universal_harness import execute, registry


async def _execute(profile, root: Path, name: str, arguments: dict[str, object]):
    return await execute(
        profile,
        root,
        name,
        arguments,
        capabilities=(
            "tool.read",
            "tool.mutate",
            "tool.auto",
            "filesystem.read",
            "filesystem.write",
            "process.exec",
        ),
    )


def test_universal_registry_registers_the_file_tool_set() -> None:
    names = set(registry().all_names())

    assert {"read_file", "write_file", "edit_file", "search_files"} <= names
    assert {"glob", "grep"}.isdisjoint(names)


@pytest.mark.asyncio
async def test_file_tools_write_verify_then_read_search_and_edit_real_bytes(tmp_path: Path) -> None:
    profile = registry()
    expected = "first line\nneedle line\n"

    written = await _execute(
        profile,
        tmp_path,
        "write_file",
        {"path": "files/example.txt", "content": expected, "create_directories": True},
    )

    target = tmp_path / "files" / "example.txt"
    assert written.status is ToolExecutionStatus.SUCCESS
    assert written.verification.status.value == "passed"
    assert target.read_bytes() == expected.encode("utf-8")
    assert not list(target.parent.glob(".example.txt.*"))

    read = await _execute(profile, tmp_path, "read_file", {"path": "files/example.txt"})
    searched = await _execute(
        profile,
        tmp_path,
        "search_files",
        {"pattern": "needle", "path": "files", "file_glob": "*.txt"},
    )
    matched = await _execute(
        profile,
        tmp_path,
        "search_files",
        {"pattern": "*.txt", "path": "files", "target": "files"},
    )
    edited = await _execute(
        profile,
        tmp_path,
        "edit_file",
        {"path": "files/example.txt", "old_str": "needle", "new_str": "verified"},
    )

    assert read.status is ToolExecutionStatus.SUCCESS
    assert "needle line" in read.text
    assert searched.status is ToolExecutionStatus.SUCCESS
    assert searched.data["matches"] == ["example.txt:2:needle line"]
    assert searched.data["engine"] in {"rg", "grep"}
    assert matched.data["matches"] == ["example.txt"]
    assert edited.status is ToolExecutionStatus.SUCCESS
    assert edited.verification.status.value == "passed"
    assert target.read_text(encoding="utf-8") == "first line\nverified line\n"


@pytest.mark.asyncio
async def test_search_files_falls_back_to_grep_and_find(monkeypatch, tmp_path: Path) -> None:
    profile = registry()
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "example.py").write_text("needle\n", encoding="utf-8")

    monkeypatch.setattr(
        "homemaster.tools.file_tools.shutil.which",
        lambda name: "/usr/bin/grep" if name == "grep" else None,
    )
    content = await _execute(
        profile,
        tmp_path,
        "search_files",
        {"pattern": "needle", "path": "."},
    )
    assert content.status is ToolExecutionStatus.SUCCESS
    assert content.data["engine"] == "grep"
    assert "nested/example.py:1:needle" in content.data["matches"]

    monkeypatch.setattr(
        "homemaster.tools.file_tools.shutil.which",
        lambda name: "/usr/bin/find" if name == "find" else None,
    )
    files = await _execute(
        profile,
        tmp_path,
        "search_files",
        {"pattern": "*.py", "path": ".", "target": "files"},
    )
    assert files.status is ToolExecutionStatus.SUCCESS
    assert files.data["engine"] == "find"
    assert files.data["matches"] == ["nested/example.py"]


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group inspection requires POSIX")
async def test_search_files_timeout_kills_child_process_group(monkeypatch, tmp_path: Path) -> None:
    profile = registry()
    pid_path = tmp_path / "search-child.pid"
    fake_rg = tmp_path / "rg"
    fake_rg.write_text(
        f"#!/bin/sh\nsleep 30 & child=$!\nprintf '%s' \"$child\" > {pid_path}\nwait\n",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    monkeypatch.setattr(
        "homemaster.tools.file_tools.shutil.which",
        lambda name: str(fake_rg) if name == "rg" else None,
    )

    result = await _execute(
        profile,
        tmp_path,
        "search_files",
        {"pattern": "needle", "timeout_seconds": 1},
    )

    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "command_timed_out"
    assert result.data["timed_out"] is True
    child_pid = int(pid_path.read_text(encoding="utf-8"))
    for _ in range(100):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"search child process {child_pid} remained after timeout")
