"""Gateway CLI lifecycle using the same Home application composition root."""

from __future__ import annotations

import asyncio

from homemaster.application.resources import ResourceCleanupError
from homemaster.channels.feishu_groups import FeishuGroupOperations
from homemaster.channels.impl.feishu import FeishuApiService
from homemaster.cli.composition import create_home_application
from homemaster.config import HomeMasterConfig
from homemaster.gateway.runtime import build_gateway_assembly


async def serve_gateway(config: HomeMasterConfig) -> None:
    if not config.gateway.enabled or not config.gateway.feishu.enabled:
        raise ValueError("gateway and gateway.feishu must both be enabled")
    api_service = FeishuApiService.from_config(config.gateway.feishu)
    group_operations = FeishuGroupOperations(api_service)
    bundle = create_home_application(
        config=config,
        progress=False,
        quiet=True,
        feishu_group_operations=group_operations,
    )
    assembly = build_gateway_assembly(
        bundle.application,
        config.gateway,
        api_service=api_service,
        group_operations=group_operations,
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
