from __future__ import annotations

import json

import httpx
import pytest

from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.environment_client import (
    EnvironmentClient,
    EnvironmentClientError,
)
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome


def test_client_returns_typed_state_and_rejects_business_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state"):
            return httpx.Response(200, json={"success": True, "state": {"state_version": 4}})
        return httpx.Response(
            409,
            json={"success": False, "error_code": "stale_state_version", "message": "stale"},
        )

    client = EnvironmentClient("http://case02.test", transport=httpx.MockTransport(handler))
    assert client.state("run")["state_version"] == 4
    with pytest.raises(EnvironmentClientError, match="stale_state_version"):
        client.reserve("run", "action", "browser_click", 3)
    client.close()


def test_client_rejects_non_json_response() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(502, text="bad gateway"))
    client = EnvironmentClient("http://case02.test", transport=transport)
    with pytest.raises(EnvironmentClientError, match="non-JSON"):
        client.health()
    client.close()


def test_fixed_cleanup_endpoints_remain_available_after_terminal_outcome() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"success": True, "summary": {}})
    )
    outcome = CoworkerOutcome()
    outcome.mark("complete")
    client = EnvironmentClient(
        "http://case02.test",
        transport=transport,
        budget=CoworkerBudget(),
        outcome=outcome,
    )
    assert client.finalize("run")["success"] is True
    assert client.stop_recording("run")["success"] is True
    client.close()


def test_presentation_event_posts_payload_without_consuming_budget() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"success": True, "event": {"sequence": 1}})

    outcome = CoworkerOutcome()
    outcome.mark("complete")
    client = EnvironmentClient(
        "http://case02.test",
        transport=httpx.MockTransport(handler),
        budget=CoworkerBudget(),
        outcome=outcome,
    )
    payload = {"runtime_event_type": "tool.call_started", "status": "running"}

    response = client.presentation_event("run-1", payload)

    assert response["event"]["sequence"] == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/runs/run-1/presentation-events"
    assert json.loads(requests[0].content) == payload
    client.close()
