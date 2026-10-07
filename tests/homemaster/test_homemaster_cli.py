from __future__ import annotations

import importlib
from pathlib import Path

from typer.testing import CliRunner

from homemaster.cli import app


def _patch_serve(monkeypatch) -> dict:
    """Intercept the bare-serve path: probe dead, port free, server stubbed."""

    cli_module = importlib.import_module("homemaster.cli.app")
    captured: dict = {"serve": None, "browser": []}
    monkeypatch.setattr(cli_module, "probe_server", lambda url: (False, None))
    monkeypatch.setattr(cli_module, "validate_port_available", lambda *a: None)
    monkeypatch.setattr(
        cli_module,
        "run_web_server",
        lambda **kwargs: captured.__setitem__("serve", kwargs),
    )
    monkeypatch.setattr(
        cli_module.webbrowser,
        "open",
        lambda url: captured["browser"].append(url),
    )
    monkeypatch.setattr(
        cli_module,
        "_open_browser_when_ready",
        lambda url, **kwargs: captured["browser"].append(url),
    )
    return captured


def test_cli_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.stdout
    assert "HomeMaster" in result.stdout
    assert "run" in result.stdout
    assert "doctor" in result.stdout
    assert "shell" in result.stdout
    assert "serve" in result.stdout


def test_bare_invocation_serves_foreground_and_opens_browser(monkeypatch) -> None:
    captured = _patch_serve(monkeypatch)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0, result.output
    assert captured["serve"] == {
        "host": "127.0.0.1",
        "port": 8000,
        "config_path": None,
    }
    assert captured["browser"] == ["http://127.0.0.1:8000"]


def test_bare_invocation_on_ssh_without_display_prints_tunnel_hint(monkeypatch) -> None:
    captured = _patch_serve(monkeypatch)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 1 10.0.0.2 22")
    monkeypatch.delenv("DISPLAY", raising=False)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "ssh -L 8000:127.0.0.1:8000" in result.output
    assert captured["browser"] == []


def test_bare_invocation_no_browser_skips_handoff(monkeypatch) -> None:
    captured = _patch_serve(monkeypatch)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    result = CliRunner().invoke(app, ["--no-browser", "--port", "8899"])

    assert result.exit_code == 0, result.output
    assert captured["serve"]["port"] == 8899
    assert captured["browser"] == []


def test_bare_invocation_attaches_to_already_running_server(monkeypatch) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured: dict = {"browser": [], "serve_calls": 0}
    monkeypatch.setattr(
        cli_module,
        "probe_server",
        lambda url: (True, {"version": "1.0"}),
    )
    monkeypatch.setattr(
        cli_module,
        "run_web_server",
        lambda **kwargs: captured.__setitem__("serve_calls", captured["serve_calls"] + 1),
    )
    monkeypatch.setattr(
        cli_module.webbrowser,
        "open",
        lambda url: captured["browser"].append(url),
    )
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 0, result.output
    assert "already running" in result.output
    assert captured["browser"] == ["http://127.0.0.1:8000"]
    assert captured["serve_calls"] == 0


def test_gateway_subcommand_delegates(monkeypatch, tmp_path: Path) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    config_path = tmp_path / "homemaster.yaml"
    config = object()

    monkeypatch.setattr(cli_module, "load_config", lambda path: captured.append(path) or config)
    monkeypatch.setattr(
        cli_module,
        "run_gateway",
        lambda value, **kwargs: captured.append((value, kwargs)),
    )

    result = CliRunner().invoke(app, ["gateway", "--config", str(config_path)])

    assert result.exit_code == 0, result.stdout
    assert captured == [config_path, (config, {"environment": None})]


