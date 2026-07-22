"""Stable CLI envelopes for HomeMaster one-shot and dry-run output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import StrEnum
from typing import Any

from homemaster.application import RunResult, RunStatus
from homemaster.events.sanitizer import sanitize_event_payload


class OutputFormat(StrEnum):
    TEXT = "text"
    JSON = "json"
    STREAM_JSON = "stream-json"


def parse_output_format(value: str | None) -> OutputFormat:
    try:
        return OutputFormat(value or OutputFormat.TEXT)
    except ValueError as exc:
        raise ValueError("--output-format must be text, json, or stream-json") from exc


def render_run_result(result: RunResult, output_format: OutputFormat) -> str:
    envelope = run_result_envelope(result)
    if output_format is OutputFormat.TEXT:
        return result.final_reply
    if output_format is OutputFormat.JSON:
        return json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True)
    lines = [
        json.dumps(
            {"type": "event", "event": _event_value(event)},
            ensure_ascii=False,
            sort_keys=True,
        )
        for event in result.events
    ]
    lines.append(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


def render_dry_run(preview: Mapping[str, object], output_format: OutputFormat) -> str:
    if output_format is OutputFormat.TEXT:
        settings = preview["settings"]
        assert isinstance(settings, Mapping)
        tools = preview["tools"]
        assert isinstance(tools, list)
        return "\n".join(
            [
                "HomeMaster Dry Run",
                f"mode: {preview['entrypoint']}",
                f"prompt: {preview.get('prompt') or '(none)'}",
                f"profile: {settings['profile']}",
                f"provider: {settings['provider']}",
                f"model: {settings['model']}",
                f"tools: {len(tools)}",
                f"mcp: {preview['mcp_discovery']}",
                f"external_io: {str(bool(preview['external_io'])).lower()}",
            ]
        )
    if output_format is OutputFormat.JSON:
        return json.dumps(preview, ensure_ascii=False, indent=2, sort_keys=True)
    return json.dumps(preview, ensure_ascii=False, sort_keys=True)


def run_result_envelope(result: RunResult) -> dict[str, object]:
    return {
        "type": "result",
        "run_id": result.run_id,
        "session_id": result.session_id,
        "status": str(result.status),
        "final_reply": result.final_reply,
        "error_code": result.error_code,
        "metadata": result.metadata_dict(),
    }


def result_exit_code(result: RunResult) -> int:
    try:
        status = RunStatus(result.status)
    except ValueError:
        return 1
    if status in {RunStatus.REPLIED, RunStatus.COMPLETED, RunStatus.WAITING_USER}:
        return 0
    if status is RunStatus.CANCELLED:
        return 130
    return 1


def _event_value(event: object) -> object:
    value: Any = asdict(event) if is_dataclass(event) else event
    return sanitize_event_payload(value)


__all__ = [
    "OutputFormat",
    "parse_output_format",
    "render_dry_run",
    "render_run_result",
    "result_exit_code",
    "run_result_envelope",
]
