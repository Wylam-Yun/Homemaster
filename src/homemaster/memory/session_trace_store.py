"""Canonical per-session runtime trace facade (V3.8).

Bridges the runtime to :class:`MemoryWorkStore`: at turn start it opens the
turn row; at the authoritative ``RunResult`` boundary it converts the turn's
canonical ``session.messages`` slice into ordered trace events and commits
terminal status + Add intent in **one** store transaction.

Two payloads are derived from the same canonical events:

- ``trace event`` — internal audit form (keeps machine fields such as
  ``backend_attempted`` for later reconciliation).
- ``add dialogue`` — the provider-visible conversation sent to MindMemOS:
  user/assistant text, one assistant message per tool call and one ``tool``
  message per tool result, both carrying the same ``tool_call_id``.  Internal
  reasoning, transport deltas, permission objects, credentials and binary
  payloads never enter the dialogue; media blocks degrade to a stable textual
  reference.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolResultMessage,
    UserMessage,
)
from homemaster.memory.memory_work_store import (
    MemoryWorkStore,
    TraceEventRecord,
    TurnRecord,
)

TerminalStatus = Literal[
    "completed", "failed", "budget_exhausted", "cancelled", "interrupted"
]

_EVENT_KIND_MESSAGE = "message"

# Media is never inlined into memory payloads (spec §4.1.3); the placeholder
# keeps ordering + provenance without transporting binary bytes.
_MEDIA_PLACEHOLDER = "[media: {path}]"
_MEDIA_PLACEHOLDER_ANON = "[media]"


@dataclass(frozen=True)
class TurnCommit:
    turn: TurnRecord
    add_operation_id: str
    add_job_id: str


def _block_text(block: ContentBlock) -> str:
    if block.type == "text":
        return block.text
    path = (block.metadata or {}).get("path")
    if isinstance(path, str) and path:
        return _MEDIA_PLACEHOLDER.format(path=path)
    return _MEDIA_PLACEHOLDER_ANON


def _content_text(content: Iterable[ContentBlock]) -> str:
    return "\n".join(text for text in (_block_text(b) for b in content) if text)


def message_to_trace_event(message: Message, *, seq_hint: int) -> dict[str, Any]:
    """Serialize one canonical session message into a trace event payload."""
    if isinstance(message, UserMessage):
        payload: dict[str, Any] = {
            "role": "user",
            "content": _content_text(message.content),
        }
    elif isinstance(message, AssistantMessage):
        payload = {
            "role": "assistant",
            "content": _content_text(message.content),
            "tool_calls": [
                {"id": call.id, "name": call.name, "arguments": call.arguments}
                for call in message.tool_calls
            ],
        }
    elif isinstance(message, ToolResultMessage):
        payload = {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.name,
            "content": _content_text(message.content),
            "is_error": message.is_error,
            "data": message.data or {},
        }
    else:  # pragma: no cover - Message is a closed union
        raise TypeError(f"unsupported message type: {type(message)!r}")
    return {"kind": _EVENT_KIND_MESSAGE, "payload": payload, "seq_hint": seq_hint}


def _tool_result_status(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    status = data.get("status")
    if isinstance(status, str) and status:
        return status
    return "error" if payload.get("is_error") else "success"


def trace_event_to_dialogue(event: TraceEventRecord) -> list[dict[str, Any]]:
    """Project one trace event into MindMemOS dialogue messages.

    Tool calls become assistant messages carrying a JSON ``tool_call_id``
    envelope; tool results become ``tool`` messages with the same id so the
    upstream grouper/extractor can re-pair them.
    """
    payload = event.payload
    role = payload.get("role")
    if role == "user":
        return [{"role": "user", "content": str(payload.get("content") or "")}]
    if role == "assistant":
        out: list[dict[str, Any]] = []
        text = str(payload.get("content") or "")
        if text:
            out.append({"role": "assistant", "content": text})
        for call in payload.get("tool_calls") or []:
            out.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "tool_call_id": call.get("id"),
                            "name": call.get("name"),
                            "arguments": call.get("arguments") or {},
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            )
        return out
    if role == "tool":
        return [
            {
                "role": "tool",
                "content": json.dumps(
                    {
                        "tool_call_id": payload.get("tool_call_id"),
                        "name": payload.get("name"),
                        "result": payload.get("content") or "",
                        "status": _tool_result_status(payload),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ]
    return []


class SessionTraceStore:
    """Application-facing facade over the canonical trace tables."""

    def __init__(self, store: MemoryWorkStore) -> None:
        self._store = store

    @property
    def store(self) -> MemoryWorkStore:
        return self._store

    def begin_turn(
        self, session_id: str, turn_index: int, run_id: str, *, message_start_index: int
    ) -> TurnRecord:
        return self._store.begin_turn(
            session_id, turn_index, run_id, message_start_index=message_start_index
        )

    def commit_terminal(
        self,
        session_id: str,
        turn_id: str,
        *,
        status: TerminalStatus,
        messages: list[Message],
        terminal_reason: str | None = None,
        message_end_index: int | None = None,
    ) -> TurnCommit:
        """Commit terminal status + canonical events + Add intent atomically."""
        events = [
            message_to_trace_event(message, seq_hint=index)
            for index, message in enumerate(messages)
        ]
        operation_id = f"turnadd:{session_id}:{turn_id}"
        intent_payload = {
            "source": "homemaster.turn",
            "session_id": session_id,
            "turn_id": turn_id,
            "terminal_status": status,
            "terminal_reason": terminal_reason or status,
            "message_end_index": message_end_index,
        }
        committed = self._store.commit_turn_terminal(
            session_id,
            turn_id,
            status=status,
            events=events,
            operation_id=operation_id,
            intent_payload=intent_payload,
        )
        return TurnCommit(
            turn=committed.turn,
            add_operation_id=operation_id,
            add_job_id=committed.add_job.job_id,
        )

    def mark_interrupted(
        self,
        session_id: str,
        turn_id: str,
        *,
        messages: list[Message],
        message_end_index: int | None = None,
    ) -> TurnCommit:
        return self.commit_terminal(
            session_id,
            turn_id,
            status="interrupted",
            messages=messages,
            terminal_reason="recovered",
            message_end_index=message_end_index,
        )

    def dialogue_for_turn(self, session_id: str, turn_id: str) -> list[dict[str, Any]]:
        """Reconstruct the Add dialogue from persisted canonical events."""
        events = self._store.events_for_turn(session_id, turn_id)
        dialogue: list[dict[str, Any]] = []
        for event in events:
            dialogue.extend(trace_event_to_dialogue(event))
        return dialogue

    def turn(self, session_id: str, turn_id: str) -> TurnRecord | None:
        return self._store.turn(session_id, turn_id)


__all__ = [
    "SessionTraceStore",
    "TerminalStatus",
    "TurnCommit",
    "message_to_trace_event",
    "trace_event_to_dialogue",
]
