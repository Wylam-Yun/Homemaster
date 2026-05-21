"""Tests for CLI run command — V1.4 generic agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeTurn:
    run_id: str = "r1"
    status: str = "replied"
    final_reply: str = "已完成。"
    trace_path: Path | None = None
    run_dir: Path | None = None
    tool_events: list = field(default_factory=list)


def test_run_command_prints_assistant_reply_and_trace(monkeypatch, tmp_path: Path) -> None:
    trace = tmp_path / "runs" / "r1" / "events.jsonl"
    monkeypatch.setattr(
        "homemaster.cli.run_command.run_single_turn",
        lambda **kwargs: FakeTurn("r1", "replied", "已完成。", trace, tmp_path / "runs" / "r1", []),
    )
    result = CliRunner().invoke(app, ["run", "--utterance", "帮我拿个水"])
    assert result.exit_code == 0
    assert "assistant: 已完成。" in result.stdout
    assert "trace:" in result.stdout
    assert "stage" not in result.stdout.lower()
    assert "scenario" not in result.stdout.lower()


def test_run_command_accepts_progress_flag(monkeypatch, tmp_path: Path) -> None:
    """--progress flag is accepted."""
    monkeypatch.setattr(
        "homemaster.cli.run_command.run_single_turn",
        lambda **kwargs: FakeTurn(),
    )
    result = CliRunner().invoke(app, ["run", "--utterance", "test", "--progress"])
    assert result.exit_code == 0


def test_run_command_no_scenario_flag() -> None:
    """--scenario is no longer a valid flag."""
    result = CliRunner().invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "fetch_cup_retry"],
    )
    assert result.exit_code != 0


def test_run_command_status_field(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.run_command.run_single_turn",
        lambda **kwargs: FakeTurn(status="replied"),
    )
    result = CliRunner().invoke(app, ["run", "--utterance", "test"])
    assert result.exit_code == 0
    assert "status: replied" in result.stdout
