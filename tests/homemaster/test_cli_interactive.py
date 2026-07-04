"""Tests for interactive shell."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.agent.turn import new_session_id
from homemaster.cli.app import app


@dataclass(frozen=True)
class FakeTurn:
    run_id: str = "r1"
    status: str = "replied"
    final_reply: str = "你好，我在。"
    trace_path: Path | None = None
    run_dir: Path | None = None
    tool_events: list = field(default_factory=list)


@dataclass(frozen=True)
class FakeCompact:
    run_id: str = "compact-r1"
    status: str = "compacted"
    message: str = "已压缩上下文：100 -> 50 tokens"
    trace_path: Path | None = None
    run_dir: Path | None = None
    compaction_triggered: bool = True
    compaction_kind: str = "manual_summary"
    before_tokens: int = 100
    after_tokens: int = 50
    agent_state: object | None = None


def test_shell_exits_without_running_task() -> None:
    result = CliRunner().invoke(app, ["shell"], input="/exit\n")
    assert result.exit_code == 0
    assert "HomeMaster V1.6" in result.stdout
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
    assert "模型回复：你好，我在。" in result.stdout
    assert "status:" not in result.stdout
    assert "trace:" not in result.stdout
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
    assert "状态：replied" in result.stdout


def test_shell_new_resets_session(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    result = CliRunner().invoke(app, ["shell"], input="/new\n/exit\n")
    assert result.exit_code == 0
    assert "新会话已创建" in result.stdout


def test_shell_help_explains_events_and_compact(monkeypatch) -> None:
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    result = CliRunner().invoke(app, ["shell"], input="/help\n/exit\n")
    assert result.exit_code == 0
    assert "/compact：立即压缩已有上下文" in result.stdout
    assert "/events：查看上一轮 trace 文件路径" in result.stdout


def test_shell_compact_runs_immediately(monkeypatch, tmp_path) -> None:
    calls: list[dict] = []
    trace_path = tmp_path / "compact_events.jsonl"

    def fake_compact_agent_context(session, **kwargs):
        calls.append(kwargs)
        return FakeCompact(trace_path=trace_path)

    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.compact_agent_context",
        fake_compact_agent_context,
    )
    result = CliRunner().invoke(app, ["shell"], input="/compact\n/events\n/exit\n")

    assert result.exit_code == 0
    assert "上下文压缩：已压缩上下文：100 -> 50 tokens" in result.stdout
    assert calls[0]["progress"] is False
    assert f"上一轮 trace 文件：{trace_path}" in result.stdout


def test_shell_events_reports_last_trace_path(monkeypatch, tmp_path) -> None:
    trace_path = tmp_path / "runtime_events.jsonl"

    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_doctor",
        lambda live=False: SimpleNamespace(has_failures=False),
    )
    monkeypatch.setattr(
        "homemaster.cli.interactive_shell.run_agent_turn",
        lambda session, text, **kwargs: FakeTurn(trace_path=trace_path, tool_events=[]),
    )
    result = CliRunner().invoke(app, ["shell"], input="你好\n/events\n/exit\n")

    assert result.exit_code == 0
    assert f"上一轮 trace 文件：{trace_path}" in result.stdout


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


def test_new_session_id_is_human_readable() -> None:
    session_id = new_session_id()
    assert len(session_id) == 22
    assert session_id[8] == "_"
    assert session_id[15] == "_"
    assert session_id[:8].isdigit()
    assert session_id[9:15].isdigit()