def test_gateway_alfworld_flag_selects_alfworld_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    config_path = tmp_path / "homemaster.yaml"
    config = object()

    monkeypatch.setattr(cli_module, "load_config", lambda _path: config)
    monkeypatch.setattr(
        cli_module,
        "run_gateway",
        lambda value, **kwargs: captured.append((value, kwargs)),
    )

    result = CliRunner().invoke(
        app,
        ["gateway", "--alfworld", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == [(config, {"environment": "alfworld"})]


def test_gateway_browser_flag_selects_browser_environment(monkeypatch, tmp_path: Path) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    config_path = tmp_path / "homemaster.yaml"
    config = object()
    monkeypatch.setattr(cli_module, "load_config", lambda _path: config)
    monkeypatch.setattr(
        cli_module,
        "run_gateway",
        lambda value, **kwargs: captured.append((value, kwargs)),
    )

    result = CliRunner().invoke(
        app,
        ["gateway", "--browser", "--config", str(config_path)],
    )

    assert result.exit_code == 0, result.stdout
    assert captured == [(config, {"environment": "browser"})]


def test_gateway_environment_flags_are_mutually_exclusive() -> None:
    result = CliRunner().invoke(app, ["gateway", "--alfworld", "--browser"])

    assert result.exit_code != 0
    assert "--alfworld and --browser are mutually exclusive" in result.output


def test_gateway_rejects_unknown_top_level_print_flag() -> None:
    result = CliRunner().invoke(app, ["gateway", "--print", "hello"])

    assert result.exit_code != 0


def test_shell_subcommand_defaults_to_remote_client(monkeypatch) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    monkeypatch.setattr(
        cli_module,
        "run_remote_shell",
        lambda **kwargs: captured.append(kwargs) or 0,
    )
    monkeypatch.setattr(
        cli_module,
        "run_interactive_shell",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("local shell must not start")),
    )

    result = CliRunner().invoke(app, ["shell"])

    assert result.exit_code == 0, result.output
    assert captured == [
        {
            "server": None,
            "config_path": None,
            "resume_session_id": None,
            "continue_latest": False,
        }
    ]


def test_shell_local_still_routes_to_in_process_shell(monkeypatch) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    monkeypatch.setattr(
        cli_module,
        "run_interactive_shell",
        lambda **kwargs: captured.append(kwargs),
    )
    monkeypatch.setattr(
        cli_module,
        "run_remote_shell",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("remote shell must not start")),
    )

    result = CliRunner().invoke(app, ["shell", "--local"])

    assert result.exit_code == 0, result.output
    assert captured and captured[0]["permission_mode"] is not None


def test_shell_forwards_resume_continue_and_server(monkeypatch) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    captured = []
    monkeypatch.setattr(
        cli_module,
        "run_remote_shell",
        lambda **kwargs: captured.append(kwargs) or 0,
    )

    result = CliRunner().invoke(
        app,
        ["shell", "--resume", "session-7", "--server", "http://127.0.0.1:9123"],
    )

    assert result.exit_code == 0, result.output
    assert captured[0]["resume_session_id"] == "session-7"
    assert captured[0]["server"] == "http://127.0.0.1:9123"

    captured.clear()
    result = CliRunner().invoke(app, ["shell", "--continue"])
    assert result.exit_code == 0, result.output
    assert captured[0]["continue_latest"] is True


def test_shell_exit_code_propagates(monkeypatch) -> None:
    cli_module = importlib.import_module("homemaster.cli.app")
    monkeypatch.setattr(cli_module, "run_remote_shell", lambda **kwargs: 3)

    result = CliRunner().invoke(app, ["shell"])

    assert result.exit_code == 3


def test_global_config_is_rejected_with_a_subcommand() -> None:
    result = CliRunner().invoke(
        app,
        ["--config", "config/homemaster.yaml", "doctor"],
    )

    assert result.exit_code != 0
    assert "global --config is only valid without a subcommand" in result.output


def test_top_level_client_flags_rejected_with_subcommand() -> None:
    for args in (
        ["-p", "hi", "doctor"],
        ["--local", "doctor"],
        ["--server", "http://x", "doctor"],
    ):
        result = CliRunner().invoke(app, args)
        assert result.exit_code != 0, args


def test_alfworld_is_no_longer_a_top_level_flag() -> None:
    result = CliRunner().invoke(app, ["--alfworld"])

    assert result.exit_code != 0


def test_browser_requires_a_local_print_or_gateway() -> None:
    result = CliRunner().invoke(app, ["--browser"])

    assert result.exit_code != 0
    assert "--browser" in result.output
