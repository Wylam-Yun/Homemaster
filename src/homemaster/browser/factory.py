"""Run-scoped BrowserSession construction."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urldefrag

from homemaster.browser.contracts import BrowserSession, audit_browser_session_implementation


@runtime_checkable
class BrowserSessionFactory(Protocol):
    async def create(self, *, run_id: str) -> BrowserSession: ...


class PlaywrightBrowserSessionFactory:
    def __init__(
        self,
        *,
        policy: object,
        video_root: Path,
        headless: bool = True,
        start_url: str | None = None,
    ) -> None:
        self._policy = policy
        self._video_root = Path(video_root)
        self._headless = headless
        self._start_url = start_url

    async def create(self, *, run_id: str) -> BrowserSession:
        from homemaster.browser.playwright_session import PlaywrightBrowserSession

        session = PlaywrightBrowserSession(
            session_id=run_id,
            policy=self._policy,
            video_dir=self._video_root / run_id,
            headless=self._headless,
        )
        try:
            audit_browser_session_implementation(session)
            await session.start()
            if self._start_url is not None:
                receipt = await session.navigate(self._start_url)
                final_url = str(receipt.get("final_url", ""))
                expected = urldefrag(self._start_url)[0].rstrip("/")
                if urldefrag(final_url)[0].rstrip("/") != expected:
                    raise RuntimeError(
                        f"start URL redirected away from configured page: {final_url}"
                    )
            return session
        except BaseException:
            await session.aclose()
            raise


__all__ = ["BrowserSessionFactory", "PlaywrightBrowserSessionFactory"]
