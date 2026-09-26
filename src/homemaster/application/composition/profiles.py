"""Profile resolution for the public application composition boundary."""

from __future__ import annotations

from typing import Literal

from homemaster.config import HomeMasterConfig


def resolve_tool_environment(
    config: HomeMasterConfig,
    requested: Literal["local_robot", "alfworld", "browser"] | None,
) -> Literal["local_robot", "alfworld", "browser"] | None:
    """Enable configured browser capabilities before a channel is attached."""

    if requested != "alfworld" and config.browser_gateway.start_url is not None:
        return "browser"
    return requested


__all__ = ["resolve_tool_environment"]
