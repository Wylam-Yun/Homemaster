"""External process gates for the V2.0 OpenHarness bash adapter."""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path

import pytest

from homemaster.tools.contracts import ToolExecutionStatus
from tests.homemaster.tools.universal_harness import execute, registry


async def _execute(
    tool_registry,
    root: Path,
    arguments: dict[str, object],
    *,
    path_rules: tuple[dict[str, object], ...] = (),
):
    return await execute(
        tool_registry,
        root,
        "bash",
        arguments,
        capabilities=("tool.read", "tool.mutate", "tool.auto", "process.exec"),
        path_rules=path_rules,
        call_id="call-bash",
    )


def test_universal_registry_registers_bash() -> None:
    assert "bash" in registry().all_names()


@pytest.mark.asyncio
async def test_bash_reports_real_success_and_nonzero_return_codes(tmp_path: Path) -> None:
    profile = registry()

    success = await _execute(profile, tmp_path, {"command": "git --version"})
    failure = await _execute(
        profile,
        tmp_path,
        {"command": "printf stdout; printf stderr >&2; exit 7"},
    )

    assert success.status is ToolExecutionStatus.SUCCESS
    assert success.data["returncode"] == 0
    assert "git version" in success.text
    assert failure.status is ToolExecutionStatus.FAILURE
    assert failure.error is not None
    assert failure.error.code == "command_failed"
    assert failure.data["returncode"] == 7
    assert "stdout" in failure.text
    assert "stderr" in failure.text


@pytest.mark.asyncio
async def test_bash_cwd_is_resolved_once_and_permission_denial_prevents_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = registry()
    denied = tmp_path / "denied"
    denied.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    denied_result = await _execute(
        profile,
        tmp_path,
        {"command": "touch command-ran", "cwd": "denied"},
        path_rules=({"pattern": str(denied), "allow": False},),
    )
    resolved_result = await _execute(
        profile,
        tmp_path,
        {"command": "pwd", "cwd": "."},
    )

    assert denied_result.status is ToolExecutionStatus.DENIED
    assert not (tmp_path / "command-ran").exists()
    assert resolved_result.status is ToolExecutionStatus.SUCCESS
    assert resolved_result.data["cwd"] == str(tmp_path)
    assert resolved_result.text == str(tmp_path)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group inspection requires POSIX")
async def test_bash_timeout_kills_the_complete_child_process_group(tmp_path: Path) -> None:
    profile = registry()
    pid_path = tmp_path / "timeout-child.pid"
    command = _child_command(pid_path)

    result = await _execute(
        profile,
        tmp_path,
        {"command": command, "timeout_seconds": 1},
    )

    child_pid = await _wait_for_pid(pid_path)
    assert result.status is ToolExecutionStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "command_timed_out"
    assert result.data["timed_out"] is True
    await _assert_process_gone(child_pid)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group inspection requires POSIX")
async def test_bash_cancellation_kills_the_complete_child_process_group(tmp_path: Path) -> None:
    profile = registry()
    pid_path = tmp_path / "cancelled-child.pid"
    task = asyncio.create_task(
        _execute(profile, tmp_path, {"command": _child_command(pid_path), "timeout_seconds": 60})
    )
    child_pid = await _wait_for_pid(pid_path)

    task.cancel()
    result = await task

    assert result.status is ToolExecutionStatus.OUTCOME_UNKNOWN
    assert result.error is not None
    assert result.error.code == "execution_cancelled"
    assert result.backend_attempted is True
    await _assert_process_gone(child_pid)


def _child_command(pid_path: Path) -> str:
    return (
        "sleep 30 & child=$!; "
        f"printf '%s' \"$child\" > {shlex.quote(str(pid_path))}; "
        "wait"
    )


async def _wait_for_pid(path: Path) -> int:
    for _ in range(100):
        if path.is_file():
            return int(path.read_text(encoding="utf-8"))
        await asyncio.sleep(0.05)
    raise AssertionError(f"child PID was not written to {path}")


async def _assert_process_gone(pid: int) -> None:
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"child process {pid} remained after tool cleanup")
