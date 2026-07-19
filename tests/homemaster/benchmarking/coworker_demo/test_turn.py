from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.agent.session import AgentSession
from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.turn import (
    DeadlineAwareTransport,
    _make_coworker_runtime,
    _resolve_configured_bundle,
    new_coworker_run_id,
)
from homemaster.benchmarking.coworker_demo.types import (
    CoworkerOutcome,
    ValidTicketRoute,
)
from homemaster.cli.coworker_router import route_coworker_ticket
from homemaster.providers.attempts import ProviderAttemptRecord
from homemaster.providers.errors import LLMNetworkError
from homemaster.providers.transports import TransportDelta


class FakeClient:
    token_estimator = object()

    def stream(self, *_args, **_kwargs):
        yield "one"
        yield "two"

    def complete(self, *_args, **_kwargs):
        return "done"


class RetryClient:
    token_estimator = object()

    def __init__(self) -> None:
        self.key_indices: list[int] = []

    def stream(
        self,
        *_args,
        attempt_sink,
        model_attempt_id: str,
        provider_key_index: int,
        **_kwargs,
    ):
        self.key_indices.append(provider_key_index)
        error = provider_key_index == 0
        attempt_sink.record_attempt(
            ProviderAttemptRecord(
                model_attempt_id=model_attempt_id,
                request_sha256="a" * 64,
                outbound_images=(),
                stripped_images=False,
                response_completed=not error,
                error_type="network_error" if error else None,
                cause_code="transient_network" if error else None,
            )
        )
        if error:
            raise LLMNetworkError(
                error_type="network_error",
                message="connection reset",
                cause_code="transient_network",
            )
        yield TransportDelta(type="transport.delta", text_delta="done")
        yield TransportDelta(type="transport.delta", finish_reason="stop")


def test_run_ids_are_unique_and_prefixed() -> None:
    first = new_coworker_run_id()
    second = new_coworker_run_id()
    assert first.startswith("coworker-")
    assert first != second


def test_deadline_transport_checks_shared_budget() -> None:
    outcome = CoworkerOutcome()
    wrapper = DeadlineAwareTransport(FakeClient(), CoworkerBudget(), outcome)
    assert list(wrapper.stream([])) == ["one", "two"]
    assert wrapper.complete([]) == "done"


def test_configured_bundle_root_must_match_the_routed_root(tmp_path: Path) -> None:
    ticket = Path("data/coworker_demo/case_02/test_set/item_change_ticket.json").resolve()
    route = route_coworker_ticket(str(ticket))
    assert isinstance(route, ValidTicketRoute)
    assert _resolve_configured_bundle(route, route.case_root).ticket_path == ticket

    with pytest.raises(ValueError, match="does not match configured"):
        _resolve_configured_bundle(route, tmp_path)


def test_coworker_runtime_retries_one_transient_provider_failure(tmp_path: Path) -> None:
    client = RetryClient()
    outcome = CoworkerOutcome()
    runtime = _make_coworker_runtime(
        transport=DeadlineAwareTransport(client, CoworkerBudget(), outcome),
        dispatcher=object(),
        max_tool_iterations=1,
        stop_condition=None,
        context_assembler=None,
        system_prompt="",
        run_root=tmp_path,
    )

    result = runtime.run(
        AgentSession(session_id="retry"),
        "hello",
        tools=[],
        run_id="retry",
    )

    assert result.status == "replied"
    assert result.final_reply == "done"
    assert client.key_indices == [0, 1]
    attempts = [
        json.loads(line)
        for line in (tmp_path / "agent/provider_attempts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [attempt["response_completed"] for attempt in attempts] == [False, True]


def test_playwright_wait_arguments_are_keyword_only() -> None:
    source = Path("src/homemaster/benchmarking/coworker_demo/browser_driver.py").read_text(
        encoding="utf-8"
    )
    assert "arg=job_id" in source
    assert "arg=selector" in source
