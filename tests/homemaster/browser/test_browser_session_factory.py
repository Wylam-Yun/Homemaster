from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.browser.factory import PlaywrightBrowserSessionFactory
from homemaster.browser.policy import BrowserPolicy


class _Session:
    def __init__(self, *, final_url: str | None = None, fail: bool = False) -> None:
        self.final_url = final_url
        self.fail = fail
        self.started = False
        self.closed = False
        self.navigated: list[str] = []

    async def start(self) -> None:
        self.started = True

    async def navigate(self, url: str):
        self.navigated.append(url)
        if self.fail:
            raise RuntimeError("navigation failed")
        return {"final_url": self.final_url or url}

    async def aclose(self) -> None:
        self.closed = True

    async def inspect(self, filters):  # pragma: no cover - interface audit only
        del filters

    async def fill(self, snapshot_id, element_id, value):  # pragma: no cover
        del snapshot_id, element_id, value

    async def select(self, snapshot_id, element_id, option):  # pragma: no cover
        del snapshot_id, element_id, option

    async def check(self, snapshot_id, element_id):  # pragma: no cover
        del snapshot_id, element_id

    async def uncheck(self, snapshot_id, element_id):  # pragma: no cover
        del snapshot_id, element_id

    async def click(self, snapshot_id, element_id):  # pragma: no cover
        del snapshot_id, element_id

    async def backfill(self, snapshot_id, element_id):  # pragma: no cover
        del snapshot_id, element_id

    async def wait(self, condition):  # pragma: no cover
        del condition

    async def screenshot(self):  # pragma: no cover
        return b""


@pytest.mark.asyncio
async def test_factory_opens_exact_start_url_before_returning(monkeypatch, tmp_path: Path) -> None:
    session = _Session()
    monkeypatch.setattr(
        "homemaster.browser.playwright_session.PlaywrightBrowserSession",
        lambda **_kwargs: session,
    )
    start_url = "http://example.test/dashboard/automation"
    factory = PlaywrightBrowserSessionFactory(
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_root=tmp_path,
        start_url=start_url,
    )

    assert await factory.create(run_id="run-1") is session
    assert session.started is True
    assert session.navigated == [start_url]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "error"),
    [
        (_Session(fail=True), "navigation failed"),
        (_Session(final_url="http://example.test/user/login"), "start URL redirected"),
    ],
)
async def test_factory_closes_session_when_start_page_is_unavailable(
    monkeypatch, tmp_path: Path, session: _Session, error: str
) -> None:
    monkeypatch.setattr(
        "homemaster.browser.playwright_session.PlaywrightBrowserSession",
        lambda **_kwargs: session,
    )
    factory = PlaywrightBrowserSessionFactory(
        policy=BrowserPolicy(allowed_origins=("http://example.test",)),
        video_root=tmp_path,
        start_url="http://example.test/dashboard/automation",
    )

    with pytest.raises(Exception, match=error):
        await factory.create(run_id="run-1")
    assert session.closed is True
