"""ToolDispatcher — validates tool calls and invokes tool executors."""

from __future__ import annotations

import json
from typing import Any

from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage
from homemaster.agent.normalized import RunContext
from homemaster.tools.results import ToolResult
from homemaster.tools.spec import ToolSpec


class ToolDispatcher:
    """Validates tool call and invokes executor. Does not mutate AgentState."""

    def __init__(self, event_sink: Any = None) -> None:
        self._event_sink = event_sink
        self._specs: dict[str, ToolSpec] = {}
        self._run_context: RunContext | None = None

    def register(self, spec: ToolSpec) -> None:
        """Register a tool spec for dispatch_many lookups."""
        self._specs[spec.name] = spec

    def set_run_context(self, run_context: RunContext) -> None:
        """Set the RunContext for __call__-based dispatch."""
        self._run_context = run_context

    def __call__(self, name: str, arguments: dict[str, Any]) -> ToolResultMessage:
        """Dispatch a single tool call by name.

        Uses the RunContext set via set_run_context().
        """
        if self._run_context is None:
            raise RuntimeError("ToolDispatcher.set_run_context() must be called before __call__")
        tc = ToolCall(id=f"call_{name}", name=name, arguments=arguments)
        results = self.dispatch(tool_calls=[tc], run_context=self._run_context)
        return results[0]

    def dispatch(
        self,
        *,
        tool_calls: list[ToolCall],
        run_context: RunContext,
    ) -> list[ToolResultMessage]:
        """Dispatch a list of tool calls and return ToolResultMessages.

        Looks up ToolSpec by name, invokes executor with run_context,
        and maps results to ToolResultMessages preserving tool_call_id.
        """
        results: list[ToolResultMessage] = []
        token = run_context.cancellation_token
        for index, tc in enumerate(tool_calls):
            if getattr(token, "cancelled", False):
                for pending in tool_calls[index:]:
                    results.append(
                        ToolResultMessage(
                            tool_call_id=pending.id,
                            name=pending.name,
                            content=[
                                ContentBlock(text='{"error": "cancelled before tool execution"}')
                            ],
                            is_error=True,
                            data={
                                "success": False,
                                "error": "cancelled before tool execution",
                                "cancelled": True,
                            },
                        )
                    )
                break
            terminal_result = _terminal_alfworld_result(tc, run_context)
            if terminal_result is not None:
                results.append(terminal_result)
                continue
            spec = self._specs.get(tc.name)
            if spec is None:
                results.append(
                    ToolResultMessage(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=[ContentBlock(text=f'{{"error": "unknown tool: {tc.name}"}}')],
                        is_error=True,
                    )
                )
                continue

            # Validate required arguments
            schema = spec.input_schema
            required = schema.get("required", [])
            missing = [f for f in required if f not in tc.arguments]
            if missing:
                error_text = f'{{"error": "missing required arguments: {missing}"}}'
                results.append(
                    ToolResultMessage(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=[ContentBlock(text=error_text)],
                        is_error=True,
                    )
                )
                continue

            if spec.executor is None:
                results.append(
                    ToolResultMessage(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=[
                            ContentBlock(text=f'{{"error": "tool {tc.name} has no executor"}}')
                        ],
                        is_error=True,
                    )
                )
                continue

            try:
                if hasattr(token, "enter_tool"):
                    token.enter_tool()
                had_previous_tool_call_id = "current_tool_call_id" in run_context.deps
                previous_tool_call_id = run_context.deps.get("current_tool_call_id")
                run_context.deps["current_tool_call_id"] = tc.id
                try:
                    tool_result = spec.executor(
                        arguments=tc.arguments,
                        run_context=run_context,
                    )
                finally:
                    if had_previous_tool_call_id:
                        run_context.deps["current_tool_call_id"] = previous_tool_call_id
                    else:
                        run_context.deps.pop("current_tool_call_id", None)
            except Exception as exc:
                tool_result = ToolResult(
                    success=False,
                    tool_name=tc.name,
                    executor_mode=spec.executor_mode,
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            finally:
                if hasattr(token, "exit_tool"):
                    token.exit_tool()

            terminal_data = getattr(tool_result, "data", None)
            if isinstance(terminal_data, dict):
                _sync_alfworld_outcome(
                    run_context=run_context,
                    tool_call_id=tc.id,
                    data=terminal_data,
                )

            if isinstance(tool_result, ToolResult):
                if tool_result.success:
                    payload = dict(tool_result.data) if tool_result.data else {"success": True}
                    payload.setdefault("success", True)
                else:
                    payload = dict(tool_result.data) if tool_result.data else {}
                    payload.setdefault("success", False)
                    payload["failure_reason"] = tool_result.failure_reason or "unknown error"
                    payload["error"] = tool_result.failure_reason or "unknown error"
                    payload["retryable"] = tool_result.retryable

                content_text = json.dumps(payload, ensure_ascii=False)
                content_blocks = [ContentBlock(text=content_text)]
                frame_path = payload.get("frame_path")
                if isinstance(frame_path, str) and frame_path:
                    try:
                        content_blocks.append(ContentBlock.from_image_path(frame_path))
                    except OSError:
                        pass
                results.append(
                    ToolResultMessage(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=content_blocks,
                        is_error=not tool_result.success,
                        data=payload,
                    )
                )
            elif isinstance(tool_result, ToolResultMessage):
                if not tool_result.tool_call_id:
                    tool_result = tool_result.model_copy(update={"tool_call_id": tc.id})
                results.append(tool_result)
            else:
                if isinstance(tool_result, dict):
                    data = tool_result
                else:
                    data = {"result": str(tool_result)}
                results.append(
                    ToolResultMessage(
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=[ContentBlock(text=json.dumps(data, ensure_ascii=False))],
                    )
                )

        return results


def _terminal_alfworld_result(
    tool_call: ToolCall,
    run_context: RunContext,
) -> ToolResultMessage | None:
    outcome = run_context.deps.get("alfworld_episode_outcome")
    if outcome is None or not bool(getattr(outcome, "terminal", False)):
        return None
    if not tool_call.name.startswith("robot_"):
        return None
    classification = str(
        getattr(outcome, "classification", None) or "unclassified_execution_failure"
    )
    payload = {
        "success": False,
        "error": "episode_terminated",
        "terminal": True,
        "classification": classification,
        "score_eligible": bool(getattr(outcome, "score_eligible", False)),
    }
    return ToolResultMessage(
        tool_call_id=tool_call.id,
        name=tool_call.name,
        content=[ContentBlock(text=json.dumps(payload, ensure_ascii=False, sort_keys=True))],
        is_error=True,
        data=payload,
    )


def _sync_alfworld_outcome(
    *,
    run_context: RunContext,
    tool_call_id: str,
    data: dict[str, Any],
) -> None:
    if data.get("terminal") is not True:
        return
    outcome = run_context.deps.get("alfworld_episode_outcome")
    if outcome is None or bool(getattr(outcome, "terminal", False)):
        return
    classification = str(data.get("classification") or "unclassified_execution_failure")
    marker = getattr(outcome, "mark_terminal", None)
    if callable(marker):
        marker(classification=classification, tool_call_id=tool_call_id)
        return
    outcome.terminal = True
    outcome.classification = classification
    outcome.terminal_tool_call_id = tool_call_id
    outcome.score_eligible = bool(data.get("score_eligible", False))
