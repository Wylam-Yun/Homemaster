"""Gateway CLI lifecycle using the same Home application composition root."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from typing import Literal

from homemaster.application.resources import ResourceCleanupError
from homemaster.channels.feishu_groups import FeishuGroupOperations
from homemaster.channels.impl.feishu import FeishuApiService
from homemaster.cli.composition import create_home_application
from homemaster.config import HomeMasterConfig
from homemaster.gateway.alfworld import (
    AlfworldGatewayApplication,
    create_alfworld_gateway_binding,
)
from homemaster.gateway.confirmation import FeishuGatewayConfirmationHandler
from homemaster.gateway.runtime import build_gateway_assembly


async def serve_gateway(
    config: HomeMasterConfig,
    *,
    environment: Literal["alfworld", "browser"] | None = None,
) -> None:
    if not config.gateway.enabled or not config.gateway.feishu.enabled:
        raise ValueError("gateway and gateway.feishu must both be enabled")
    api_service = FeishuApiService.from_config(config.gateway.feishu)
    group_operations = FeishuGroupOperations(api_service)
    confirmation_handler = FeishuGatewayConfirmationHandler(timeout_s=300)
    bundle = create_home_application(
        config=config,
        progress=False,
        quiet=True,
        feishu_group_operations=group_operations,
        tool_environment=environment,
        confirmation_handler=confirmation_handler,
    )
    gateway_application = bundle.application
    profile = "home"
    alfworld_application: AlfworldGatewayApplication | None = None
    try:
        if environment == "alfworld":
            binding, owner = await create_alfworld_gateway_binding(
                config.alfworld_gateway,
                run_dir=bundle.run_dir,
                resource_scope=bundle.application.resource_scope,
            )
            alfworld_application = AlfworldGatewayApplication(
                bundle.application,
                owner,
                binding,
            )
            gateway_application = alfworld_application
            profile = "alfworld"
        elif environment == "browser" or config.browser_gateway.start_url is not None:
            profile = "browser"
        assembly = build_gateway_assembly(
            gateway_application,
            config.gateway,
            api_service=api_service,
            group_operations=group_operations,
            profile=profile,
            confirmation_handler=confirmation_handler,
        )
    except BaseException:
        await confirmation_handler.aclose()
        await bundle.application.aclose()
        raise
    shutdown_requested = asyncio.Event()
    remove_shutdown_handlers = _install_shutdown_handlers(
        asyncio.get_running_loop(), shutdown_requested
    )
    try:
        await _serve_until_shutdown(
            assembly.runtime,
            assembly.channel,
            shutdown_requested=shutdown_requested,
        )
    finally:
        remove_shutdown_handlers()
        await assembly.runtime.aclose(deadline_s=config.gateway.shutdown_deadline_s)
        if alfworld_application is not None:
            await alfworld_application.seal()
        try:
            await bundle.application.aclose()
        except ResourceCleanupError:
            raise


def run_gateway(
    config: HomeMasterConfig,
    *,
    environment: Literal["alfworld", "browser"] | None = None,
) -> None:
    asyncio.run(serve_gateway(config, environment=environment))


def _install_shutdown_handlers(
    loop: asyncio.AbstractEventLoop, shutdown_requested: asyncio.Event
) -> Callable[[], None]:
    registered: list[signal.Signals] = []
    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(shutdown_signal, shutdown_requested.set)
        except (NotImplementedError, RuntimeError):
            continue
        registered.append(shutdown_signal)

    def remove_handlers() -> None:
        for shutdown_signal in registered:
            try:
                loop.remove_signal_handler(shutdown_signal)
            except (NotImplementedError, RuntimeError):
                continue

    return remove_handlers


async def _serve_until_shutdown(
    runtime,
    channel,
    *,
    shutdown_requested: asyncio.Event,
) -> None:
    service = asyncio.create_task(runtime.serve(channel), name="gateway:service")
    shutdown = asyncio.create_task(shutdown_requested.wait(), name="gateway:shutdown-signal")
    try:
        done, _pending = await asyncio.wait(
            (service, shutdown), return_when=asyncio.FIRST_COMPLETED
        )
        if shutdown in done and not service.done():
            service.cancel()
        try:
            await service
        except asyncio.CancelledError:
            if shutdown.done():
                return
            raise
    finally:
        shutdown.cancel()
        try:
            await shutdown
        except asyncio.CancelledError:
            pass


__all__ = ["run_gateway", "serve_gateway"]
