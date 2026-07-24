"""Test-harness adaptation for unchanged OpenHarness upstream tests."""

from __future__ import annotations

import inspect

import pytest
import pytest_asyncio

from openharness.tasks.manager import shutdown_task_manager


@pytest.hookimpl(tryfirst=True)
def pytest_pycollect_makeitem(collector: pytest.Collector, name: str, obj: object) -> None:
    """Match OpenHarness's auto asyncio mode without changing its test bytes."""

    del collector, name
    if inspect.iscoroutinefunction(obj) and not any(
        mark.name == "asyncio" for mark in getattr(obj, "pytestmark", ())
    ):
        pytest.mark.asyncio(obj)
    return None


@pytest_asyncio.fixture(autouse=True)
async def _reap_upstream_task_processes():
    """Close upstream global subprocess transports before pytest closes the loop."""

    yield
    await shutdown_task_manager()
