"""External filesystem gates for the V2.0 OpenHarness file-tool adapter."""

from __future__ import annotations

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
        ),
    )


def test_universal_registry_registers_the_file_tool_set() -> None:
    names = set(registry().all_names())

    assert {"read_file", "write_file", "edit_file", "glob", "grep"} <= names


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
        "grep",
        {"pattern": "needle", "root": "files", "file_glob": "*.txt"},
    )
    matched = await _execute(profile, tmp_path, "glob", {"pattern": "*.txt", "root": "files"})
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
    assert matched.data["matches"] == ["example.txt"]
    assert edited.status is ToolExecutionStatus.SUCCESS
    assert edited.verification.status.value == "passed"
    assert target.read_text(encoding="utf-8") == "first line\nverified line\n"
