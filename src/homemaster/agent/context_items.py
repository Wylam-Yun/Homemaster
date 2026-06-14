"""ContextItem — typed context unit for the V1.5 context assembler."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from homemaster.agent.messages import Message
from homemaster.compat import StrEnum


class ContextPriority(StrEnum):
    REQUIRED = "required"
    IMPORTANT = "important"
    AUXILIARY = "auxiliary"
    TRACE_ONLY = "trace_only"


class ContextFreshness(StrEnum):
    CURRENT = "current"
    RECENT = "recent"
    OLD = "old"
    ARCHIVED = "archived"


class ContextPlacement(StrEnum):
    SYSTEM_PROMPT = "system_prompt"
    CONTEXT_PRELUDE = "context_prelude"
    CONVERSATION = "conversation"
    TOOL_SCHEMA = "tool_schema"
    TRACE_ONLY = "trace_only"


class RenderMode(StrEnum):
    FULL = "full"
    COMPACT = "compact"
    SUMMARY = "summary"
    POINTER = "pointer"


RenderedContext = str | list[Message]


@dataclass(frozen=True)
class ContextItem:
    id: str
    kind: str
    priority: ContextPriority
    freshness: ContextFreshness
    placement: ContextPlacement
    token_estimate: int
    render: Callable[[RenderMode], RenderedContext]
    group_id: str | None = None
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    mode: RenderMode = RenderMode.FULL
