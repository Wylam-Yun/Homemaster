"""Loopback-only production entrypoint for the HomeMaster Web Console."""

from __future__ import annotations

import ipaddress

import uvicorn
from fastapi import FastAPI

from homemaster.cli.composition import create_home_application
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


def create_home_web_app() -> FastAPI:
    """Compose one long-lived Home runtime and its browser adapter."""

    confirmation_handler = WebConfirmationHandler()
    bundle = create_home_application(
        progress=False,
        quiet=True,
        console_show_replies=False,
        permission_mode=PermissionMode.DEFAULT,
        confirmation_handler=confirmation_handler,
    )
    app = create_web_app(
        application=bundle.application,
        confirmation_handler=confirmation_handler,
    )
    app.state.home_bundle = bundle
    return app


def run_web_server(*, host: str = "127.0.0.1", port: int = 8000) -> None:
    """Validate the local bind before constructing any runtime resources."""

    validated_host = validate_bind_host(host)
    app = create_home_web_app()
    uvicorn.run(app, host=validated_host, port=port)


__all__ = ["create_home_web_app", "run_web_server", "validate_bind_host"]
