"""External filesystem gates for the V2.0 OpenHarness file-tool adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.adapters.profiles import build_home_profile
from homemaster.agent.messages import ToolCall
from homemaster.permissions import HomePermissionPolicy, PermissionMode, PermissionSettingsConfig
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext, ToolExecutionStatus
from homemaster.tools.pipeline import ToolExecutionPipeline


def _context(profile, root: Path, *, tool_id: str, call_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        session_id="file-session",
        run_id="file-run",
        turn_index=0,
        tool_call_id=call_id,
        internal_tool_id=tool_id,
        tool_view=profile.view,
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="cli",
            capabilities=(
                "tool.read",
                "tool.mutate",
                "tool.auto",
                "filesystem.read",
                "filesystem.write",
            ),
        ),
        backend=None,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=root,
    )


async def _execute(profile, root: Path, name: str, arguments: dict[str, object]):
    tool = profile.view.lookup(name).tool
    assert tool is not None
    pipeline = ToolExecutionPipeline(
        profile.catalog,
        permission_policy=HomePermissionPolicy(
            PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
        ),
    )
    return await pipeline.execute(
        ToolCall(id=f"call-{name}", name=name, arguments=arguments),
        _context(profile, root, tool_id=tool.definition.internal_id, call_id=f"call-{name}"),
    )


def test_home_profile_registers_the_first_openharness_file_tool_set() -> None:
    names = set(build_home_profile().model_tool_names)

    assert {"read_file", "write_file", "edit_file", "glob", "grep"} <= names


@pytest.mark.asyncio
async def test_file_tools_write_verify_then_read_search_and_edit_real_bytes(tmp_path: Path) -> None:
    profile = build_home_profile()
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
    assert searched.data["matches"] == ("example.txt:2:needle line",)
    assert matched.data["matches"] == ("example.txt",)
    assert edited.status is ToolExecutionStatus.SUCCESS
    assert edited.verification.status.value == "passed"
    assert target.read_text(encoding="utf-8") == "first line\nverified line\n"
