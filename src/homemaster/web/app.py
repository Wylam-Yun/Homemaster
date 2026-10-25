"""FastAPI adapter for the HomeMaster browser console."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from homemaster.services.provider_config import ProviderConfigService
from homemaster.web._shared import _application_services, _error, _session_ids
from homemaster.web.confirmations import WebConfirmationHandler
from homemaster.web.context import WebAppContext
from homemaster.web.event_hub import WebEventHub
from homemaster.web.event_projection import WebEventProjection
from homemaster.web.memory_views import MemoryManagementService
from homemaster.web.pending_questions import PendingQuestionRegistry
from homemaster.web.routes import register_routes

# Kept importable from this module for the existing test/unit surface.
from homemaster.web.routes.runs import (
    _run_and_report_prestart_failure as _run_and_report_prestart_failure,
)
from homemaster.web.run_registry import WebRunRegistry
from homemaster.web.schemas import WebEvent
from homemaster.web.sse import mount_sse_stream
from homemaster.web.static import mount_web_static

logger = logging.getLogger(__name__)


def _log_mode_broadcast_failure(future: Any) -> None:
    """Surface a failed thread-safe mode broadcast instead of dropping it."""

    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        logger.warning("web_mode_broadcast_failed", exc_info=exc)


def create_web_app(
    *,
    application: Any,
    confirmation_handler: WebConfirmationHandler,
    memory_management_service: MemoryManagementService | None = None,
    alfworld_compile_jobs: Any | None = None,
    permission_store: Any | None = None,
    config: Any | None = None,
    environment: str | None = None,
    provider_config_service: ProviderConfigService | None = None,
) -> FastAPI:
    """Build a Web adapter around one long-lived ApplicationRuntime."""

    run_registry = WebRunRegistry()
    pending_questions = PendingQuestionRegistry()
    environment_label = (
        environment if isinstance(environment, str) and environment.strip() else "home"
    )
    hub = WebEventHub(
        application.event_bus,
        run_registry,
        WebEventProjection(include_thinking=True),
    )
    close_lock = asyncio.Lock()
    closed = False
    # Loop the hub lives on, captured once lifespan startup runs on it; the
    # plan-mode listener schedules publishes here so tool-driven set() calls
    # stay safe from any thread (mirrors the remote-shell coroutine bridge).
    hub_loop: asyncio.AbstractEventLoop | None = None

    def _on_plan_mode_change(session_id: str, enabled: bool) -> None:
        """Broadcast a plan-mode transition once over the session event stream."""

        loop = hub_loop
        if loop is None or not loop.is_running():
            return
        publish = hub.publish(
            WebEvent(
                type="session.mode_changed",
                session_id=session_id,
                run_id="",
                request_id="",
                payload={"ui_mode": "plan" if enabled else "act"},
            )
        )
        try:
            future = asyncio.run_coroutine_threadsafe(publish, loop)
        except RuntimeError:
            publish.close()
            return
        future.add_done_callback(_log_mode_broadcast_failure)

    plan_mode_service = _application_services(application).get("plan_mode")
    add_mode_listener = getattr(plan_mode_service, "add_listener", None)
    if callable(add_mode_listener):
        add_mode_listener(_on_plan_mode_change)

    async def close_resources() -> None:
        nonlocal closed, hub_loop
        async with close_lock:
            if closed:
                return
            await confirmation_handler.aclose()
            await run_registry.aclose()
            await pending_questions.aclose()
            await hub.aclose()
            # Web/thin-client sessions never open an ApplicationSession, so
            # session_close() hooks never fire for them — enqueue finalization
            # for every session that had activity before the application-owned
            # memory queue is sealed by application.aclose().
            session_end = getattr(application, "session_end_handler", None)
            if session_end is not None:
                for session_runtime in application.session_manager.sessions:
                    session_id = session_runtime.session.session_id
                    try:
                        receipt = session_end(session_id, "server_shutdown")
                    except Exception:
                        logger.warning(
                            "session finalization enqueue failed for %s",
                            session_id,
                            exc_info=True,
                        )
                    else:
                        if receipt is None:
                            logger.warning(
                                "session finalization was not admitted for %s "
                                "(memory queue not ready)",
                                session_id,
                            )
            await application.aclose()
            hub_loop = None
            closed = True

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        nonlocal hub_loop
        try:
            await application.start()
            await hub.start()
            hub_loop = asyncio.get_running_loop()
            yield
        finally:
            await close_resources()

    app = FastAPI(title="HomeMaster Web Console", lifespan=lifespan)
    app.state.application = application
    app.state.confirmation_handler = confirmation_handler
    app.state.run_registry = run_registry
    app.state.event_hub = hub
    app.state.pending_questions = pending_questions
    app.state.memory_management_service = memory_management_service
    app.state.alfworld_compile_jobs = alfworld_compile_jobs
    app.state.config = config
    app.state.environment = environment_label
    app.state.provider_config_service = provider_config_service or ProviderConfigService(
        runtime_config=config
    )
    app.state.aclose = close_resources

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: object, exc: RequestValidationError) -> JSONResponse:
        del request, exc
        return _error(
            422,
            "invalid_request",
            "Request validation failed.",
            retryable=False,
        )

    ctx = WebAppContext(
        application=application,
        confirmation_handler=confirmation_handler,
        run_registry=run_registry,
        pending_questions=pending_questions,
        hub=hub,
        config=config,
        environment_label=environment_label,
        permission_store=permission_store,
    )
    register_routes(app, ctx)

    # The retired ``/api/events`` WebSocket and its ``/{path:path}`` reject
    # catch-all are gone; SSE on ``/api/sessions/{id}/stream`` is the single
    # live event channel. WebSocket upgrade requests now hit no route and get
    # a plain HTTP 403/404 from the ASGI layer instead of a 4404 close.
    mount_sse_stream(
        app,
        hub=hub,
        session_ids=lambda: _session_ids(application.session_manager),
        error=_error,
    )

    mount_web_static(app)
    return app


__all__ = ["create_web_app"]
