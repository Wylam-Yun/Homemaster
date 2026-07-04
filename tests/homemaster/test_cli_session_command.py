"""Tests for persisted session CLI commands."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from homemaster.agent.messages import UserMessage
from homemaster.agent.session import AgentSession
from homemaster.agent.session_persistence import save_snapshot
from homemaster.agent.state import AgentState, ProviderUsage
from homemaster.cli.app import app
from homemaster.task_state.store import TaskStateStore


def test_session_list_shows_persisted_snapshot(monkeypatch, tmp_path) -> None:
    session = AgentSession(session_id="s-list")
    session.append(UserMessage.from_text("hello"))
    agent_state = AgentState(
        run_id="r-list",
        session_id="s-list",
        status="replied",
        iteration_index=3,
        provider_usage=ProviderUsage(total_tokens=1200),
    )
    task_store = TaskStateStore(run_id="r-list")
    save_snapshot(
        session=session,
        agent_state=agent_state,
        task_state_store=task_store,
        path=tmp_path / "s-list" / "session.json",
        model="test-model",
        system_prompt="system",
    )
    monkeypatch.setattr(
        "homemaster.cli.session_command.load_config",
        lambda: SimpleNamespace(observability=SimpleNamespace(session_dir=str(tmp_path))),
    )

    result = CliRunner().invoke(app, ["session", "list"])

    assert result.exit_code == 0
    assert "s-list" in result.stdout
    assert "replied" in result.stdout
    assert "1K" in result.stdout
