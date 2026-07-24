"""Structured runtime event tracing for AgentRuntime and application tools.

RuntimeEvent schema, event sinks (JSONL, console, null, fanout), and bounded
console payload projection.
"""

from homemaster.events.bus import EventBus
from homemaster.events.event_payloads import bounded_event_payload, trace_event_payload
from homemaster.events.runtime_events import KNOWN_EVENT_TYPES, EventSink, RuntimeEvent
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
    "bounded_event_payload",
    "trace_event_payload",
    "project_stream_event",
]
