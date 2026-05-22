from __future__ import annotations

from typer.testing import CliRunner

from homemaster.cli import app


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster" in result.stdout
    assert "run" in result.stdout
    assert "doctor" in result.stdout
    assert "shell" in result.stdout
