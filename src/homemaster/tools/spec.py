"""ToolSpec — declares tool metadata and executor reference.

Responsibility boundary:
  ToolSpec:     declares metadata; generates compact Mimo manifest; does NOT execute
  Dispatcher:   validates + invokes executor; does NOT mutate AgentState
  ToolRegistry: stores ToolSpec by name; returns selectable manifests
  ToolResult:   typed execution outcome; no state_patch
  StateUpdater: sole component that transforms AgentState
  EventSink:    append-only redacted events
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from homemaster.agent.state import AgentState
from homemaster.tools.results import ToolResult


class ToolExecutor(Protocol):
    """Protocol for tool execution."""

    def __call__(
        self,
        *,
        arguments: dict[str, Any],
        state: AgentState,
        settings: Any,  # RuntimeSettings — forward reference to avoid circular import
    ) -> ToolResult: ...


class ToolSpec(BaseModel):
    """Declarative tool specification.

    Holds metadata for Mimo manifest generation and runtime validation.
    The executor reference is used by Dispatcher only — ToolSpec itself
    never invokes it.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    executor_mode: Literal[
        "live_llm",
        "live_embedding",
        "programmatic",
        "simulated_skill",
        "simulated_verification",
        "not_integrated",
        "internal",
    ]
    selectable_by_model: bool = True
    requires_verification: bool = False
    state_effects: list[str] = Field(default_factory=list)
    failure_semantics: str = "raise"
    executor: Callable[..., Any] | None = None

    def to_mimo_manifest(self) -> dict[str, Any]:
        """Compact manifest for Mimo tool selection.

        Excludes executor, output_schema, state_effects, failure_semantics.
        Includes executor_mode for runtime-side validation.
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "executor_mode": self.executor_mode,
        }
