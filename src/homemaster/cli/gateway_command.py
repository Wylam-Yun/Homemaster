"""Gateway CLI lifecycle using the same Home application composition root."""

from __future__ import annotations

import asyncio
import os

from homemaster.application.resources import ResourceCleanupError
from homemaster.cli.composition import create_home_application
from homemaster.config import HomeMasterConfig
from homemaster.gateway.runtime import build_gateway_assembly


async def serve_gateway(config: HomeMasterConfig) -> None:
    if not config.gateway.enabled or not config.gateway.telegram.enabled:
        raise ValueError("gateway and gateway.telegram must both be enabled")
    bundle = create_home_application(config=config, progress=False, quiet=True)
    sensitive_values = [
        key for provider in config.providers.items for key in provider.api_keys if key
    ]
    telegram_token = os.environ.get(config.gateway.telegram.token_env, "").strip()
    if telegram_token:
        sensitive_values.append(telegram_token)
    assembly = build_gateway_assembly(
        bundle.application,
        config.gateway,
        sensitive_values=tuple(sensitive_values),
    )
    try:
        await assembly.runtime.serve(assembly.channel)
    finally:
        await assembly.runtime.aclose(deadline_s=config.gateway.shutdown_deadline_s)
        try:
            await bundle.application.aclose()
        except ResourceCleanupError:
            raise


def run_gateway(config: HomeMasterConfig) -> None:
    asyncio.run(serve_gateway(config))


__all__ = ["run_gateway", "serve_gateway"]
