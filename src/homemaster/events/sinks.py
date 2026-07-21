"""Runtime event sinks for trace, messages, console, and fanout output."""

from __future__ import annotations

import inspect
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from homemaster.events.runtime_events import RuntimeEvent
from homemaster.events.sanitizer import sanitize_event_payload, sanitize_for_trace


class JsonlTraceSink:
    """Append RuntimeEvents to JSONL with redacted but untruncated payloads."""

    def __init__(self, output_dir: Path, *, filename: str = "runtime_events.jsonl") -> None:
        self._output_dir = output_dir
        self._filename = filename
        self._events: list[RuntimeEvent] = []
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._output_dir / self._filename
        self._handle = self._path.open("a", encoding="utf-8")

    def emit(self, event: RuntimeEvent) -> None:
        """Append event to in-memory list and write to JSONL file."""
        self._events.append(event)
        entry = _event_to_trace_dict(event)
        self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    @property
    def events(self) -> list[RuntimeEvent]:
        return list(self._events)


JsonlEventSink = JsonlTraceSink


class NullEventSink:
    """No-op sink. Accepts events silently, returns empty list."""

    def emit(self, event: RuntimeEvent) -> None:
        pass

    @property
    def events(self) -> list[RuntimeEvent]:
        return []


class MessagesLogSink:
    """Write user/assistant message-like events to messages.jsonl."""

    def __init__(self, output_dir: Path, *, filename: str = "messages.jsonl") -> None:
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._output_dir / filename

    def emit(self, event: RuntimeEvent) -> None:
        role = None
        content = None
        if event.type == "runtime.turn_started":
            role = "user"
            content = event.payload.get("user_text")
        elif event.type == "assistant.reply":
            role = "assistant"
            content = event.payload.get("reply")
        if role is None or not content:
            return
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "role": role,
                "content": content,
                "run_id": event.run_id,
                "session_id": event.session_id,
                "timestamp": event.timestamp,
            }, ensure_ascii=False) + "\n")

    @property
    def events(self) -> list[RuntimeEvent]:
        return []


_CONSOLE_EVENT_TYPES: frozenset[str] = frozenset({
    "runtime.turn_failed",
    "runtime.budget_exhausted",
    "transport.request_failed",
    "assistant.thinking",
    "assistant.reply",
    "context.compaction",
    "tool.call_started",
    "tool.call_completed",
    "tool.call_failed",
})


class ConsoleEventSink:
    """Render medium-granularity runtime events to stderr."""

    def __init__(
        self,
        *,
        verbose: bool = False,
        quiet: bool = False,
        show_replies: bool = True,
    ) -> None:
        self._console: Any = None
        self._verbose = verbose
        self._quiet = quiet
        self._show_replies = show_replies

    def _get_console(self) -> Any:
        if self._console is None:
            from rich.console import Console
            self._console = Console(file=sys.stderr, highlight=False, markup=False)
        return self._console

    def emit(self, event: RuntimeEvent) -> None:
        if self._quiet:
            return
        if event.type not in _CONSOLE_EVENT_TYPES and not event.type.endswith("_failed"):
            return
        console = self._get_console()
        rendered = self._render_event(event)
        if rendered:
            console.print(rendered)

    @property
    def events(self) -> list[RuntimeEvent]:
        return []

    def _render_event(self, event: RuntimeEvent) -> str:
        iteration = event.payload.get("iteration")
        prefix = f"[iter {iteration}] " if iteration is not None else ""
        if event.type == "assistant.thinking":
            thinking = str(event.payload.get("thinking") or "")
            if not self._verbose:
                thinking = _first_line(thinking)
            return f"{prefix}[模型思考] {thinking}"
        if event.type == "assistant.reply":
            if not self._show_replies:
                return ""
            reply = str(event.payload.get("reply") or "")
            if not self._verbose:
                reply = _first_line(reply)
            return f"{prefix}[模型回复] {reply}"
        if event.type == "tool.call_started":
            arguments = _compact_json(event.payload.get("arguments", {}), max_chars=240)
            suffix = f": {arguments}" if arguments else ""
            return f"{prefix}[工具调用] {event.name or 'unknown'}{suffix}"
        if event.type in {"tool.call_completed", "tool.call_failed"}:
            result = str(event.payload.get("result") or "")
            if not result:
                result = _compact_json(event.payload.get("data", {}), max_chars=240)
            if not self._verbose:
                result = _first_line(result, max_chars=240)
            label = "工具失败" if event.type.endswith("failed") else "工具结果"
            duration = f" ({event.duration_ms:.0f}ms)" if event.duration_ms is not None else ""
            return f"{prefix}[{label}] {event.name or 'unknown'}{duration}: {result}"
        if event.type == "context.compaction":
            trigger = event.payload.get("trigger") or "auto"
            kind = event.payload.get("kind") or "summary"
            after_tokens = event.payload.get("after_tokens")
            token_text = f", after_tokens={after_tokens}" if after_tokens is not None else ""
            return f"{prefix}[上下文压缩] trigger={trigger}, kind={kind}{token_text}"
        if event.type == "runtime.turn_failed":
            error = event.payload.get("error") or event.payload.get("error_code") or "unknown error"
            return f"{prefix}[运行失败] {error}"
        if event.type == "runtime.budget_exhausted":
            return f"{prefix}[运行失败] 达到最大工具迭代次数"
        if event.type == "transport.request_failed":
            error = (
                event.payload.get("error")
                or event.payload.get("error_type")
                or "request failed"
            )
            return f"{prefix}[模型请求失败] {error}"
        if event.type.endswith("_failed"):
            error = event.payload.get("error") or event.payload.get("error_code") or event.type
            return f"{prefix}[运行失败] {error}"
        return ""


class VerboseConsoleEventSink(ConsoleEventSink):
    """Render complete thinking/tool-result console output."""

    def __init__(self, *, show_replies: bool = True) -> None:
        super().__init__(verbose=True, show_replies=show_replies)


class FanoutEventSink:
    """Forwards emit() to all wrapped sinks. events from first sink."""

    def __init__(self, sinks: list[Any]) -> None:
        self._sinks = sinks

    def emit(self, event: RuntimeEvent) -> None:
        for sink in self._sinks:
            sink.emit(event)

    async def aemit(self, event: RuntimeEvent) -> None:
        for sink in self._sinks:
            aemit = getattr(sink, "aemit", None)
            if callable(aemit):
                await aemit(event)
                continue
            value = sink.emit(event)
            if inspect.isawaitable(value):
                await value

    @property
    def events(self) -> list[RuntimeEvent]:
        if self._sinks:
            return self._sinks[0].events
        return []


def _event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    """Serialize RuntimeEvent to dict with all fields and sanitized payload."""
    entry = asdict(event)
    entry["payload"] = sanitize_event_payload(event.payload)
    return entry


def _event_to_trace_dict(event: RuntimeEvent) -> dict[str, Any]:
    entry = asdict(event)
    entry["payload"] = sanitize_for_trace(event.payload)
    return entry


def _first_line(value: str, *, max_chars: int = 200) -> str:
    line = value.splitlines()[0] if value else ""
    if len(line) <= max_chars:
        return line
    return line[:max_chars] + "..."


def _compact_json(value: Any, *, max_chars: int) -> str:
    if value in (None, "", {}, []):
        return ""
    try:
        rendered = json.dumps(sanitize_event_payload(value), ensure_ascii=False, sort_keys=True)
    except TypeError:
        rendered = str(sanitize_event_payload(value))
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars] + "..."
