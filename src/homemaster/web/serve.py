"""Loopback-only production entrypoint for the HomeMaster Web Console."""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import socket
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI

from homemaster.cli.composition import create_home_application
from homemaster.config import load_config
from homemaster.gateway.alfworld import (
    AlfworldGatewayApplication,
    create_alfworld_gateway_binding,
)
from homemaster.memory.management import MemoryManagementService
from homemaster.permissions import PermissionMode
from homemaster.web.app import create_web_app
from homemaster.web.confirmations import WebConfirmationHandler


def validate_bind_host(host: str) -> str:
    """Reject every unauthenticated network bind except the local loopback."""

    if host.casefold() == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Web Console host must be a loopback address or localhost") from exc
    if not address.is_loopback:
        raise ValueError("Web Console host must be a loopback address or localhost")
    return host


def validate_port_available(host: str, port: int) -> int:
    """Fail before runtime construction when the requested listener is occupied."""

    if not 1 <= port <= 65535:
        raise ValueError("Web Console port must be between 1 and 65535")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError(f"Web Console address {host!r} could not be resolved")
    last_error: OSError | None = None
    seen: set[tuple[int, object]] = set()
    for family, socktype, proto, _canonname, sockaddr in addresses:
        key = (family, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            probe = socket.socket(family, socktype, proto)
        except PermissionError:
            # Some sandboxes deny socket creation even for localhost. Uvicorn remains
            # the final bind authority in that environment.
            return port
        with probe:
            try:
                probe.bind(sockaddr)
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    raise ValueError(
                        f"Web Console port {port} on {host} is already in use; choose another port"
                    ) from exc
                last_error = exc
    if last_error is not None:
        raise ValueError(f"Web Console cannot bind {host}:{port}: {last_error}") from last_error
    return port


def create_home_web_app(config_path: Path | None = None) -> FastAPI:
    """Compose one long-lived runtime with capabilities selected by configuration."""

    confirmation_handler = WebConfirmationHandler()
    bundle = create_home_application(
        config=load_config(config_path=config_path) if config_path is not None else None,
        progress=False,
        quiet=True,
        console_show_replies=False,
        permission_mode=PermissionMode.FULL_AUTO,
        confirmation_handler=confirmation_handler,
        publish_artifacts=True,
    )
    memory_management_service = (
        MemoryManagementService(bundle.mindmemos, bundle.application.session_manager)
        if bundle.mindmemos is not None
        else None
    )
    app = create_web_app(
        application=bundle.application,
        confirmation_handler=confirmation_handler,
        memory_management_service=memory_management_service,
    )
    app.state.home_bundle = bundle
    return app


def create_browser_web_app(config_path: Path | None = None) -> FastAPI:
    """Compose the Web Console with the same browser execution profile as other channels."""

    confirmation_handler = WebConfirmationHandler()
    bundle = create_home_application(
        config=load_config(config_path=config_path),
        progress=False,
        quiet=True,
        console_show_replies=False,
        tool_environment="browser",
        permission_mode=PermissionMode.FULL_AUTO,
        confirmation_handler=confirmation_handler,
        publish_artifacts=True,
    )
    memory_management_service = (
        MemoryManagementService(bundle.mindmemos, bundle.application.session_manager)
        if bundle.mindmemos is not None
        else None
    )
    app = create_web_app(
        application=bundle.application,
        confirmation_handler=confirmation_handler,
        memory_management_service=memory_management_service,
    )
    app.state.home_bundle = bundle
    return app


async def create_alfworld_web_app() -> FastAPI:
    """Compose the Web Console around the existing fixed-episode ALFWorld adapter."""

    confirmation_handler = WebConfirmationHandler()
    bundle = create_home_application(
        progress=False,
        quiet=True,
        console_show_replies=False,
        tool_environment="alfworld",
        permission_mode=PermissionMode.FULL_AUTO,
        confirmation_handler=confirmation_handler,
        publish_artifacts=True,
    )
    memory_management_service = (
        MemoryManagementService(bundle.mindmemos, bundle.application.session_manager)
        if bundle.mindmemos is not None
        else None
    )
    application = bundle.application
    try:
        binding, owner = await create_alfworld_gateway_binding(
            bundle.config.alfworld_gateway,
            run_dir=bundle.run_dir,
            resource_scope=bundle.application.resource_scope,
        )
        application = AlfworldGatewayApplication(bundle.application, owner, binding)
        app = create_web_app(
            application=application,
            confirmation_handler=confirmation_handler,
            memory_management_service=memory_management_service,
        )
    except BaseException:
        await confirmation_handler.aclose()
        await application.aclose()
        raise
    app.state.home_bundle = bundle
    app.state.alfworld_application = application
    return app


async def _serve_web_server(
    *,
    host: str,
    port: int,
    environment: Literal["alfworld", "browser"] | None,
    config_path: Path | None = None,
) -> None:
    if environment not in (None, "alfworld", "browser"):
        raise ValueError(f"unsupported Web environment: {environment}")
    if environment == "alfworld":
        app = await create_alfworld_web_app()
    elif environment == "browser":
        app = create_browser_web_app(config_path)
    else:
        app = create_home_web_app(config_path)
    try:
        config = uvicorn.Config(app=app, host=host, port=port)
        await uvicorn.Server(config).serve()
    finally:
        await app.state.aclose()


def run_web_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    environment: Literal["alfworld", "browser"] | None = None,
    config_path: Path | None = None,
) -> None:
    """Validate the local bind before constructing any runtime resources."""

    validated_host = validate_bind_host(host)
    validate_port_available(validated_host, port)
    asyncio.run(
        _serve_web_server(
            host=validated_host,
            port=port,
            environment=environment,
            config_path=config_path,
        )
    )


__all__ = [
    "create_alfworld_web_app",
    "create_browser_web_app",
    "create_home_web_app",
    "run_web_server",
    "validate_bind_host",
    "validate_port_available",
]
