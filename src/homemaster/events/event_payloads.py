"""Bounded event payload projection for compact console output."""

from __future__ import annotations

from typing import Any

from homemaster.events.trace import json_compatible_copy

_MAX_PAYLOAD_LEN = 4000


def bounded_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy payload values exactly and annotate oversized console payloads."""
    projected: dict[str, Any] = json_compatible_copy(payload)
    serialized = str(projected)
    if len(serialized) > _MAX_PAYLOAD_LEN:
        projected["_oversized"] = True
        projected["_payload_len"] = len(serialized)
    return projected


def trace_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Copy debug trace payloads exactly without truncating content."""

    return json_compatible_copy(payload)
