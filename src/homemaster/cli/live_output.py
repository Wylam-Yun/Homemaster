"""Live RuntimeEvent adapters for Rich, plain text, and JSON-lines output."""

from __future__ import annotations

import json
from typing import Any, TextIO

from homemaster.agent.messages import AssistantMessage, ContentBlock, ToolCall
from homemaster.cli.rich_renderer import RichOutputRenderer
from homemaster.events.public_projection import PublicEventProjection
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
    project_stream_event,
)


class _ProjectedRuntimeSink:
    def __init__(self) -> None:
        self._projection = PublicEventProjection()

    def _project(self, event: RuntimeEvent) -> StreamEvent | None:
        public = project_stream_event(event)
        return _copy_stream_event(public, self._projection) if public is not None else None

    @property
    def events(self) -> list[RuntimeEvent]:
        return []


class TextStreamEventSink(_ProjectedRuntimeSink):
    def __init__(self, *, file: TextIO) -> None:
        super().__init__()
        self._file = file
        self._text = ""

    def emit(self, event: RuntimeEvent) -> None:
        public = self._project(event)
        if not isinstance(public, AssistantTextDelta):
            return
        self._text += public.text
        self._file.write(public.text)
        self._file.flush()

    def finish(self, final_text: str) -> None:
        """Complete stdout when a provider returned no or only partial deltas."""

        safe = self._projection.project_content(final_text)
        if safe.startswith(self._text):
            suffix = safe[len(self._text) :]
            if suffix:
                self._file.write(suffix)
                self._file.flush()
                self._text = safe


class StreamJsonEventSink(_ProjectedRuntimeSink):
    def __init__(self, *, file: TextIO) -> None:
        super().__init__()
        self._file = file

    def emit(self, event: RuntimeEvent) -> None:
        public = self._project(event)
        if public is None:
            return
        self.write_public(public)

    def write_public(self, event: StreamEvent) -> None:
        self.write_envelope(stream_event_envelope(event))

    def write_envelope(self, envelope: dict[str, object]) -> None:
        self._file.write(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()


class RichStreamEventSink(_ProjectedRuntimeSink):
    def __init__(
        self,
        renderer: RichOutputRenderer,
    ) -> None:
        super().__init__()
        self._renderer = renderer

    def emit(self, event: RuntimeEvent) -> None:
        if event.type == "transport.request_started":
            self._renderer.model_request_started()
            return
        if event.type == "permission.confirmation_requested":
            self._renderer.confirmation_requested()
            return
        public = self._project(event)
        if public is None:
            return
        self._renderer.render(public)
        if isinstance(public, ErrorEvent) or event.type == "runtime.cancelled":
            self._renderer.close()

    def close(self) -> None:
        self._renderer.close()


def stream_event_envelope(event: StreamEvent) -> dict[str, object]:
    if isinstance(event, AssistantTextDelta):
        return {"type": "assistant_delta", "text": event.text}
    if isinstance(event, AssistantTurnComplete):
        return {
            "type": "assistant_complete",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": block.text}
                    for block in event.message.content
                    if block.type == "text"
                ],
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    for tool_call in event.message.tool_calls
                ],
                "finish_reason": event.message.finish_reason,
            },
            "usage": event.usage,
        }
    if isinstance(event, ToolExecutionStarted):
        return {
            "type": "tool_started",
            "tool_name": event.tool_name,
            "tool_input": event.tool_input,
        }
    if isinstance(event, ToolExecutionCompleted):
        return {
            "type": "tool_completed",
            "tool_name": event.tool_name,
            "output": event.output,
            "is_error": event.is_error,
            "metadata": event.metadata,
        }
    if isinstance(event, ErrorEvent):
        return {
            "type": "error",
            "message": event.message,
            "recoverable": event.recoverable,
        }
    if isinstance(event, StatusEvent):
        return {"type": "status", "message": event.message}
    if isinstance(event, CompactProgressEvent):
        return {
            "type": "compact_progress",
            "phase": event.phase,
            "trigger": event.trigger,
            "message": event.message,
            "attempt": event.attempt,
            "checkpoint": event.checkpoint,
            "metadata": event.metadata,
        }
    raise TypeError(f"unsupported StreamEvent: {type(event).__name__}")


def _copy_stream_event(
    event: StreamEvent,
    projection: PublicEventProjection,
) -> StreamEvent:
    if isinstance(event, AssistantTextDelta):
        return AssistantTextDelta(projection.project_content(event.text))
    if isinstance(event, AssistantTurnComplete):
        return AssistantTurnComplete(
            message=AssistantMessage(
                content=[
                    ContentBlock(text=projection.project_content(block.text))
                    for block in event.message.content
                    if block.type == "text"
                ],
                tool_calls=[
                    ToolCall(
                        id=tool_call.id,
                        name=projection.project_content(tool_call.name),
                        arguments=_safe_mapping(projection.copy_value(tool_call.arguments)),
                    )
                    for tool_call in event.message.tool_calls
                ],
                finish_reason=event.message.finish_reason,
            ),
            usage=dict(event.usage),
        )
    if isinstance(event, ToolExecutionStarted):
        return ToolExecutionStarted(
            tool_name=projection.project_content(event.tool_name),
            tool_input=_safe_mapping(projection.copy_value(event.tool_input)),
        )
    if isinstance(event, ToolExecutionCompleted):
        metadata = projection.copy_value(event.metadata)
        return ToolExecutionCompleted(
            tool_name=projection.project_content(event.tool_name),
            output=projection.project_content(event.output),
            is_error=event.is_error,
            metadata=_safe_mapping(metadata) if isinstance(metadata, dict) else None,
        )
    if isinstance(event, ErrorEvent):
        return ErrorEvent(
            message=projection.project_content(event.message),
            recoverable=event.recoverable,
        )
    if isinstance(event, StatusEvent):
        return StatusEvent(projection.project_content(event.message))
    if isinstance(event, CompactProgressEvent):
        metadata = projection.copy_value(event.metadata)
        return CompactProgressEvent(
            phase=event.phase,
            trigger=event.trigger,
            message=(
                projection.project_content(event.message) if event.message is not None else None
            ),
            attempt=event.attempt,
            checkpoint=(
                projection.project_content(event.checkpoint)
                if event.checkpoint is not None
                else None
            ),
            metadata=_safe_mapping(metadata) if isinstance(metadata, dict) else None,
        )
    raise TypeError(f"unsupported StreamEvent: {type(event).__name__}")


def _safe_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "RichStreamEventSink",
    "StreamJsonEventSink",
    "TextStreamEventSink",
    "stream_event_envelope",
]
