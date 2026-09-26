"""Observability composition contracts."""

from __future__ import annotations

from pathlib import Path

from homemaster.events.sinks import JsonlTraceSink, MessagesLogSink


def compose_observability_sinks(run_dir: Path) -> tuple[JsonlTraceSink, MessagesLogSink]:
    """Create the durable trace sinks owned by one application bundle."""

    return JsonlTraceSink(run_dir), MessagesLogSink(run_dir)


__all__ = ["compose_observability_sinks"]
