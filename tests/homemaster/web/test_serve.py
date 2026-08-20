"""Tests for the loopback-only Web Console production entrypoint."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.permissions import PermissionMode
from homemaster.web import serve


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "LOCALHOST", "::1"])
def test_validate_bind_host_accepts_only_loopback_names_and_addresses(host: str) -> None:
    assert serve.validate_bind_host(host) == host


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.test", ""])
def test_validate_bind_host_rejects_non_loopback(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        serve.validate_bind_host(host)


def test_create_home_web_app_uses_confirming_permission_mode(monkeypatch) -> None:
    captured: dict[str, object] = {}
    application = object()
    bundle = SimpleNamespace(application=application)
    expected_app = SimpleNamespace(state=SimpleNamespace())

    def fake_create_home_application(**kwargs):
        captured.update(kwargs)
        return bundle

    def fake_create_web_app(**kwargs):
        captured.update({f"web_{key}": value for key, value in kwargs.items()})
        return expected_app

    monkeypatch.setattr(serve, "create_home_application", fake_create_home_application)
    monkeypatch.setattr(serve, "create_web_app", fake_create_web_app)

    result = serve.create_home_web_app()

    assert result is expected_app
    assert captured["permission_mode"] is PermissionMode.DEFAULT
    assert captured["console_show_replies"] is False
    assert captured["progress"] is False
    assert captured["quiet"] is True
    assert captured["publish_artifacts"] is True
    assert captured["web_application"] is application
    assert captured["web_confirmation_handler"] is captured["confirmation_handler"]
    assert expected_app.state.home_bundle is bundle


def test_serve_cli_rejects_public_bind_before_app_construction(monkeypatch) -> None:
    created = False

    def forbidden_create():
        nonlocal created
        created = True
        raise AssertionError("application must not be constructed")

    monkeypatch.setattr(serve, "create_home_web_app", forbidden_create)

    result = CliRunner().invoke(app, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code != 0
    assert "loopback" in result.stdout.lower()
    assert created is False


def test_run_web_server_validates_then_starts_uvicorn(monkeypatch) -> None:
    expected_app = object()
    calls: list[tuple[object, str, int]] = []
    monkeypatch.setattr(serve, "create_home_web_app", lambda: expected_app)
    monkeypatch.setattr(
        serve.uvicorn,
        "run",
        lambda app, *, host, port: calls.append((app, host, port)),
    )

    serve.run_web_server(host="127.0.0.1", port=9123)

    assert calls == [(expected_app, "127.0.0.1", 9123)]
