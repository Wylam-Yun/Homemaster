from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from homemaster.application.contracts import RunRequest, RunResult, RunStatus
from homemaster.gateway.alfworld import (
    AlfworldGatewayApplication,
    AlfworldSessionOwner,
)


class _Application:
    def __init__(self) -> None:
        self.requests: list[RunRequest] = []
        self.close_count = 0

    async def run(self, request: RunRequest) -> RunResult:
        self.requests.append(request)
        await asyncio.sleep(0)
        return RunResult(
            run_id=f"run-{len(self.requests)}",
            session_id=request.session_id or "missing",
            status=RunStatus.REPLIED,
            final_reply="done",
        )

    def cancel(self, _session_id: str) -> bool:
        return True

    async def aclose(self) -> None:
        self.close_count += 1


def _binding():
    adapter = SimpleNamespace(backend_id="alfworld:test")
    translator = object()
    terminal_owner = object()
    return SimpleNamespace(
        adapter=adapter,
        dependencies={
            "alfworld_translator": translator,
            "external_terminal_owner": terminal_owner,
        },
    )


@pytest.mark.asyncio
async def test_session_owner_atomically_allows_exactly_one_distinct_session() -> None:
    application = _Application()
    owner = AlfworldSessionOwner()
    wrapped = AlfworldGatewayApplication(application, owner, _binding())

    results = await asyncio.gather(
        wrapped.run(RunRequest(text="one", session_id="session-one")),
        wrapped.run(RunRequest(text="two", session_id="session-two")),
    )

    assert len(application.requests) == 1
    assert sum(result.status == RunStatus.REPLIED for result in results) == 1
    assert sum(result.error_code == "alfworld_session_busy" for result in results) == 1
    assert application.requests[0].session_id == owner.session_id


@pytest.mark.asyncio
async def test_session_owner_retains_owner_allows_resume_and_seals_on_close() -> None:
    application = _Application()
    owner = AlfworldSessionOwner()
    wrapped = AlfworldGatewayApplication(application, owner, _binding())

    first = await wrapped.run(RunRequest(text="first", session_id="same"))
    resumed = await wrapped.run(RunRequest(text="resume", session_id="same", resume=True))
    await wrapped.seal()
    after_close = await wrapped.run(RunRequest(text="late", session_id="same"))

    assert first.status == resumed.status == RunStatus.REPLIED
    assert after_close.error_code == "alfworld_session_busy"
    assert [request.session_id for request in application.requests] == ["same", "same"]


@pytest.mark.asyncio
async def test_application_close_seals_new_runs_before_delegating_cleanup() -> None:
    application = _Application()
    owner = AlfworldSessionOwner()
    wrapped = AlfworldGatewayApplication(application, owner, _binding())

    await wrapped.aclose()
    result = await wrapped.run(RunRequest(text="late", session_id="session"))

    assert application.close_count == 1
    assert result.error_code == "alfworld_session_busy"
    assert application.requests == []


@pytest.mark.asyncio
async def test_application_binding_preserves_gateway_authority_and_binds_adapter() -> None:
    application = _Application()
    binding = _binding()
    wrapped = AlfworldGatewayApplication(
        application,
        AlfworldSessionOwner(),
        binding,
    )
    request = RunRequest(
        text="do the task",
        session_id="gateway-session",
        dependencies={"channel_attachments": ()},
        metadata={"gateway_generation": 7},
    )

    result = await wrapped.run(request)
    enriched = application.requests[0]

    assert result.status == RunStatus.REPLIED
    assert enriched.profile == "alfworld"
    assert enriched.borrowed_environment is binding.adapter
    assert (
        enriched.dependencies["alfworld_translator"] is binding.dependencies["alfworld_translator"]
    )
    assert (
        enriched.dependencies["external_terminal_owner"]
        is binding.dependencies["external_terminal_owner"]
    )
    assert enriched.dependencies["channel_attachments"] == ()
    assert enriched.session_id == request.session_id
    assert enriched.permission_subject is request.permission_subject
    assert enriched.metadata == request.metadata
