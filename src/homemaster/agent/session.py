"""AgentSession — message container and snapshot serialization."""

from __future__ import annotations

import time
from typing import Any

from homemaster.agent.messages import (
    AssistantMessage,
    ContentBlock,
    Message,
    ToolResultMessage,
    UserMessage,
)
from homemaster.agent.state import AgentState
from homemaster.task_state.store import TaskStateStore


class AgentSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._created_at = time.time()
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    def append(self, message: Message) -> None:
        self._messages.append(message)

    def replace_messages(self, messages: list[Message]) -> None:
        self._messages = list(messages)

    def clear(self) -> None:
        self._messages.clear()

    def to_snapshot_dict(
        self,
        *,
        agent_state: AgentState,
        task_state_store: TaskStateStore,
        model: str,
        system_prompt: str,
        strip_images: bool = True,
        preserve_image_tool_call_ids: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        messages = [
            _message_to_dict(
                message,
                strip_images=strip_images,
                iter_index=index,
                preserve_image_tool_call_ids=preserve_image_tool_call_ids,
            )
            for index, message in enumerate(self._messages)
        ]
        agent_state_dump = agent_state.model_dump(mode="json")
        if strip_images:
            _strip_artifact_head_images(agent_state_dump)
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "created_at": self._created_at,
            "saved_at": time.time(),
            "model": model,
            "system_prompt": system_prompt,
            "messages": messages,
            "agent_state": agent_state_dump,
            "task_state": task_state_store.to_snapshot_dict(),
        }

    @classmethod
    def from_snapshot_dict(
        cls, data: dict[str, Any]
    ) -> tuple[AgentSession, AgentState, TaskStateStore]:
        session = cls(session_id=str(data["session_id"]))
        session._created_at = float(data.get("created_at") or time.time())
        session._messages = []
        for item in data.get("messages", []):
            if not isinstance(item, dict) or _is_legacy_empty_assistant(item):
                continue
            session._messages.append(_message_from_dict(item))
        agent_state = AgentState.model_validate(data.get("agent_state") or {})
        task_state = TaskStateStore.from_snapshot_dict(data.get("task_state") or {})
        return session, agent_state, task_state

    @staticmethod
    def _strip_image_for_persistence(
        block: ContentBlock,
        *,
        tool_name: str = "unknown",
        iter_index: int = 0,
        args: dict[str, Any] | None = None,
    ) -> ContentBlock:
        return ContentBlock(
            type="text",
            text=(
                f"[image stripped - {tool_name} @ iter {iter_index}, "
                f"args={args or {}}. See trace.jsonl for original]"
            ),
        )


def _strip_artifact_head_images(agent_state_dump: dict[str, Any]) -> None:
    """Drop image bytes embedded in a persisted compaction head.

    Snapshots strip images from the canonical transcript, so an artifact
    whose folded prefix contained images can never hash-match after resume
    anyway — persisting the bytes is pure bloat. Head messages are already
    plain dicts (``model_dump`` output), so the strip works on dict shape
    rather than re-validating ContentBlocks.
    """
    compaction = agent_state_dump.get("compaction")
    if not isinstance(compaction, dict):
        return
    for item in compaction.get("head_messages") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        tool_name = item.get("name") or item.get("role") or "unknown"
        item["content"] = [
            {
                "type": "text",
                "text": (
                    f"[image stripped - {tool_name} @ compaction head. "
                    "See trace.jsonl for original]"
                ),
            }
            if isinstance(block, dict) and block.get("type") == "image"
            else block
            for block in content
        ]


def _message_to_dict(
    message: Message,
    *,
    strip_images: bool,
    iter_index: int,
    preserve_image_tool_call_ids: frozenset[str],
) -> dict[str, Any]:
    payload = message.model_dump(mode="json")
    if not strip_images:
        return payload
    tool_name = getattr(message, "name", getattr(message, "role", "unknown"))
    preserve_images = (
        isinstance(message, ToolResultMessage)
        and message.tool_call_id in preserve_image_tool_call_ids
    )
    payload["content"] = [
        (
            AgentSession._strip_image_for_persistence(
                ContentBlock.model_validate(block),
                tool_name=tool_name,
                iter_index=iter_index,
            ).model_dump(mode="json")
            if (isinstance(block, dict) and block.get("type") == "image" and not preserve_images)
            else block
        )
        for block in payload.get("content", [])
    ]
    return payload


def _message_from_dict(data: dict[str, Any]) -> Message:
    role = data.get("role")
    if role == "user":
        return UserMessage.model_validate(data)
    if role == "assistant":
        return AssistantMessage.model_validate(data)
    if role == "tool":
        return ToolResultMessage.model_validate(data)
    raise ValueError(f"unknown message role: {role!r}")


def _is_legacy_empty_assistant(data: dict[str, Any]) -> bool:
    return (
        data.get("role") == "assistant"
        and not data.get("content")
        and not data.get("tool_calls")
        and not data.get("reasoning_content")
    )


def new_session_id() -> str:
    """Timestamped session id used by the CLI (moved from agent/turn.py —
    the turn wrappers were orphaned compat shims; this is the only part
    with live consumers)."""
    import uuid
    from datetime import datetime

    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
