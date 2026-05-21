"""Tests for interactive shell — V1.4 generic agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeTurn:
    run_id: str = "r1"
    status: str = "replied"
    final_reply: str = "你好，我在。"
    trace_path: Path | None = None
    run_dir: Path | None = None
    tool_events: list = field(default_factory=list)


def test_shell_exits_without_running_task() -> None:
    result = CliRunner().invoke(app, ["shell"], input="/exit\n")
    assert result.exit_code == 0
    assert "HomeMaster V1.4" in result.stdout
    assert "再见" in result.stdout


def test_shell_greeting_returns_reply_without_task_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_agent_turn",
        lambda session, text, **kwargs: FakeTurn(tool_events=[]),
    )
    result = CliRunner().invoke(app, ["shell"], input="你好\n/exit\n")
    assert result.exit_code == 0
    assert "你好，我在。" in result.stdout
    assert "final_status" not in result.stdout
    assert "scenario" not in result.stdout
    assert "stage" not in result.stdout.lower()


def test_shell_doctor_command_prints_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(
            has_failures=False,
            config_source="test",
            live=False,
            checks=[SimpleNamespace(name="test", status="PASS", message="ok", suggestion=None)],
        ),
    )
    result = CliRunner().invoke(app, ["shell"], input="/doctor\n/exit\n")
    assert result.exit_code == 0


def test_shell_status_reports_last_turn_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_agent_turn",
        lambda session, text, **kwargs: FakeTurn(status="replied", tool_events=[]),
    )
    result = CliRunner().invoke(app, ["shell"], input="你好\n/status\n/exit\n")
    assert result.exit_code == 0
    assert "status: replied" in result.stdout


def test_shell_new_resets_session(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    result = CliRunner().invoke(app, ["shell"], input="/new\n/exit\n")
    assert result.exit_code == 0
    assert "新会话已创建" in result.stdout


def test_shell_no_guess_scenario(monkeypatch) -> None:
    """Shell must not use _guess_scenario or fixed run ids."""
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_agent_turn",
        lambda session, text, **kwargs: FakeTurn(tool_events=[]),
    )
    result = CliRunner().invoke(app, ["shell"], input="去厨房找水杯\n/exit\n")
    assert result.exit_code == 0
    assert "scenario" not in result.stdout
    assert "fetch_cup_retry" not in result.stdout
    assert "interactive-" not in result.stdout
