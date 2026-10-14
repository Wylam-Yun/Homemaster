"""Trace output for RoboCasa benchmark episodes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage


class RoboCasaToolDispatchObserver:
    """Terminal short-circuit + bookkeeping for robocasa_* tool calls."""

    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    def on_call(self, tool_call: ToolCall) -> None:
        del tool_call
        self._outcome.agent_tool_call_count += 1

    def terminal_result(self, tool_call: ToolCall) -> ToolResultMessage | None:
        if not self._outcome.terminal or not tool_call.name.startswith("robocasa_"):
            return None
        payload = {
            "success": False,
            "error": "infrastructure_error",
            "terminal": True,
            "classification": self._outcome.classification or "infrastructure_error",
            "score_eligible": False,
            "detail": "The episode has already terminated.",
        }
        return ToolResultMessage(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=[ContentBlock(text=json.dumps(payload, ensure_ascii=False, sort_keys=True))],
            is_error=True,
            data=payload,
        )

    def on_result(self, tool_call: ToolCall, result: Any) -> None:
        data = getattr(result, "data", None)
        if not isinstance(data, Mapping) or data.get("terminal") is not True:
            return
        self._outcome.mark_terminal(
            classification=str(data.get("classification") or "infrastructure_error"),
            tool_call_id=tool_call.id,
            evidence_ref=_first_evidence_ref(data),
        )


def _first_evidence_ref(data: Mapping[str, Any]) -> str | None:
    refs = data.get("evidence_refs")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, str) and ref:
                return ref
    ref = data.get("evidence_ref")
    return ref if isinstance(ref, str) and ref else None


class RoboCasaTraceWriter:
    def __init__(self, episode_dir: Path) -> None:
        self.episode_dir = episode_dir
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.episode_dir / "trace.jsonl"
        self.model_trace_path = self.episode_dir / "model_trace.jsonl"
        self.summary_path = self.episode_dir / "summary.json"
        self.trajectory_memory_path = self.episode_dir / "trajectory_memory.json"

    def write_event(self, event: dict[str, Any]) -> str:
        encoded = json.dumps(_copy_trace_value(event), ensure_ascii=False, sort_keys=True)
        with self.trace_path.open("a", encoding="utf-8") as writer:
            writer.write(encoded)
            writer.write("\n")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return f"{self.trace_path.name}#sha256={digest}"

    def write_model_event(self, event: dict[str, Any]) -> None:
        with self.model_trace_path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(_copy_trace_value(event), ensure_ascii=False, sort_keys=True))
            writer.write("\n")

    def write_session_messages(self, session: Any) -> None:
        for index, message in enumerate(getattr(session, "messages", [])):
            role = getattr(message, "role", "")
            if role == "user":
                self.write_model_event({
                    "content": _content_trace_payload(getattr(message, "content", [])),
                    "event": "user_message",
                    "message_index": index,
                })
            elif role == "assistant":
                self.write_model_event({
                    "event": "assistant_completed",
                    "finish_reason": getattr(message, "finish_reason", None),
                    "message_index": index,
                    "reasoning_content": getattr(message, "reasoning_content", None),
                    "text": getattr(message, "text", ""),
                    "tool_calls": [
                        {"arguments": call.arguments, "id": call.id, "name": call.name}
                        for call in getattr(message, "tool_calls", [])
                    ],
                    "usage": getattr(message, "usage", None),
                })
            elif role == "tool":
                self.write_model_event({
                    "content": _content_trace_payload(getattr(message, "content", [])),
                    "event": "tool_result",
                    "is_error": getattr(message, "is_error", False),
                    "message_index": index,
                    "name": getattr(message, "name", None),
                    "tool_call_id": getattr(message, "tool_call_id", None),
                })

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(
            json.dumps(_copy_trace_value(summary), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def write_episode_finished(self, payload: Mapping[str, Any]) -> str:
        return self.write_event({"event": "robocasa.episode_finished", **dict(payload)})

    def source_trace_sha256(self) -> str:
        return hashlib.sha256(self.trace_path.read_bytes()).hexdigest()

    def write_trajectory_memory(self, payload: Mapping[str, Any]) -> None:
        self.trajectory_memory_path.write_text(
            json.dumps(_copy_trace_value(payload), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def _copy_trace_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _copy_trace_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy_trace_value(item) for item in value]
    if isinstance(value, tuple):
        return [_copy_trace_value(item) for item in value]
    return value


def _content_trace_payload(blocks: list[Any]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for block in blocks:
        block_type = getattr(block, "type", "text")
        if block_type == "image":
            source = getattr(block, "source", None) or {}
            metadata = getattr(block, "metadata", None) or {}
            payload.append({
                "media_type": source.get("media_type"),
                "path": metadata.get("path"),
                "type": "image",
            })
        else:
            payload.append({"text": getattr(block, "text", ""), "type": "text"})
    return payload


__all__ = ["RoboCasaToolDispatchObserver", "RoboCasaTraceWriter"]
