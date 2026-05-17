"""ToolDispatcher — validates and invokes tool executors.

Responsibility boundary:
  Dispatcher: validates + invokes executor; does NOT mutate AgentState
  StateUpdater: sole component that transforms AgentState
"""

from __future__ import annotations

from typing import Any

from homemaster.agent.state import AgentState
from homemaster.config.runtime_settings import RuntimeSettings
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


class ToolDispatcher:
    """Validates tool call and invokes executor. Does not mutate AgentState."""

    def dispatch(
        self,
        *,
        spec: ToolSpec,
        arguments: dict[str, Any],
        state: AgentState,
        settings: RuntimeSettings,
    ) -> ToolResult:
        """Dispatch a tool call: validate, invoke executor, return result.

        Returns ToolResult on success or executor failure.
        Returns ToolResult(success=False) on validation failure.
        """
        # Validate tool is allowed by active skill (if any)
        if state.active_skills:
            allowed = self._get_allowed_tools(state)
            if allowed is not None and spec.name not in allowed:
                return ToolResult(
                    success=False,
                    tool_name=spec.name,
                    executor_mode=spec.executor_mode,
                    failure_reason=f"tool {spec.name!r} not allowed by active skill",
                )

        # Invoke executor
        if spec.executor is None:
            return ToolResult(
                success=False,
                tool_name=spec.name,
                executor_mode=spec.executor_mode,
                failure_reason=f"tool {spec.name!r} has no executor",
            )

        try:
            return spec.executor(arguments=arguments, state=state, settings=settings)
        except Exception as exc:
            return ToolResult(
                success=False,
                tool_name=spec.name,
                executor_mode=spec.executor_mode,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )

    def _get_allowed_tools(self, state: AgentState) -> set[str] | None:
        """Collect allowed_tools from loaded skill contexts."""
        allowed: set[str] = set()
        for skill_name in state.active_skills:
            ctx = state.loaded_skill_contexts.get(skill_name)
            if ctx:
                allowed.update(ctx.get("allowed_tools", []))
        return allowed if allowed else None
