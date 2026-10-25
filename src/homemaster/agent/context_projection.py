"""Provider-side tool-context projection seam for model context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from homemaster.agent.messages import Message


def project_model_tool_context(
    messages: Sequence[Message],
    *,
    tools: Sequence[Mapping[str, object]] | None,
) -> list[Message]:
    """Return provider-visible messages unchanged (intentional pass-through).

    This is a deliberate seam, not a real projection: the V3.5/V3.7 plans
    keep a single hook between provider history collection and dispatch so
    a future tool-context projection plugs in without touching call sites.
    ``tools`` is accepted for signature stability and ignored.
    """

    del tools
    return list(messages)


__all__ = [
    "project_model_tool_context",
]
