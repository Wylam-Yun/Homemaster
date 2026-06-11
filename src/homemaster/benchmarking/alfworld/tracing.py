"""Trace output for ALFWorld benchmark episodes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SECRET_KEY_FRAGMENTS = (
    "api_key",
    "auth",
    "authorization",
    "bearer",
    "secret",
    "password",
)
SECRET_KEY_EXACT = {"token"}


class AlfworldTraceWriter:
    def __init__(self, episode_dir: Path) -> None:
        self.episode_dir = episode_dir
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.episode_dir / "trace.jsonl"
        self.model_trace_path = self.episode_dir / "model_trace.jsonl"
        self.readable_path = self.episode_dir / "trajectory.md"
        self.summary_path = self.episode_dir / "summary.json"

    def write_event(self, event: dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
            writer.write("\n")

    def write_model_event(self, event: dict[str, Any]) -> None:
        with self.model_trace_path.open("a", encoding="utf-8") as writer:
            writer.write(json.dumps(_redact(event), ensure_ascii=False, sort_keys=True))
            writer.write("\n")

    def write_summary(self, summary: dict[str, Any]) -> None:
        self.summary_path.write_text(
            json.dumps(_redact(summary), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def write_readable_trajectory(self, summary: dict[str, Any]) -> None:
        self.readable_path.write_text(
            _render_episode_markdown(
                summary=_redact(summary),
                env_events=_read_jsonl(self.trace_path),
                model_events=_read_jsonl(self.model_trace_path),
            ),
            encoding="utf-8",
        )


def write_run_readable_trajectories(run_dir: Path, summary: dict[str, Any]) -> Path:
    path = run_dir / "readable_trajectories.md"
    lines = [
        f"# ALFWorld Run {summary.get('run_id', run_dir.name)}",
        "",
        f"- episode_count: {summary.get('episode_count')}",
        f"- success_rate: {summary.get('success_rate')}",
        f"- average_steps: {summary.get('average_steps')}",
        f"- total_invalid_actions: {summary.get('total_invalid_actions')}",
        "",
    ]
    for index, episode in enumerate(summary.get("episodes", []), start=1):
        episode_dir = run_dir / f"episode-{index:04d}"
        trajectory_path = episode_dir / "trajectory.md"
        lines.extend([
            f"## Episode {index:04d}",
            "",
            f"- episode_id: {episode.get('episode_id')}",
            f"- success: {episode.get('success')}",
            f"- steps: {episode.get('steps')}",
            f"- invalid_actions: {episode.get('invalid_actions')}",
            f"- failure_reason: {episode.get('failure_reason')}",
            "",
        ])
        if trajectory_path.exists():
            text = trajectory_path.read_text(encoding="utf-8").strip()
            lines.append(text)
            lines.append("")
        else:
            lines.append(f"Missing readable trajectory: {trajectory_path}")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as reader:
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                events.append({"event_type": "invalid_jsonl", "raw_line": line})
            else:
                if isinstance(payload, dict):
                    events.append(payload)
    return events


def _render_episode_markdown(
    *,
    summary: dict[str, Any],
    env_events: list[dict[str, Any]],
    model_events: list[dict[str, Any]],
) -> str:
    task = _first_task(env_events, model_events)
    lines = [
        f"# Episode {summary.get('run_id', '')}",
        "",
        "## Outcome",
        "",
        f"- episode_id: {summary.get('episode_id')}",
        f"- success: {summary.get('success')}",
        f"- runtime_status: {summary.get('runtime_status')}",
        f"- failure_reason: {summary.get('failure_reason')}",
        f"- steps: {summary.get('steps')}",
        f"- invalid_actions: {summary.get('invalid_actions')}",
        f"- goal_condition_success_rate: {summary.get('goal_condition_success_rate')}",
        "",
        "## Task",
        "",
        _format_block(task or "(missing task text)"),
        "",
        "## Model And Tool Timeline",
        "",
    ]
    if model_events:
        lines.extend(_render_model_timeline(model_events))
    else:
        lines.append("(no model trace events)")
    lines.extend([
        "",
        "## Environment Steps",
        "",
    ])
    if env_events:
        lines.extend(_render_env_steps(env_events))
    else:
        lines.append("(no environment trace events)")
    lines.append("")
    return "\n".join(lines)


def _render_model_timeline(model_events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    assistant_turn = 0
    for event in model_events:
        event_type = event.get("event_type")
        if event_type == "episode_started":
            lines.extend([
                "- Episode started.",
                f"  - max_env_steps: {event.get('max_env_steps')}",
                f"  - max_invalid_actions: {event.get('max_invalid_actions')}",
                "",
            ])
            continue
        if event_type == "assistant_message":
            assistant_turn += 1
            message = _as_dict(event.get("message"))
            tool_calls = _as_list(message.get("tool_calls"))
            lines.extend([
                f"### Assistant Turn {assistant_turn}",
                "",
                f"- finish_reason: {message.get('finish_reason') or event.get('finish_reason')}",
                f"- elapsed_ms: {event.get('elapsed_ms')}",
                f"- usage: `{json.dumps(event.get('usage'), ensure_ascii=False)}`",
                "",
            ])
            text = _content_text(message)
            if text:
                lines.extend(["Model text:", "", _format_block(text), ""])
            reasoning = message.get("reasoning_content")
            if reasoning:
                lines.extend([
                    "Model reasoning returned by provider:",
                    "",
                    _format_block(str(reasoning)),
                    "",
                ])
            if tool_calls:
                lines.append("Tool calls:")
                for call in tool_calls:
                    call_dict = _as_dict(call)
                    lines.append(
                        "- "
                        f"{call_dict.get('name')} "
                        f"`{json.dumps(call_dict.get('arguments'), ensure_ascii=False)}`"
                    )
                lines.append("")
            continue
        if event_type == "tool_result":
            message = _as_dict(event.get("message"))
            data = _tool_result_data(message)
            lines.append(
                "- Tool result "
                f"`{event.get('name')}` "
                f"is_error={event.get('is_error')}"
            )
            if data:
                command = data.get("translated_command")
                feedback = data.get("feedback") or data.get("observation")
                won = data.get("won")
                step_index = data.get("step_index")
                if command:
                    lines.append(f"  - command: `{command}`")
                if step_index is not None:
                    lines.append(f"  - env_step: {step_index}")
                if won is not None:
                    lines.append(f"  - won: {won}")
                if feedback:
                    lines.append(f"  - feedback: {feedback}")
            lines.append("")
            continue
        event_json = json.dumps(event, ensure_ascii=False)
        lines.append(f"- {event_type or 'unknown_event'}: `{event_json}`")
    return lines


def _render_env_steps(env_events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for event in env_events:
        step = event.get("step_index")
        tool_name = event.get("tool_name")
        command = event.get("translated_command")
        feedback = event.get("feedback") or event.get("observation")
        won = event.get("won")
        reward = event.get("reward")
        lines.append(
            f"{step}. {tool_name} -> `{command}` "
            f"(reward={reward}, won={won})"
        )
        if feedback:
            lines.append(f"   {feedback}")
    return lines


def _first_task(
    env_events: list[dict[str, Any]],
    model_events: list[dict[str, Any]],
) -> str | None:
    for event in env_events:
        task = event.get("task")
        if task:
            return str(task)
    for event in model_events:
        state = _as_dict(event.get("state"))
        task = state.get("task")
        if task:
            return str(task)
    return None


def _content_text(message: dict[str, Any]) -> str:
    parts = []
    for block in _as_list(message.get("content")):
        block_dict = _as_dict(block)
        text = block_dict.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(parts)


def _tool_result_data(message: dict[str, Any]) -> dict[str, Any]:
    data = _as_dict(message.get("data"))
    if data:
        return data
    text = _content_text(message)
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"content": text}
    return payload if isinstance(payload, dict) else {"content": payload}


def _format_block(text: str) -> str:
    return f"```text\n{text}\n```"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _redact(value: Any, key: str | None = None) -> Any:
    if key is not None:
        normalized_key = key.lower()
        if normalized_key in SECRET_KEY_EXACT or any(
            fragment in normalized_key for fragment in SECRET_KEY_FRAGMENTS
        ):
            return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
