from __future__ import annotations

from pathlib import Path

import pytest

from homemaster.application.contracts import RunRequest, RunResult, RunStatus
from homemaster.browser.application import BrowserApplication


class _Application:
    def __init__(self) -> None:
        self.requests: list[RunRequest] = []

    async def run(self, request: RunRequest) -> RunResult:
        self.requests.append(request)
        return RunResult(
            run_id="browser-run",
            session_id=request.session_id or "missing",
            status=RunStatus.REPLIED,
            final_reply="done",
        )

    def cancel(self, _session_id: str) -> bool:
        return True


@pytest.mark.asyncio
async def test_browser_application_enriches_requests_independent_of_input_channel() -> None:
    application = _Application()
    factory = object()
    wrapped = BrowserApplication(application, factory)
    request = RunRequest(
        text="Execute the ticket.",
        session_id="shared-session",
        dependencies={"input_source": Path("/tmp/ticket.json")},
        metadata={"input_channel": "cli"},
    )

    result = await wrapped.run(request)
    enriched = application.requests[0]

    assert result.status is RunStatus.REPLIED
    assert enriched.profile == "browser"
    assert enriched.text == request.text
    assert enriched.session_id == request.session_id
    assert enriched.dependencies["browser_session_factory"] is factory
    assert enriched.dependencies["input_source"] == request.dependencies["input_source"]
    assert enriched.metadata == request.metadata
    assert enriched.run_policy.max_tool_iterations is None
