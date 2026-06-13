"""Context providers — collect ContextItems from runtime state."""

from __future__ import annotations

import json
from typing import Protocol

from homemaster.agent.context_budget import estimate_text_tokens
from homemaster.agent.context_items import (
    ContextFreshness,
    ContextItem,
    ContextPlacement,
    ContextPriority,
    RenderMode,
)
from homemaster.agent.session import AgentSession
from homemaster.agent.state import AgentState
from homemaster.task_state.store import TaskStateStore


class ContextProvider(Protocol):
    name: str

    def collect(self) -> list[ContextItem]: ...


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


class TaskStateSnapshotProvider:
    name = "task_state_snapshot"

    def __init__(self, store: TaskStateStore | None) -> None:
        self._store = store

    def collect(self) -> list[ContextItem]:
        snapshot = self._store.snapshot if self._store else None
        if snapshot is None:
            return []
        if snapshot.status.value == "completed":
            visible = snapshot.to_completed_model_summary_dict()
        else:
            visible = snapshot.to_model_visible_dict()
        text = "# Task State Snapshot\n" + _json_text(visible)
        priority = (
            ContextPriority.REQUIRED
            if visible.get("status") == "active"
            else ContextPriority.IMPORTANT
        )
        return [
            ContextItem(
                id="task_state_snapshot",
                kind="task_state_snapshot",
                priority=priority,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
                mode=RenderMode.FULL,
            )
        ]


class RuntimeBudgetStatusProvider:
    name = "runtime_budget_status"

    def __init__(self, state: AgentState) -> None:
        self._state = state

    def collect(self) -> list[ContextItem]:
        payload = {
            "type": "runtime_budget_status",
            "iteration_index": self._state.iteration_index,
            "max_tool_iterations": self._state.max_tool_iterations,
            "consecutive_tool_errors": self._state.consecutive_tool_errors,
            "no_progress_iterations": self._state.no_progress_iterations,
            "estimated_context_tokens": self._state.estimated_context_tokens,
            "last_compaction": (
                self._state.last_compaction.kind
                if self._state.last_compaction is not None
                else "none"
            ),
        }
        text = "# Runtime Budget Status\n" + _json_text(payload)
        return [
            ContextItem(
                id="runtime_budget_status",
                kind="runtime_budget_status",
                priority=ContextPriority.IMPORTANT,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
                mode=RenderMode.FULL,
            )
        ]


class FailureSummaryProvider:
    name = "failure_summary"

    def __init__(self, state: AgentState) -> None:
        self._state = state

    def collect(self) -> list[ContextItem]:
        errors = [
            r for r in self._state.last_tool_results_summary if r.get("is_error")
        ]
        if not errors:
            return []
        payload = {
            "type": "failure_summary",
            "active_failures": [
                {
                    "tool": r.get("name", "unknown"),
                    "reason": r.get("text", "")[:200],
                    "attempts": 1,
                }
                for r in errors[:3]
            ],
            "consecutive_tool_errors": self._state.consecutive_tool_errors,
        }
        text = "# Failure Summary\n" + _json_text(payload)
        return [
            ContextItem(
                id="failure_summary",
                kind="failure_summary",
                priority=ContextPriority.IMPORTANT,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONTEXT_PRELUDE,
                token_estimate=estimate_text_tokens(text),
                render=lambda _mode, text=text: text,
                mode=RenderMode.FULL,
            )
        ]


class ConversationProvider:
    name = "conversation"

    def __init__(self, session: AgentSession) -> None:
        self._session = session

    def collect(self) -> list[ContextItem]:
        messages = self._session.messages
        if not messages:
            return []
        total_text = sum(
            estimate_text_tokens(block.text)
            for msg in messages
            for block in msg.content
            if block.text
        )
        return [
            ContextItem(
                id="conversation",
                kind="conversation",
                priority=ContextPriority.REQUIRED,
                freshness=ContextFreshness.CURRENT,
                placement=ContextPlacement.CONVERSATION,
                token_estimate=total_text,
                render=lambda _mode, msgs=messages: msgs,
            )
        ]
