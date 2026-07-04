"""Tests for CLI run command."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import save_snapshot
from homemaster.agent.state import AgentState
from homemaster.cli.app import app
from homemaster.task_state.models import TaskStatus
from homemaster.task_state.store import TaskStateStore


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


def test_run_command_resume_without_utterance_prints_restored_status(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_snapshot(tmp_path, session_id="s1")
    monkeypatch.setattr(
        "homemaster.cli.run_command.load_config",
        lambda: SimpleNamespace(observability=SimpleNamespace(session_dir=str(tmp_path))),
    )

    result = CliRunner().invoke(app, ["run", "--resume", "s1"])

    assert result.exit_code == 0
    assert "session: s1" in result.stdout
    assert "status: resumed" in result.stdout


def test_run_command_resume_passes_restored_task_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write_snapshot(tmp_path, session_id="s2", task_status=TaskStatus.PAUSED)
    captured = {}

    def fake_run_agent_turn(session, text, **kwargs):
        captured["session_id"] = session.session_id
        captured["text"] = text
        captured["task_status"] = kwargs["task_state_store"].snapshot.status
        return FakeTurn(run_id=kwargs["run_id"], final_reply="继续。")

    monkeypatch.setattr(
        "homemaster.cli.run_command.load_config",
        lambda: SimpleNamespace(observability=SimpleNamespace(session_dir=str(tmp_path))),
    )
    monkeypatch.setattr("homemaster.cli.run_command.run_agent_turn", fake_run_agent_turn)

    result = CliRunner().invoke(
        app,
        ["run", "--resume", "s2", "--utterance", "继续"],
    )

    assert result.exit_code == 0
    assert captured == {
        "session_id": "s2",
        "text": "继续",
        "task_status": TaskStatus.ACTIVE,
    }
    assert "assistant: 继续。" in result.stdout


def _write_snapshot(
    root: Path,
    *,
    session_id: str,
    task_status: TaskStatus = TaskStatus.ACTIVE,
) -> None:
    session = AgentSession(session_id=session_id)
    from homemaster.agent.messages import UserMessage

    session.append(UserMessage.from_text("hello"))
    agent_state = AgentState(
        run_id=f"{session_id}-run",
        session_id=session_id,
        status="waiting_user",
        iteration_index=2,
    )
    task_store = TaskStateStore(run_id=agent_state.run_id)
    task_store.create_or_replace_plan(
        goal="test",
        subtasks=[{"id": "a", "title": "A", "description": "test step"}],
    )
    task_store.update_status(task_status)
    save_snapshot(
        session=session,
        agent_state=agent_state,
        task_state_store=task_store,
        path=root / session_id / "session.json",
        model="test-model",
        system_prompt="system",
    )
