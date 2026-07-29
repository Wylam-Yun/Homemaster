"""Tests for CLI help text."""

from __future__ import annotations

from typer.testing import CliRunner

from homemaster.cli.app import app

runner = CliRunner()


def test_help_exposes_only_final_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "shell" in result.stdout
    assert "doctor" in result.stdout
    assert "gateway" in result.stdout
    assert "benchmark-alfworld" in result.stdout
    assert "stage" not in result.stdout.lower()
    assert "smoke" not in result.stdout.lower()
    assert "scenario" not in result.stdout.lower()
    assert "contract-smoke" not in result.stdout.lower()
    assert "understand" not in result.stdout.lower()
    assert "--print" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--output-format" in result.stdout
    assert "--resume" in result.stdout
    assert "--continue" in result.stdout
    assert "--gateway" in result.stdout
    assert "--alfworld" in result.stdout
    assert "--config" in result.stdout


def test_run_help_mentions_generic_agent() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "agent" in result.stdout.lower()


def test_run_help_does_not_mention_scenario() -> None:
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "scenario" not in result.stdout.lower()


def test_gateway_help_exposes_ignored_config_path() -> None:
    result = runner.invoke(app, ["gateway", "--help"])
    assert result.exit_code == 0
    assert "--config" in result.stdout
    assert "feishu" in result.stdout.lower()
    assert "telegram" not in result.stdout.lower()
