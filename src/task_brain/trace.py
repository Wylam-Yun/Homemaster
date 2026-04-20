"""Trace helpers for CLI rendering and JSONL export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def normalize_graph_trace_events(trace: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalize graph trace events for CLI and file output."""
    if not isinstance(trace, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(trace):
        if not isinstance(item, dict):
            continue

        event = item.get("event")
        if not isinstance(event, str):
            continue

        payload = {key: value for key, value in item.items() if key != "event"}
        normalized.append(
            {
                "index": index,
                "event": event,
                "payload": payload,
            }
        )
    return normalized


def render_trace_report(
    *,
    scenario: str,
    instruction: str,
    final_status: str,
    events: list[dict[str, Any]],
) -> str:
    """Render human-readable CLI trace report."""
    lines = [
        f"scenario: {scenario}",
        f"instruction: {instruction}",
        f"final_status: {final_status}",
        "trace_events:",
    ]
    for event in events:
        lines.append(f"- {event['index']:02d} {event['event']}")
    return "\n".join(lines)


def write_trace_jsonl(
    *,
    path: str | Path,
    scenario: str,
    final_status: str,
    events: list[dict[str, Any]],
) -> Path:
    """Write normalized trace events to a JSONL file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    with target.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(
                json.dumps(
                    {
                        "scenario": scenario,
                        "final_status": final_status,
                        "index": event["index"],
                        "event": event["event"],
                        "payload": event["payload"],
                    },
                    ensure_ascii=False,
                )
            )
            handle.write("\n")
    return target


__all__ = [
    "normalize_graph_trace_events",
    "render_trace_report",
    "write_trace_jsonl",
]
