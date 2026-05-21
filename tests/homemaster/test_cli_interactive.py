from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.cli import app


def test_cli_help_lists_stage_07_commands_and_existing_developer_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster" in result.stdout
    assert "doctor" in result.stdout
    assert "run" in result.stdout
    assert "contract-smoke" in result.stdout
    assert "understand" in result.stdout


def test_interactive_shell_exits_without_running_task() -> None:
    result = CliRunner().invoke(app, ["shell"], input="/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster V1.3" in result.stdout
    assert "再见" in result.stdout


def test_interactive_shell_doctor_command_prints_summary() -> None:
    result = CliRunner().invoke(app, ["shell"], input="/doctor\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "Doctor" in result.stdout
    assert "PASS" in result.stdout or "WARN" in result.stdout or "FAIL" in result.stdout


def test_interactive_shell_uses_agent_runtime_progress_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_homemaster_task",
        lambda **kwargs: SimpleNamespace(
            final_status="success",
            case_dir=Path("/tmp/homemaster-case"),
            runtime_memory_root=Path("/tmp/homemaster-runtime-memory"),
            memory_commit=False,
        ),
    )

    result = CliRunner().invoke(app, ["shell"], input="你好\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster V1.3" in result.stdout
    assert "AgentRuntime tool loop running..." in result.stdout
    assert "Stage02 -> Stage06 running" not in result.stdout
