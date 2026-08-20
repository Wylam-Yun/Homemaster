"""Stateful Rich renderer adapted from locked OpenHarness ``ui/output.py``."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from homemaster.events.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)

_FAILURE_OUTPUT_LIMIT = 500
_TRUNCATED_FAILURE_MARKER = "[truncated; complete output remains in machine events]"


class RichOutputRenderer:
    """Own one model/tool spinner and one replaceable assistant Live region."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        style_name: str = "default",
        live_factory: Callable[..., Any] = Live,
        status_factory: Callable[[str], Any] | None = None,
        ascii_only: bool = False,
    ) -> None:
        self.console = console or Console(stderr=True)
        self._style_name = style_name
        self._live_factory = live_factory
        self._status_factory = status_factory
        self._ascii_only = ascii_only
        self._state = "idle"
        self._assistant_buffer = ""
        self._live: Any = None
        self._spinner: Any = None
        self._tool_inputs: dict[str, deque[dict[str, Any]]] = defaultdict(deque)

    @property
    def state(self) -> str:
        return self._state

    def model_request_started(self) -> None:
        if self._state == "closed":
            self._state = "idle"
        self._stop_spinner()
        self._state = "waiting-model"
        self._start_spinner("Model working...")

    def confirmation_requested(self) -> None:
        """Yield terminal ownership before the blocking approval prompt."""

        self._stop_spinner()
        self._stop_live()
        self._state = "waiting-confirmation"

    def render(self, event: StreamEvent) -> None:
        if self._state == "closed":
            return
        if isinstance(event, AssistantTextDelta):
            self._render_delta(event)
        elif isinstance(event, AssistantTurnComplete):
            self._render_assistant_complete(event)
        elif isinstance(event, ToolExecutionStarted):
            self._render_tool_started(event)
        elif isinstance(event, ToolExecutionCompleted):
            self._render_tool_completed(event)
        elif isinstance(event, ErrorEvent):
            self._stop_spinner()
            self._stop_live()
            self.console.print(
                Panel(Text(event.message), title="Error", border_style="red", padding=(0, 1))
            )
            self._state = "idle"
        elif isinstance(event, StatusEvent):
            self._stop_spinner()
            self.console.print(Text(self._system_line(event.message)))
        elif isinstance(event, CompactProgressEvent):
            self._stop_spinner()
            self.console.print(Text(self._system_line(event.message or _compact_label(event))))

    def close(self) -> None:
        if self._state == "closed":
            return
        self._stop_spinner()
        self._stop_live()
        self._tool_inputs.clear()
        self._state = "closed"

    def _render_delta(self, event: AssistantTextDelta) -> None:
        self._stop_spinner()
        self._assistant_buffer += event.text
        if self._live is None:
            self._live = self._live_factory(
                Text(self._assistant_buffer),
                console=self.console,
                auto_refresh=False,
                refresh_per_second=20,
                transient=False,
            )
            self._live.start()
        self._live.update(Text(self._assistant_buffer), refresh=True)
        self._state = "streaming-assistant"

    def _render_assistant_complete(self, event: AssistantTurnComplete) -> None:
        self._stop_spinner()
        final_text = event.message.text
        if self._live is None and final_text:
            self._live = self._live_factory(
                Markdown(final_text),
                console=self.console,
                auto_refresh=False,
                refresh_per_second=20,
                transient=False,
            )
            self._live.start()
        elif self._live is not None:
            self._live.update(Markdown(final_text), refresh=True)
        self._stop_live()
        self._assistant_buffer = ""
        self._state = "running-tools" if event.message.tool_calls else "idle"

    def _render_tool_started(self, event: ToolExecutionStarted) -> None:
        self._stop_spinner()
        self._stop_live()
        self._tool_inputs[event.tool_name].append(event.tool_input)
        summary = _summarize_tool_input(event.tool_input)
        suffix = f" {summary}" if summary else ""
        marker = ">" if self._ascii_only else "▶"
        self.console.print(Text(f"  {marker} {event.tool_name}{suffix}"))
        self._state = "running-tools"
        self._start_spinner(f"Running {event.tool_name}...")

    def _render_tool_completed(self, event: ToolExecutionCompleted) -> None:
        self._stop_spinner()
        queue = self._tool_inputs.get(event.tool_name)
        tool_input = queue.popleft() if queue else {}
        if queue is not None and not queue:
            self._tool_inputs.pop(event.tool_name, None)
        del tool_input
        if event.is_error:
            marker = "x" if self._ascii_only else "✗"
            detail = _failure_detail(event)
            suffix = f": {detail}" if detail else ""
            self.console.print(Text(f"{marker} 执行失败{suffix}"))
        else:
            marker = "+" if self._ascii_only else "✓"
            self.console.print(Text(f"{marker} 执行成功"))
        if self._tool_inputs:
            self._state = "running-tools"
            self._start_spinner("Running tools...")
        else:
            self._state = "idle"

    def _start_spinner(self, message: str) -> None:
        if self._style_name == "minimal":
            return
        self._stop_spinner()
        self._spinner = (
            self._status_factory(message)
            if self._status_factory is not None
            else self.console.status(message, spinner="dots")
        )
        self._spinner.start()

    def _stop_spinner(self) -> None:
        if self._spinner is None:
            return
        self._spinner.stop()
        self._spinner = None

    def _stop_live(self) -> None:
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    def _system_line(self, message: str) -> str:
        marker = "i" if self._ascii_only else "ℹ"
        return f"{marker} {message}"


def _summarize_tool_input(tool_input: dict[str, Any] | None) -> str:
    if not tool_input:
        return ""
    if len(tool_input) == 1:
        key, value = next(iter(tool_input.items()))
        return f"{key}={value}"
    return json.dumps(tool_input, ensure_ascii=False, separators=(",", ":"), default=str)


def _failure_detail(event: ToolExecutionCompleted) -> str:
    metadata = event.metadata or {}
    return_code = metadata.get("returncode", metadata.get("return_code"))
    parts = [f"returncode={return_code}"] if return_code is not None else []
    if event.output:
        output = event.output
        if len(output) > _FAILURE_OUTPUT_LIMIT:
            output = f"{output[:_FAILURE_OUTPUT_LIMIT]} {_TRUNCATED_FAILURE_MARKER}"
        parts.append(output)
    return " ".join(parts)


def _compact_label(event: CompactProgressEvent) -> str:
    if event.phase == "compact_start":
        return (
            "Context is too large. Compacting..."
            if event.trigger == "reactive"
            else "Compacting..."
        )
    if event.phase == "compact_end":
        return "Compaction complete."
    if event.phase == "compact_failed":
        return "Compaction failed."
    return "Compacting..."


__all__ = ["RichOutputRenderer"]
