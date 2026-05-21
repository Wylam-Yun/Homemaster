"""Tests for CLI help text and error classification."""

from __future__ import annotations

from typer.testing import CliRunner

from homemaster.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Help text contract tests
# ---------------------------------------------------------------------------


def test_run_help_mentions_agent_runtime() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "AgentRuntime" in result.output


def test_stage_help_lists_understand() -> None:
    result = runner.invoke(app, ["stage", "--help"])
    assert result.exit_code == 0
    assert "understand" in result.output


def test_smoke_help_lists_contract() -> None:
    result = runner.invoke(app, ["smoke", "--help"])
    assert result.exit_code == 0
    assert "contract" in result.output


def test_top_level_help_shows_run_doctor_stage_smoke() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("run", "doctor", "stage", "smoke"):
        assert cmd in result.output


# ---------------------------------------------------------------------------
# Error classification tests
# ---------------------------------------------------------------------------


def test_config_error_exits_2(monkeypatch) -> None:
    """RuntimeConfigError → exit code 2, config_failed prefix."""
    from homemaster.runtime import RuntimeConfigError

    def _raise_config(*args, **kwargs):
        raise RuntimeConfigError("bad config")

    monkeypatch.setattr(
        "homemaster.cli.run_command.run_homemaster_task", _raise_config,
    )
    result = runner.invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "fetch_cup_retry"],
    )
    assert result.exit_code == 2
    assert "config_failed" in result.stderr


def test_run_error_exits_1(monkeypatch) -> None:
    """HomeMasterRunError → exit code 1, run_failed prefix."""
    from homemaster.task_runner import HomeMasterRunError

    def _raise_run(*args, **kwargs):
        raise HomeMasterRunError("run failed")

    monkeypatch.setattr(
        "homemaster.cli.run_command.run_homemaster_task", _raise_run,
    )
    result = runner.invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "fetch_cup_retry"],
    )
    assert result.exit_code == 1
    assert "run_failed" in result.stderr


def test_llm_provider_error_exits_1(monkeypatch) -> None:
    """LLMProviderNetworkError → exit code 1, run_failed prefix."""
    from homemaster.llm_client import LLMProviderNetworkError

    def _raise_llm(*args, **kwargs):
        raise LLMProviderNetworkError(
            error_type="provider_network_error", message="connection refused",
        )

    monkeypatch.setattr(
        "homemaster.cli.run_command.run_homemaster_task", _raise_llm,
    )
    result = runner.invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "fetch_cup_retry"],
    )
    assert result.exit_code == 1
    assert "run_failed" in result.stderr


def test_embedding_provider_error_exits_1(monkeypatch) -> None:
    """EmbeddingProviderNetworkError → exit code 1, run_failed prefix."""
    from homemaster.embedding_client import EmbeddingProviderNetworkError

    def _raise_emb(*args, **kwargs):
        raise EmbeddingProviderNetworkError(
            error_type="provider_network_error", message="timeout",
        )

    monkeypatch.setattr(
        "homemaster.cli.run_command.run_homemaster_task", _raise_emb,
    )
    result = runner.invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "fetch_cup_retry"],
    )
    assert result.exit_code == 1
    assert "run_failed" in result.stderr


def test_unexpected_error_exits_1(monkeypatch) -> None:
    """Unexpected exception → exit code 1, internal_error prefix."""
    def _raise_unexpected(*args, **kwargs):
        raise RuntimeError("something broke")

    monkeypatch.setattr(
        "homemaster.cli.run_command.run_homemaster_task", _raise_unexpected,
    )
    result = runner.invoke(
        app,
        ["run", "--utterance", "test", "--scenario", "fetch_cup_retry"],
    )
    assert result.exit_code == 1
    assert "internal_error" in result.stderr
