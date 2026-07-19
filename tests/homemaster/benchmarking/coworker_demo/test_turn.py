from __future__ import annotations

import json

from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.turn import (
    DeadlineAwareTransport,
    _safe_provider_identity,
    _update_attempt_manifest,
    new_coworker_run_id,
)
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome
from homemaster.config import ProviderProfileConfig


class FakeClient:
    token_estimator = object()

    def stream(self, *_args, **_kwargs):
        yield "one"
        yield "two"

    def complete(self, *_args, **_kwargs):
        return "done"


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


def test_playwright_wait_arguments_are_keyword_only() -> None:
    from pathlib import Path

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
