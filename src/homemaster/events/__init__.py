"""Structured runtime event tracing for AgentRuntime and application tools.

RuntimeEvent schema, event sinks (JSONL, console, null, fanout), and
sanitizer for redacting sensitive data from trace output.
"""

from homemaster.events.bus import EventBus
from homemaster.events.runtime_events import KNOWN_EVENT_TYPES, EventSink, RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload, sanitize_for_trace
from homemaster.events.sinks import (
    ConsoleEventSink,
    FanoutEventSink,
    JsonlEventSink,
    JsonlTraceSink,
    MessagesLogSink,
    NullEventSink,
    VerboseConsoleEventSink,
)
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

__all__ = [
    "ConsoleEventSink",
    "AssistantTextDelta",
    "AssistantTurnComplete",
    "CompactProgressEvent",
    "EventBus",
    "EventSink",
    "ErrorEvent",
    "FanoutEventSink",
    "JsonlEventSink",
    "JsonlTraceSink",
    "KNOWN_EVENT_TYPES",
    "MessagesLogSink",
    "NullEventSink",
    "RuntimeEvent",
    "StatusEvent",
    "StreamEvent",
    "ToolExecutionCompleted",
    "ToolExecutionStarted",
    "VerboseConsoleEventSink",
    "sanitize_event_payload",
    "sanitize_for_trace",
    "project_stream_event",
]
