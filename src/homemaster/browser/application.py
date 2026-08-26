"""Channel-independent browser execution binding."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from homemaster.application.contracts import RunRequest, RunResult
from homemaster.browser.factory import PlaywrightBrowserSessionFactory
from homemaster.browser.policy import BrowserPolicy
from homemaster.config import BrowserGatewayConfig


class BrowserApplication:
    """Add the browser profile and one run-scoped session factory to any input channel."""

    def __init__(self, application: Any, factory: object) -> None:
        self._application = application
        self._factory = factory

    def __getattr__(self, name: str) -> Any:
        return getattr(self._application, name)

    async def run(self, request: RunRequest) -> RunResult:
        dependencies = dict(request.dependencies)
        factory = self._factory
        for_run = getattr(factory, "for_run", None)
        if callable(for_run):
            factory = for_run(eval_allowed="browser.eval" in request.run_policy.capabilities)
        dependencies["browser_session_factory"] = factory
        return await self._application.run(
            replace(
                request,
                profile="browser",
                dependencies=dependencies,
                run_policy=replace(request.run_policy, max_tool_iterations=None),
            )
        )

    def cancel(self, session_id: str) -> bool:
        return self._application.cancel(session_id)


def create_browser_application(
    application: Any,
    config: BrowserGatewayConfig,
    *,
    run_dir: Path,
) -> BrowserApplication:
    start_url, allowed_origins = config.require_runtime()
    factory = PlaywrightBrowserSessionFactory(
        policy=BrowserPolicy(
            allowed_origins=allowed_origins,
            action_timeout_ms=config.action_timeout_ms,
            navigation_timeout_ms=config.navigation_timeout_ms,
            wait_timeout_ms=config.wait_timeout_ms,
        ),
        video_root=run_dir / "browser",
        headless=config.headless,
        start_url=start_url,
    )
    return BrowserApplication(application, factory)


__all__ = ["BrowserApplication", "create_browser_application"]
