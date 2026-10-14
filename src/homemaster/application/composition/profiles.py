"""Profile resolution for the public application composition boundary."""

from __future__ import annotations

from typing import Literal

from homemaster.config import HomeMasterConfig

_BENCHMARK_PROFILES = {"alfworld", "robocasa"}


def resolve_tool_environment(
    config: HomeMasterConfig,
    requested: Literal["local_robot", "alfworld", "browser", "robocasa"] | None,
) -> Literal["local_robot", "alfworld", "browser", "robocasa"] | None:
    """Enable configured browser capabilities before a channel is attached.

    Benchmark profiles own their environment explicitly and must pass through
    untouched even when a browser gateway is configured.
    """

    if (
        requested not in _BENCHMARK_PROFILES
        and config.browser_gateway.start_url is not None
    ):
        return "browser"
    return requested


__all__ = ["resolve_tool_environment"]
