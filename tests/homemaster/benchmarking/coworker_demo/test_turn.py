from __future__ import annotations

from homemaster.benchmarking.coworker_demo.budget import CoworkerBudget
from homemaster.benchmarking.coworker_demo.turn import DeadlineAwareTransport, new_coworker_run_id
from homemaster.benchmarking.coworker_demo.types import CoworkerOutcome


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
