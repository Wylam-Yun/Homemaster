"""Run-scoped BrowserSession construction."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from homemaster.browser.contracts import BrowserSession, audit_browser_session_implementation


@runtime_checkable
class BrowserSessionFactory(Protocol):
    async def create(self, *, run_id: str) -> BrowserSession: ...


class PlaywrightBrowserSessionFactory:
    def __init__(self, *, policy: object, video_root: Path, headless: bool = True) -> None:
        self._policy = policy
        self._video_root = Path(video_root)
        self._headless = headless

    async def create(self, *, run_id: str) -> BrowserSession:
        from homemaster.browser.playwright_session import PlaywrightBrowserSession

        session = PlaywrightBrowserSession(
            session_id=run_id,
            policy=self._policy,
            video_dir=self._video_root / run_id,
            headless=self._headless,
        )
        audit_browser_session_implementation(session)
        await session.start()
        return session


__all__ = ["BrowserSessionFactory", "PlaywrightBrowserSessionFactory"]
