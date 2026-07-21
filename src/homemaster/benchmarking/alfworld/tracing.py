"""Trace output for ALFWorld benchmark episodes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from homemaster.agent.messages import ContentBlock, ToolCall, ToolResultMessage

SECRET_KEY_FRAGMENTS = ("api_key", "token", "auth", "secret", "password")
USAGE_TOKEN_KEYS = {
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "input_tokens",
    "max_tokens",
    "output_tokens",
    "total_tokens",
}


class AlfworldToolDispatchObserver:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    def on_call(self, tool_call: ToolCall) -> None:
        del tool_call
        self._outcome.agent_tool_call_count += 1

    def terminal_result(self, tool_call: ToolCall) -> ToolResultMessage | None:
        if not self._outcome.terminal or not tool_call.name.startswith("robot_"):
            return None
        payload = {
            "success": False,
            "error": "unclassified_execution_failure",
            "terminal": True,
            "classification": self._outcome.classification
            or "unclassified_execution_failure",
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
        classification = str(
            data.get("classification") or "unclassified_execution_failure"
        )
        self._outcome.mark_terminal(
            classification=classification,
            tool_call_id=tool_call.id,
            evidence_ref=_first_evidence_ref(data),
        )

    def on_exception(self, tool_call: ToolCall, error: Exception) -> ToolResultMessage:
        del error
        payload = {
            "success": False,
            "error": "unclassified_execution_failure",
            "terminal": True,
            "classification": "runtime_failure",
            "score_eligible": False,
            "detail": "Tool execution failed before a verified result was produced.",
        }
        self._outcome.mark_terminal(
            classification="runtime_failure",
            tool_call_id=tool_call.id,
        )
        return ToolResultMessage(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            content=[ContentBlock(text=json.dumps(payload, ensure_ascii=False, sort_keys=True))],
            is_error=True,
            data=payload,
        )


def _first_evidence_ref(data: Mapping[str, Any]) -> str | None:
    refs = data.get("evidence_refs")
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, str) and ref:
                return ref
    ref = data.get("evidence_ref")
    return ref if isinstance(ref, str) and ref else None


class AlfworldTraceWriter:
    def __init__(self, episode_dir: Path) -> None:
        self.episode_dir = episode_dir
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.episode_dir / "trace.jsonl"
        self.model_trace_path = self.episode_dir / "model_trace.jsonl"
        self.summary_path = self.episode_dir / "summary.json"
        self.trajectory_path = self.episode_dir / "trajectory.md"

    def write_event(self, event: dict[str, Any]) -> str:
        encoded = json.dumps(
            _redact(event),
            ensure_ascii=False,
            sort_keys=True,
        )
        with self.trace_path.open("a", encoding="utf-8") as writer:
            writer.write(encoded)
            writer.write("\n")
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        return f"{self.trace_path.name}#sha256={digest}"

    def write_model_event(self, event: dict[str, Any]) -> None:
        with self.model_trace_path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
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
                        {
                            "arguments": call.arguments,
                            "id": call.id,
                            "name": call.name,
                        }
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
            json.dumps(_redact(summary), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def write_trajectory(self, summary: dict[str, Any]) -> None:
        lines = [
            f"# ALFWorld Episode {summary.get('run_id', '')}",
            "",
            f"- episode_id: {summary.get('episode_id')}",
            f"- success: {summary.get('success')}",
            f"- failure_reason: {summary.get('failure_reason')}",
            f"- steps: {summary.get('steps')}",
            f"- invalid_actions: {summary.get('invalid_actions')}",
            f"- goal_condition_success_rate: {summary.get('goal_condition_success_rate')}",
            "",
            "## Model Trace",
            "",
        ]
        model_events = _read_jsonl(self.model_trace_path)
        assistant_index = 0
        for event in model_events:
            if event.get("event") == "episode_started":
                state = event.get("state", {})
                lines.extend([
                    "### Episode Started",
                    "",
                    f"- task: {state.get('task')}",
                    f"- observation: {state.get('observation')}",
                    f"- frame_path: {state.get('frame_path')}",
                    "",
                ])
            if event.get("event") in {"assistant_completed", "model.assistant_completed"}:
                assistant_index += 1
                payload = event.get("payload", event)
                lines.extend([
                    f"### Assistant Turn {assistant_index}",
                    "",
                    f"- finish_reason: {payload.get('finish_reason')}",
                    f"- usage: {json.dumps(payload.get('usage'), ensure_ascii=False)}",
                ])
                reasoning = payload.get("reasoning_content")
                if reasoning:
                    lines.extend(["", "Reasoning:", "", str(reasoning)])
                text = payload.get("text")
                if text:
                    lines.extend(["", "Text:", "", str(text)])
                tool_calls = payload.get("tool_calls") or []
                if tool_calls:
                    lines.extend(["", "Tool calls:", ""])
                    for call in tool_calls:
                        lines.append(
                            f"- {call.get('name')} "
                            f"{json.dumps(call.get('arguments'), ensure_ascii=False)}"
                        )
                lines.append("")
            if event.get("event") in {"tool_result", "tool.result"}:
                payload = event.get("payload", event)
                content = payload.get("content", [])
                image_paths = [
                    item.get("path")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "image"
                ]
                lines.extend([
                    "### Tool Result",
                    "",
                    f"- tool: {event.get('name') or payload.get('name')}",
                    f"- is_error: {payload.get('is_error')}",
                    f"- image_paths: {json.dumps(image_paths, ensure_ascii=False)}",
                    "",
                ])
        lines.extend(["## Environment Steps", ""])
        for step in _read_jsonl(self.trace_path):
            lines.extend([
                f"### Step {step.get('step_index')}",
                "",
                f"- tool_name: {step.get('tool_name')}",
                f"- tool_args: {json.dumps(step.get('tool_args'), ensure_ascii=False)}",
                f"- translated_command: {step.get('translated_command')}",
                f"- reward: {step.get('reward')}",
                f"- done: {step.get('done')}",
                f"- won: {step.get('won')}",
                f"- invalid_action_count: {step.get('invalid_action_count')}",
                f"- frame_path: {step.get('frame_path')}",
                "",
                "Feedback:",
                "",
                str(step.get("feedback") or step.get("observation") or ""),
                "",
            ])
        self.trajectory_path.write_text("\n".join(lines), encoding="utf-8")

def split_trace_bucket(split: str) -> str:
    mapping = {
        "train": "train",
        "valid_seen": "valid",
        "valid_unseen": "test",
    }
    if split not in mapping:
        raise ValueError(f"unsupported ALFWorld split: {split}")
    return mapping[split]


def write_readable_trajectories(run_dir: Path) -> Path:
    output = run_dir / "readable_trajectories.md"
    parts: list[str] = []
    for episode_dir in sorted(run_dir.glob("episode-*")):
        trajectory = episode_dir / "trajectory.md"
        if trajectory.exists():
            parts.append(trajectory.read_text(encoding="utf-8"))
    output.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
    return output


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _is_secret_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lower = key.lower()
    if lower in USAGE_TOKEN_KEYS:
        return False
    return any(fragment in lower for fragment in SECRET_KEY_FRAGMENTS)


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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows
