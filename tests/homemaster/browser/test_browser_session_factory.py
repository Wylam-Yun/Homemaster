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

    async def navigate(self, url: str, **kwargs: object):
        del kwargs
        self.navigated.append(url)
        if self.fail:
            raise RuntimeError("navigation failed")
        return {"final_url": self.final_url or url}

    async def aclose(self) -> None:
        self.closed = True

    async def inspect(self, filters):  # pragma: no cover - interface audit only
        del filters

    async def history(self, action, **kwargs):  # pragma: no cover
        del action, kwargs

    async def find(self, query):  # pragma: no cover
        del query

    async def read(self, query):  # pragma: no cover
        del query

    async def extract(self, query):  # pragma: no cover
        del query

    async def fill(self, target, value):  # pragma: no cover
        del target, value

    async def type(self, target, text, **kwargs):  # pragma: no cover
        del target, text, kwargs

    async def select(self, target, option, **kwargs):  # pragma: no cover
        del target, option, kwargs

    async def check(self, target):  # pragma: no cover
        del target

    async def uncheck(self, target):  # pragma: no cover
        del target

    async def click(self, target, **kwargs):  # pragma: no cover
        del target, kwargs

    async def hover(self, target, **kwargs):  # pragma: no cover
        del target, kwargs

    async def focus(self, target):  # pragma: no cover
        del target

    async def press(self, key, target=None, **kwargs):  # pragma: no cover
        del key, target, kwargs

    async def scroll(self, query):  # pragma: no cover
        del query

    async def upload(self, target, artifact_refs):  # pragma: no cover
        del target, artifact_refs

    async def drag(self, source, destination, **kwargs):  # pragma: no cover
        del source, destination, kwargs

    async def backfill(self, target, **kwargs):  # pragma: no cover
        del target, kwargs

    async def tabs(self, query):  # pragma: no cover
        del query

    async def dialog(self, query):  # pragma: no cover
        del query

    async def network(self, query):  # pragma: no cover
        del query

    async def download(self, query):  # pragma: no cover
        del query

    async def wait(self, condition):  # pragma: no cover
        del condition

    async def screenshot(self, **kwargs):  # pragma: no cover
        del kwargs
        return b""

    async def eval(self, query):  # pragma: no cover
        del query

    async def analyze(self, query):  # pragma: no cover
        del query


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
