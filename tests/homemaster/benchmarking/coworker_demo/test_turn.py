from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from homemaster.adapters.coworker_entry import CoworkerApplicationEntry, DeadlineAwareTransport
from homemaster.adapters.profiles import CoworkerObservationBackend
from homemaster.application import RunPolicy, RunRequest
from homemaster.benchmarking.coworker_demo.browser_driver import PlaywrightBrowserDriver
from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.turn import (
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
from homemaster.config import HomeMasterConfig, ProviderProfileConfig
from homemaster.events.sinks import NullEventSink
from homemaster.providers.attempts import JsonlProviderAttemptSink, ProviderAttemptRecord
from homemaster.providers.errors import LLMNetworkError
from homemaster.providers.transports import TransportDelta
from scripts.coworker_demo.preflight import _mode_0600_check


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
        self.closed = False

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

    def close(self) -> None:
        self.closed = True


class BorrowedBackend:
    backend_id = "coworker:test"

    def __init__(self) -> None:
        self.bound_run_id: str | None = None
        self.bound_generation: int | None = None
        self.closed = False

    def bind_application_run(self, run_id: str, generation: int) -> None:
        self.bound_run_id = run_id
        self.bound_generation = generation

    def close(self) -> None:
        self.closed = True


class FakePage:
    url = "about:blank"

    def goto(self, url: str, **_kwargs) -> None:
        self.url = url


class FakeBrowserClient:
    def __init__(self) -> None:
        self.runtime_events: list[dict] = []

    def state(self, _run_id: str) -> dict:
        return {"state_version": 3}

    def reserve(self, *_args, **_kwargs) -> None:
        return None

    def record_action(self, *_args, **_kwargs) -> dict:
        return {"event": {"event_id": "ev-navigate"}}

    def runtime_event(self, _run_id: str, **kwargs) -> dict:
        self.runtime_events.append(kwargs)
        return {"event": {"event_id": "ev-observe"}}


class FakeObservationDriver:
    def observe(self, _action_id: str) -> dict:
        return {
            "route": "ticket",
            "url": "http://case02.test/ticket/domain-run",
            "dom": {"title": "Locked procedure"},
            "page_state_version": 3,
            "evidence_refs": ["ev-browser-observe"],
        }


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


def test_navigate_returns_receipt_only_without_dom() -> None:
    client = FakeBrowserClient()
    driver = object.__new__(PlaywrightBrowserDriver)
    driver.run_id = "domain-run"
    driver.base_url = "http://case02.test"
    driver.timeout_ms = 1000
    driver.client = client
    driver.page = FakePage()

    receipt = driver.navigate("ticket", "navigate-1")

    assert receipt == {
        "url": "http://case02.test/ticket/domain-run",
        "route": "ticket",
        "page_state_version": 3,
        "evidence_refs": ["ev-navigate"],
    }
    assert "dom" not in receipt and "html" not in receipt


def test_observe_records_ticket_read_with_canonical_content_hash() -> None:
    client = FakeBrowserClient()
    backend = CoworkerObservationBackend(
        driver=FakeObservationDriver(),
        client=client,
        domain_run_id="domain-run",
    )
    backend.bind_application_run("application-run", 2)

    capture = backend.capture()

    expected = json.dumps(
        capture.content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert client.runtime_events == [
        {
            "action_id": "observe-0001",
            "tool_name": "observe",
            "arguments": {
                "content_sha256": hashlib.sha256(expected).hexdigest(),
                "page_state_version": 3,
            },
            "node_id": "TICKET_READ",
            "evidence_refs": ["ev-browser-observe"],
        }
    ]
    assert capture.run_id == "application-run"
    assert capture.evidence_ref == "ev-observe"


def test_configured_bundle_root_must_match_the_routed_root(tmp_path: Path) -> None:
    ticket = Path("data/coworker_demo/case_02/test_set/item_change_ticket.json").resolve()
    route = route_coworker_ticket(str(ticket))
    assert isinstance(route, ValidTicketRoute)
    assert _resolve_configured_bundle(route, route.case_root).ticket_path == ticket

    with pytest.raises(ValueError, match="does not match configured"):
        _resolve_configured_bundle(route, tmp_path)


def test_coworker_entry_retries_and_preserves_child_run_identity(tmp_path: Path) -> None:
    client = RetryClient()
    outcome = CoworkerOutcome()
    backend = BorrowedBackend()
    transport_run_ids: list[str] = []

    def transport_factory(run_id: str):
        transport_run_ids.append(run_id)
        return DeadlineAwareTransport(client, CoworkerBudget(), outcome)

    entry = CoworkerApplicationEntry(
        config=HomeMasterConfig(),
        provider_profile=ProviderProfileConfig(
            name="test",
            model="test-model",
            api_format="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="test",
        run_root=tmp_path,
        transport_factory=transport_factory,
        event_sink=NullEventSink(),
    )
    try:
        result = entry.run(
            RunRequest(
                text="hello",
                session_id="retry",
                profile="coworker",
                environment=backend,
                run_policy=RunPolicy(max_tool_iterations=1),
                dependencies={
                    "provider_attempt_sink_factory": lambda: JsonlProviderAttemptSink(
                        tmp_path / "agent/provider_attempts.jsonl"
                    )
                },
            )
        )
    finally:
        entry.close()

    assert result.status == "replied"
    assert result.final_reply == "done"
    assert client.key_indices == [0, 1]
    assert transport_run_ids == [result.run_id]
    assert backend.bound_run_id == result.run_id
    assert backend.bound_generation == 1
    assert backend.closed is False
    assert client.closed is True
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


def test_coworker_runner_has_no_model_runtime_assembly() -> None:
    path = Path("src/homemaster/benchmarking/coworker_demo/turn.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    constructed = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not {
        "AgentRuntime",
        "GenericAgentRuntime",
        "LLMClient",
        "ToolDispatcher",
        "ToolRegistry",
        "build_coworker_tool_registry",
    } & (imported | constructed)


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


def test_preflight_requires_exact_mode_0600_for_each_config(tmp_path: Path) -> None:
    path = tmp_path / "private.yaml"
    path.write_text("safe: true\n", encoding="utf-8")
    path.chmod(0o600)
    assert _mode_0600_check(path) == {"pass": True, "mode": "0o600"}

    path.chmod(0o640)
    assert _mode_0600_check(path) == {"pass": False, "mode": "0o640"}
    assert _mode_0600_check(tmp_path / "missing.yaml") == {"pass": False, "mode": None}
