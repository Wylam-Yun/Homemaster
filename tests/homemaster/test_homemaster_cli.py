from __future__ import annotations

import importlib
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli import app


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster" in result.stdout
    assert "run" in result.stdout
    assert "doctor" in result.stdout
    assert "shell" in result.stdout


def test_gateway_flag_delegates_without_starting_shell(monkeypatch, tmp_path: Path) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    config_path = tmp_path / "homemaster.yaml"
    config = object()

    monkeypatch.setattr(cli_module, "load_config", lambda path: captured.append(path) or config)
    monkeypatch.setattr(cli_module, "run_gateway", lambda value: captured.append(value))
    monkeypatch.setattr(
        cli_module,
        "run_interactive_shell",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("shell must not start")),
    )

    result = CliRunner().invoke(app, ["--gateway", "--config", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert captured == [config_path, config]


def test_gateway_flag_rejects_one_shot_options() -> None:
    result = CliRunner().invoke(app, ["--gateway", "--print", "hello"])

    assert result.exit_code != 0
    assert "--gateway cannot be combined" in result.output


def test_config_requires_gateway_flag() -> None:
    result = CliRunner().invoke(app, ["--config", "config/homemaster.yaml"])

    assert result.exit_code != 0
    assert "--config requires --gateway" in result.output
