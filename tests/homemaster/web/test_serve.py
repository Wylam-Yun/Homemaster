"""Tests for the loopback-only Web Console production entrypoint."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from homemaster.cli.app import app
from homemaster.memory.management import MemoryManagementService
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
    session_manager = object()
    mindmemos = object()
    application = SimpleNamespace(session_manager=session_manager)
    bundle = SimpleNamespace(application=application, mindmemos=mindmemos)
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
    memory_service = captured["web_memory_management_service"]
    assert isinstance(memory_service, MemoryManagementService)
    assert memory_service._mindmemos is mindmemos
    assert memory_service._sessions is session_manager
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
    ("args", "expected_environment", "expected_config"),
    [
        (["serve"], None, None),
        (
            ["serve", "--config", "/tmp/homemaster.yaml"],
            None,
            Path("/tmp/homemaster.yaml"),
        ),
        (["serve", "--alfworld"], "alfworld", None),
        (
            ["serve", "--browser", "--config", "/tmp/homemaster.yaml"],
            "browser",
            Path("/tmp/homemaster.yaml"),
        ),
    ],
)
def test_serve_cli_selects_web_environment(
    monkeypatch,
    args: list[str],
    expected_environment: str | None,
    expected_config: Path | None,
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
        {
            "host": "127.0.0.1",
            "port": 8000,
            "environment": expected_environment,
            "config_path": expected_config,
        }
    ]


def test_run_web_server_validates_then_owns_async_server(monkeypatch) -> None:
    serve_async = AsyncMock()
    monkeypatch.setattr(serve, "_serve_web_server", serve_async, raising=False)

    serve.run_web_server(host="127.0.0.1", port=9123, environment="alfworld")

    serve_async.assert_awaited_once_with(
        host="127.0.0.1",
        port=9123,
        environment="alfworld",
        config_path=None,
    )


@pytest.mark.asyncio
async def test_default_web_environment_forwards_config_to_common_composition(
    monkeypatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "homemaster.yaml"
    expected_app = SimpleNamespace(state=SimpleNamespace(aclose=AsyncMock()))
    captured: list[Path | None] = []

    monkeypatch.setattr(
        serve,
        "create_home_web_app",
        lambda path=None: captured.append(path) or expected_app,
    )
    monkeypatch.setattr(serve.uvicorn, "Config", lambda **_kwargs: object())

    class Server:
        def __init__(self, _config):
            pass

        async def serve(self):
            return None

    monkeypatch.setattr(serve.uvicorn, "Server", Server)

    await serve._serve_web_server(
        host="127.0.0.1",
        port=8000,
        environment=None,
        config_path=config_path,
    )

    assert captured == [config_path]
    expected_app.state.aclose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_create_alfworld_web_app_reuses_existing_binding(monkeypatch) -> None:
    captured: dict[str, object] = {}
    close_base = AsyncMock()
    session_manager = object()
    mindmemos = object()
    base_application = SimpleNamespace(
        resource_scope=object(),
        session_manager=session_manager,
        aclose=close_base,
    )
    config = SimpleNamespace(alfworld_gateway=object())
    bundle = SimpleNamespace(
        application=base_application,
        config=config,
        run_dir=object(),
        mindmemos=mindmemos,
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
    memory_service = captured["web_memory_management_service"]
    assert isinstance(memory_service, MemoryManagementService)
    assert memory_service._mindmemos is mindmemos
    assert memory_service._sessions is session_manager
    assert expected_app.state.home_bundle is bundle
    close_base.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_alfworld_web_app_closes_base_when_binding_fails(monkeypatch) -> None:
    close_base = AsyncMock()
    base_application = SimpleNamespace(
        resource_scope=object(),
        session_manager=object(),
        aclose=close_base,
    )
    bundle = SimpleNamespace(
        application=base_application,
        config=SimpleNamespace(alfworld_gateway=object()),
        run_dir=object(),
        mindmemos=object(),
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
