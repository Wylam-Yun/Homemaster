"""Provider management routes backed by ``app.state.provider_config_service``."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import FastAPI

from homemaster.services.provider_config import ProviderConfigError
from homemaster.web._shared import _provider_config_error
from homemaster.web.context import WebAppContext


def register(app: FastAPI, ctx: WebAppContext) -> None:
    del ctx  # The service is app-owned and stays a lazy ``app.state`` read.

    @app.get("/api/provider-templates")
    async def list_provider_templates() -> object:
        service = app.state.provider_config_service
        return {"templates": [asdict(template) for template in service.templates()]}

    @app.get("/api/providers")
    async def list_providers() -> object:
        service = app.state.provider_config_service
        return {"providers": service.list_profiles()}

    @app.post("/api/providers")
    async def upsert_provider(body: dict[str, Any], overwrite: bool = False) -> object:
        service = app.state.provider_config_service
        try:
            return service.upsert(body, overwrite=overwrite)
        except ProviderConfigError as exc:
            return _provider_config_error(exc)

    @app.patch("/api/providers/{name}")
    async def patch_provider(name: str, body: dict[str, Any]) -> object:
        service = app.state.provider_config_service
        try:
            return service.patch(name, body)
        except ProviderConfigError as exc:
            return _provider_config_error(exc)

    @app.delete("/api/providers/{name}")
    async def delete_provider(name: str) -> object:
        service = app.state.provider_config_service
        try:
            return service.delete(name)
        except ProviderConfigError as exc:
            return _provider_config_error(exc)

    @app.post("/api/providers/test")
    async def test_provider(body: dict[str, Any] | None = None) -> object:
        service = app.state.provider_config_service
        # Always 200: probe outcomes (auth/network/model/schema) ride the body.
        return await service.test(body or {})

    @app.get("/api/providers/{name}/models")
    async def list_provider_models(name: str) -> object:
        service = app.state.provider_config_service
        try:
            return await service.list_models(name)
        except ProviderConfigError as exc:
            return _provider_config_error(exc)


__all__ = ["register"]
