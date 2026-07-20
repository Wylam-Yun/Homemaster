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
    _safe_provider_identity,
    _update_attempt_manifest,
    new_coworker_run_id,
)
from homemaster.benchmarking.coworker_demo.types import (
    CoworkerOutcome,
    ValidTicketRoute,
)
from homemaster.cli.coworker_router import route_coworker_ticket
from homemaster.config import ProviderProfileConfig
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


def test_provider_identity_contains_no_endpoint_path_or_secret() -> None:
    provider = ProviderProfileConfig(
        name="Mimo",
        api_format="anthropic",
        transport="raw_http",
        base_url="https://token-plan-cn.xiaomimimo.com/v1/messages",
        model="mimo-v2.5",
        api_keys=["actual-secret"],
    )
    identity = _safe_provider_identity(provider, provider_config_override=False)

    assert identity["provider"] == "Mimo"
    assert identity["model"] == "mimo-v2.5"
    assert identity["scheme"] == "https"
    assert identity["host"] == "token-plan-cn.xiaomimimo.com"
    assert "v1/messages" not in str(identity)
    assert "actual-secret" not in str(identity)
    assert identity["provider_config_override"] is False
    assert identity["created_at_utc"].endswith("+00:00")
    assert len(identity["config_fingerprint_sha256"]) == 64


def test_attempt_manifest_is_created_and_updated_without_secret_values(tmp_path) -> None:
    first = _update_attempt_manifest(
        tmp_path,
        schema_version=1,
        run_id="run-a",
        run_root=str(tmp_path),
        status="allocated",
        secret="must-not-be-used",
    )
    second = _update_attempt_manifest(tmp_path, status="failed", error_type="TimeoutError")

    assert first["status"] == "allocated"
    assert first["run_root"] == str(tmp_path)
    assert second["status"] == "failed"
    encoded = (tmp_path / "attempt_manifest.json").read_text(encoding="utf-8")
    assert "must-not-be-used" not in encoded
    assert json.loads(encoded)["error_type"] == "TimeoutError"
