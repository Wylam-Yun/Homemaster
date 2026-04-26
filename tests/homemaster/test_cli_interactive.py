from __future__ import annotations

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
    result = CliRunner().invoke(app, [], input="/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster" in result.stdout
    assert "再见" in result.stdout


def test_interactive_shell_doctor_command_prints_summary() -> None:
    result = CliRunner().invoke(app, [], input="/doctor\n/exit\n")

    assert result.exit_code == 0, result.stdout
    assert "Doctor" in result.stdout
    assert "PASS" in result.stdout or "WARN" in result.stdout or "FAIL" in result.stdout
