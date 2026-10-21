"""CLI smoke for `homemaster setup` — flag parsing and exit codes."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from homemaster import setup_runtime as setup_mod
from homemaster.cli.app import app


def test_setup_help_lists_flags() -> None:
    result = CliRunner().invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    for flag in (
        "--runtime-root",
        "--full",
        "--files",
        "--offline-bundle",
        "--offline",
        "--no-doctor",
        "--rebuild",
        "--with-browser",
        "--config",
    ):
        assert flag in result.stdout


def test_setup_forwards_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_setup_runtime(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(setup_mod, "setup_runtime", fake_setup_runtime)
    result = CliRunner().invoke(
        app,
        [
            "setup",
            "--runtime-root",
            str(tmp_path / "rt"),
            "--files",
            "--offline",
            "--offline-bundle",
            str(tmp_path / "bundle"),
            "--no-doctor",
            "--rebuild",
            "--with-browser",
            "--config",
            str(tmp_path / "cfg.yaml"),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert captured["runtime_root"] == tmp_path / "rt"
    assert captured["full"] is False
    assert captured["offline_only"] is True
    assert captured["offline_bundle"] == tmp_path / "bundle"
    assert captured["skip_doctor"] is True
    assert captured["rebuild"] is True
    assert captured["with_browser"] is True
    assert captured["config_path"] == tmp_path / "cfg.yaml"


def test_setup_defaults_files_and_home_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_setup_runtime(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(setup_mod, "setup_runtime", fake_setup_runtime)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 0, result.stdout
    assert captured["full"] is False
    assert captured["runtime_root"] is None  # defaults inside setup_runtime
    assert captured["skip_doctor"] is False


def test_setup_error_maps_to_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**kwargs: object) -> int:
        raise setup_mod.SetupError("unsupported platform: Plan9")

    monkeypatch.setattr(setup_mod, "setup_runtime", boom)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 2
    assert "[FAIL]" in result.stdout and "Plan9" in result.stdout


def test_setup_propagates_doctor_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_mod, "setup_runtime", lambda **kwargs: 1)
    result = CliRunner().invoke(app, ["setup"])
    assert result.exit_code == 1
