"""Tests for the loopback-only Web Console production entrypoint."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

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


@pytest.mark.parametrize(
    ("args", "expected_environment"),
    [(["serve"], None), (["serve", "--alfworld"], "alfworld")],
)
def test_serve_cli_selects_web_environment(
    monkeypatch, args: list[str], expected_environment: str | None
) -> None:
    calls: list[dict[str, object]] = []
    cli_app_module = importlib.import_module("homemaster.cli.app")
    monkeypatch.setattr(
        cli_app_module,
        "run_web_server",
        lambda **kwargs: calls.append(kwargs),
    )

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 0, result.stdout
    assert calls == [
        {"host": "127.0.0.1", "port": 8000, "environment": expected_environment}
    ]


def test_run_web_server_validates_then_owns_async_server(monkeypatch) -> None:
    serve_async = AsyncMock()
    monkeypatch.setattr(serve, "_serve_web_server", serve_async, raising=False)

    serve.run_web_server(host="127.0.0.1", port=9123, environment="alfworld")

    serve_async.assert_awaited_once_with(
        host="127.0.0.1",
        port=9123,
        environment="alfworld",
    )


@pytest.mark.asyncio
async def test_create_alfworld_web_app_reuses_existing_binding(monkeypatch) -> None:
    captured: dict[str, object] = {}
    close_base = AsyncMock()
    base_application = SimpleNamespace(resource_scope=object(), aclose=close_base)
    config = SimpleNamespace(alfworld_gateway=object())
    bundle = SimpleNamespace(
        application=base_application,
        config=config,
        run_dir=object(),
    )
    binding = object()
    owner = SimpleNamespace(claim=AsyncMock(return_value=True), seal=AsyncMock())
    expected_app = SimpleNamespace(state=SimpleNamespace())

    def fake_create_home_application(**kwargs):
        captured.update(kwargs)
        return bundle

    async def fake_create_binding(binding_config, *, run_dir, resource_scope):
        captured["binding_config"] = binding_config
        captured["run_dir"] = run_dir
        captured["resource_scope"] = resource_scope
        return binding, owner

    def fake_create_web_app(**kwargs):
        captured.update({f"web_{key}": value for key, value in kwargs.items()})
        return expected_app

    monkeypatch.setattr(serve, "create_home_application", fake_create_home_application)
    monkeypatch.setattr(
        serve, "create_alfworld_gateway_binding", fake_create_binding, raising=False
    )
    monkeypatch.setattr(serve, "create_web_app", fake_create_web_app)

    result = await serve.create_alfworld_web_app()

    assert result is expected_app
    assert captured["tool_environment"] == "alfworld"
    assert captured["permission_mode"] is PermissionMode.DEFAULT
    assert captured["binding_config"] is config.alfworld_gateway
    assert captured["run_dir"] is bundle.run_dir
    assert captured["resource_scope"] is base_application.resource_scope
    wrapped = captured["web_application"]
    assert wrapped is expected_app.state.alfworld_application
    assert wrapped._application is base_application
    assert wrapped._binding is binding
    assert wrapped._owner is owner
    assert expected_app.state.home_bundle is bundle
    close_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_alfworld_web_app_closes_base_when_binding_fails(monkeypatch) -> None:
    close_base = AsyncMock()
    base_application = SimpleNamespace(resource_scope=object(), aclose=close_base)
    bundle = SimpleNamespace(
        application=base_application,
        config=SimpleNamespace(alfworld_gateway=object()),
        run_dir=object(),
    )

    monkeypatch.setattr(serve, "create_home_application", lambda **_kwargs: bundle)

    async def fail_binding(*_args, **_kwargs):
        raise RuntimeError("binding failed")

    monkeypatch.setattr(
        serve, "create_alfworld_gateway_binding", fail_binding, raising=False
    )

    with pytest.raises(RuntimeError, match="binding failed"):
        await serve.create_alfworld_web_app()

    close_base.assert_awaited_once_with()
