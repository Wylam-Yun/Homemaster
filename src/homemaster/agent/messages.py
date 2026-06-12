"""Normalized message schemas for the generic agent loop.

All message content is stored as list[ContentBlock]. External APIs may
accept plain strings, but normalize_content() converts before storage.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class ContentBlock(BaseModel):
    type: Literal["text", "image"] = "text"
    text: str = ""
    source: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_image_path(
        cls,
        path: str | Path,
        *,
        media_type: str = "image/png",
    ) -> ContentBlock:
        image_path = Path(path)
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return cls(
            type="image",
            source={"type": "base64", "media_type": media_type, "data": data},
            metadata={"path": str(image_path)},
        )


def normalize_content(value: str | list[ContentBlock]) -> list[ContentBlock]:
    if isinstance(value, str):
        return [ContentBlock(text=value)] if value else []
    return value


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: list[ContentBlock]

    @classmethod
    def from_text(cls, text: str) -> UserMessage:
        return cls(content=normalize_content(text))


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock] = Field(default_factory=list)
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.content if b.text)


class ToolResultMessage(BaseModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: list[ContentBlock]
    is_error: bool = False
    data: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


Message = UserMessage | AssistantMessage | ToolResultMessage
