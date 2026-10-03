"""Provider-only projection for tool availability in model context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from homemaster.agent.messages import Message


def project_model_tool_context(
    messages: Sequence[Message],
    *,
    tools: Sequence[Mapping[str, object]] | None,
) -> list[Message]:
    """Keep V3.1 semantic targets and stable refs in provider-visible history."""

    del tools
    return list(messages)


__all__ = [
    "project_model_tool_context",
]
