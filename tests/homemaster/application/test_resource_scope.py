from __future__ import annotations

import pytest

from homemaster.application import (
    ResourceBinding,
    ResourceCleanupError,
    ResourceLifetime,
    RunResourceScope,
)


class Resource:
    def __init__(self, name: str, events: list[str], *, fail_close: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail_close = fail_close
        self.close_count = 0

    async def close(self) -> None:
        self.close_count += 1
        self.events.append(f"close:{self.name}")
        if self.fail_close:
            raise RuntimeError(f"close:{self.name}")


@pytest.mark.asyncio
async def test_acquire_registers_before_start_and_rolls_back_in_reverse_order() -> None:
    events: list[str] = []
    scope = RunResourceScope()
    first = Resource("first", events)
    second = Resource("second", events)
    await scope.acquire("first", lambda: first)
    with pytest.raises(RuntimeError, match="start failed"):
        await scope.acquire(
            "second",
            lambda: second,
            start=lambda _resource: (_ for _ in ()).throw(RuntimeError("start failed")),
        )
    assert events == ["close:second", "close:first"]
    assert first.close_count == second.close_count == 1
    assert scope.closed


@pytest.mark.asyncio
async def test_factory_failure_rolls_back_previous_resources_only() -> None:
    events: list[str] = []
    scope = RunResourceScope()
    resource = Resource("first", events)
    await scope.acquire("first", lambda: resource)
    with pytest.raises(RuntimeError, match="factory failed"):
        await scope.acquire(
            "second",
            lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
        )
    assert events == ["close:first"]


@pytest.mark.asyncio
async def test_close_aggregates_errors_and_borrowed_is_never_closed() -> None:
    events: list[str] = []
    scope = RunResourceScope()
    failing = Resource("failing", events, fail_close=True)
    healthy = Resource("healthy", events)
    borrowed = Resource("borrowed", events)
    await scope.acquire("failing", lambda: failing)
    await scope.acquire("healthy", lambda: healthy)
    scope.bind(
        ResourceBinding.borrowed(
            "borrowed",
            borrowed,
            lifetime=ResourceLifetime.RUN,
        )
    )
    with pytest.raises(ResourceCleanupError) as error:
        await scope.aclose()
    assert len(error.value.errors) == 1
    assert events == ["close:healthy", "close:failing"]
    assert borrowed.close_count == 0
    await scope.aclose()
    assert healthy.close_count == failing.close_count == 1


@pytest.mark.asyncio
async def test_primary_error_is_not_replaced_by_cleanup_error() -> None:
    scope = RunResourceScope()
    resource = Resource("bad", [], fail_close=True)
    with pytest.raises(ValueError, match="primary") as error:
        async with scope:
            scope.bind(
                ResourceBinding.owned(
                    "bad",
                    resource,
                    lifetime=ResourceLifetime.RUN,
                )
            )
            raise ValueError("primary")
    assert isinstance(getattr(error.value, "cleanup_error", None), ResourceCleanupError)


def test_run_request_rejects_owned_environment_and_freezes_metadata() -> None:
    from homemaster.application import RunPolicy, RunRequest

    with pytest.raises(ValueError, match="environment must be borrowed"):
        RunRequest(
            text="hello",
            environment=ResourceBinding.owned("env", object()),
        )
    request = RunRequest(
        text="hello",
        enabled_tool_ids=("home.observe.v1",),
        run_policy=RunPolicy(max_turns=2),
        metadata={"nested": {"items": [1, 2]}},
    )
    with pytest.raises(TypeError):
        request.metadata["new"] = "value"  # type: ignore[index]
    assert request.metadata_dict() == {"nested": {"items": [1, 2]}}
