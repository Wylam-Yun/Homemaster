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
    ) -> dict[str, Any]:
        messages = [
            _message_to_dict(message, strip_images=strip_images, iter_index=index)
            for index, message in enumerate(self._messages)
        ]
        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "created_at": self._created_at,
            "saved_at": time.time(),
            "model": model,
            "system_prompt": system_prompt,
            "messages": messages,
            "agent_state": agent_state.model_dump(mode="json"),
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


def _message_to_dict(
    message: Message,
    *,
    strip_images: bool,
    iter_index: int,
) -> dict[str, Any]:
    payload = message.model_dump(mode="json")
    if not strip_images:
        return payload
    tool_name = getattr(message, "name", getattr(message, "role", "unknown"))
    payload["content"] = [
        (
            AgentSession._strip_image_for_persistence(
                ContentBlock.model_validate(block),
                tool_name=tool_name,
                iter_index=iter_index,
            ).model_dump(mode="json")
            if isinstance(block, dict) and block.get("type") == "image"
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
