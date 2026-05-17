"""Events — structured runtime event tracing for AgentRuntime.

RuntimeEvent schema, event sinks (JSONL, console, null, fanout), and
sanitizer for redacting sensitive data from trace output.
"""

from homemaster.events.runtime_events import KNOWN_EVENT_TYPES, EventSink, RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload
from homemaster.events.sinks import (
    ConsoleProgressEventSink,
    FanoutEventSink,
    JsonlEventSink,
    NullEventSink,
)

__all__ = [
    "ConsoleProgressEventSink",
    "EventSink",
    "FanoutEventSink",
    "JsonlEventSink",
    "KNOWN_EVENT_TYPES",
    "NullEventSink",
    "RuntimeEvent",
    "sanitize_event_payload",
]
