"""Black-box checks for portable V2.0 OpenHarness core tools."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from homemaster.adapters.profiles import build_coworker_profile, build_home_profile
from homemaster.agent.messages import ToolCall
from homemaster.permissions import HomePermissionPolicy, PermissionMode, PermissionSettingsConfig
from homemaster.tools.contracts import PermissionSubject, ToolExecutionContext, ToolExecutionStatus
from homemaster.tools.pipeline import ToolExecutionPipeline


def _context(profile, root: Path, name: str) -> ToolExecutionContext:
    tool = profile.view.lookup(name).tool
    assert tool is not None
    return ToolExecutionContext(
        session_id="core-session",
        run_id="core-run",
        turn_index=0,
        tool_call_id=f"call-{name}",
        internal_tool_id=tool.definition.internal_id,
        tool_view=profile.view,
        permission_subject=PermissionSubject(
            subject_id="operator",
            channel="cli",
            capabilities=(
                "tool.read",
                "tool.mutate",
                "tool.auto",
                "filesystem.write",
                "process.exec",
            ),
        ),
        backend=None,
        deadline=None,
        cancellation=None,
        domain_observer=None,
        working_directory=root,
    )


async def _execute(profile, root: Path, name: str, arguments: dict[str, object]):
    pipeline = ToolExecutionPipeline(
        profile.catalog,
        permission_policy=HomePermissionPolicy(
            PermissionSettingsConfig(mode=PermissionMode.FULL_AUTO)
        ),
    )
    return await pipeline.execute(
        ToolCall(id=f"call-{name}", name=name, arguments=arguments),
        _context(profile, root, name),
    )


@pytest.mark.asyncio
async def test_notebook_and_todo_write_have_independent_filesystem_terminal_states(
    tmp_path: Path,
) -> None:
    profile = build_home_profile()
    notebook = await _execute(
        profile,
        tmp_path,
        "notebook_edit",
        {"path": "analysis.ipynb", "cell_index": 1, "new_source": "print('verified')\n"},
    )
    todo = await _execute(
        profile,
        tmp_path,
        "todo_write",
        {"item": "verify notebook", "checked": False},
    )
    todo_checked = await _execute(
        profile,
        tmp_path,
        "todo_write",
        {"item": "verify notebook", "checked": True},
    )

    parsed = json.loads((tmp_path / "analysis.ipynb").read_text(encoding="utf-8"))
    assert notebook.status is ToolExecutionStatus.SUCCESS
    assert notebook.verification.status.value == "passed"
    assert parsed["cells"][1]["source"] == "print('verified')\n"
    assert todo.status is ToolExecutionStatus.SUCCESS
    assert todo.verification.status.value == "passed"
    assert todo_checked.status is ToolExecutionStatus.SUCCESS
    assert todo_checked.verification.status.value == "passed"
    assert "- [x] verify notebook" in (tmp_path / "TODO.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_worktree_tools_change_real_git_worktree_state(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "V2 Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("root\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    profile = build_home_profile()
    worktree = tmp_path / "feature-tree"

    entered = await _execute(
        profile,
        tmp_path,
        "enter_worktree",
        {"branch": "feature/v20", "path": str(worktree)},
    )
    listed = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert entered.status is ToolExecutionStatus.SUCCESS
    assert entered.verification.status.value == "passed"
    assert f"worktree {worktree}" in listed
    assert worktree.is_dir()
    exited = await _execute(profile, tmp_path, "exit_worktree", {"path": str(worktree)})
    listed_after = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert exited.status is ToolExecutionStatus.SUCCESS
    assert exited.verification.status.value == "passed"
    assert f"worktree {worktree}" not in listed_after
    assert not worktree.exists()


@pytest.mark.asyncio
async def test_tool_search_only_returns_the_active_view_and_pure_tools_run(tmp_path: Path) -> None:
    profile = build_home_profile()
    search = await _execute(profile, tmp_path, "tool_search", {"query": "web"})
    brief = await _execute(profile, tmp_path, "brief", {"text": "a" * 30, "max_chars": 20})
    slept = await _execute(profile, tmp_path, "sleep", {"seconds": 0})
    coworker = build_coworker_profile()

    assert search.status is ToolExecutionStatus.SUCCESS
    assert {match["name"] for match in search.data["matches"]} == {"web_fetch", "web_search"}
    assert brief.data["text"] == "a" * 20 + "..."
    assert slept.data["seconds"] == 0.0
    assert "bash" not in coworker.model_tool_names
