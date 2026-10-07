"""Render server WebEvent dicts onto the terminal with Rich.

Mirrors the browser projection vocabulary (``web/src/protocol/events.ts``):
streamed ``answer.delta`` text, one folded line for ``thinking.*``, compact
``tool.started``/``tool.*`` summary rows, an ``approval.requested`` card,
``question.asked`` prompts, ``run.*`` terminals and error surfaces.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from homemaster.cli.client import RESYNC_EVENT_TYPE

TERMINAL_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})
_INTERACTIVE_EVENT_TYPES = frozenset({"approval.requested", "question.asked"})
# Events that stay silent: handshake bookkeeping and usage meter the toolbar
# reads instead of echoing.
_SILENT_EVENT_TYPES = frozenset(
    {"request.accepted", "run.started", "permission.grants_changed", "usage.updated"}
)
_THINKING_EVENT_TYPES = frozenset({"thinking.delta", "thinking.snapshot"})
_FAILURE_OUTPUT_LIMIT = 500
_TRUNCATED_MARKER = "[truncated]"


def is_terminal_event(event: dict[str, Any]) -> bool:
    return event.get("type") in TERMINAL_EVENT_TYPES


def is_interactive_event(event: dict[str, Any]) -> bool:
    return event.get("type") in _INTERACTIVE_EVENT_TYPES


def terminal_status(event: dict[str, Any]) -> str:
    """Map a terminal WebEvent to a RunStatus-compatible status string."""

    etype = event.get("type")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if etype == "run.cancelled":
        return "cancelled"
    if etype == "run.failed":
        return "failed"
    status = str(payload.get("status") or "completed")
    return status


def terminal_exit_code(event: dict[str, Any] | None) -> int:
    """Exit-code contract shared by ``run``/``-p`` — mirrors result_exit_code."""

    if event is None:
        return 1
    status = terminal_status(event)
    if status in {"replied", "completed", "waiting_user"}:
        return 0
    if status == "cancelled":
        return 130
    return 1


def event_final_reply(event: dict[str, Any]) -> str | None:
    payload = event.get("payload")
    if isinstance(payload, dict):
        reply = payload.get("final_reply")
        if isinstance(reply, str) and reply:
            return reply
    return None


class EventRenderer:
    """Stateful WebEvent → terminal renderer (stream deltas, folded thinking)."""

    def __init__(
        self,
        *,
        console: Console | None = None,
        answer_stream: TextIO | None = None,
        show_thinking: bool = True,
        show_tool_lines: bool = True,
        ascii_only: bool = False,
    ) -> None:
        self.console = console or Console()
        # Answer text goes to ``answer_stream`` when set (``-p`` keeps stdout
        # clean of chrome); otherwise it streams through the console itself.
        self._answer_stream = answer_stream
        self._show_thinking = show_thinking
        self._show_tool_lines = show_tool_lines
        self._ascii_only = ascii_only
        self._streamed_answer = ""
        self._thinking_chars = 0
        self._thinking_excerpt = ""
        self._flushed_thinking_chars = -1
        self._flushed_thinking_excerpt = ""
        self.last_usage: dict[str, int] = {}

    # -- public surface -------------------------------------------------
    def render(self, event: dict[str, Any]) -> None:
        etype = event.get("type")
        payload = event.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        if etype in _THINKING_EVENT_TYPES:
            self._accumulate_thinking(
                payload, snapshot=etype.endswith(".snapshot")
            )
            return
        # Anything that is not a thinking chunk ends the folded thought line.
        self._flush_thinking()
        if etype == "answer.delta":
            self._write_answer(str(payload.get("text") or ""))
        elif etype == "answer.snapshot":
            self._write_snapshot(str(payload.get("text") or ""))
        elif etype == "tool.started":
            self._render_tool_started(payload)
        elif etype == "tool.completed" or etype == "tool.failed":
            self._render_tool_terminal(etype, payload)
        elif etype == "approval.requested":
            self.render_approval_card(payload)
        elif etype == "approval.resolved":
            self._render_approval_resolved(payload)
        elif etype == "question.asked":
            self._render_question(payload)
        elif etype == "question.answered":
            self.console.print("[dim]question answered[/dim]")
        elif etype == "run.completed":
            reply = event_final_reply(event)
            if reply is not None:
                self.finish(reply)
            status = str(payload.get("status") or "")
            if status and status not in {"replied", "completed"}:
                self.console.print(f"[dim]run {escape(status)}[/dim]")
        elif etype == "run.failed":
            message = str(payload.get("message") or payload.get("code") or "run failed")
            self.console.print(
                Panel(Text(message), title="Error", border_style="red", padding=(0, 1))
            )
        elif etype == "run.cancelled":
            self.console.print("[yellow]run cancelled[/yellow]")
        elif etype == "context.compacted":
            self.console.print("[dim]context compacted[/dim]")
        elif etype == "session.mode_changed":
            mode = str(payload.get("ui_mode") or "?")
            self.console.print(f"[dim]mode → {escape(mode)}[/dim]")
        elif etype == RESYNC_EVENT_TYPE:
            self.console.print("[yellow]connection restored; state resynced[/yellow]")
        elif etype == "usage.updated":
            self.last_usage = {
                str(k): v for k, v in payload.items() if isinstance(v, int)
            }
        elif etype in _SILENT_EVENT_TYPES:
            return
        else:
            self.console.print(f"[dim]event: {escape(str(etype))}[/dim]")

    def render_approval_card(self, payload: dict[str, Any]) -> None:
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        lines = Text()
        summary = str(payload.get("intent_summary") or "").strip()
        if summary:
            lines.append(summary + "\n", style="bold")
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get("action_label") or "action")
            name = str(item.get("display_name") or item.get("item_id") or "")
            location = str(item.get("location") or "")
            suffix = f" ({location})" if location else ""
            lines.append(f"  • {label}: {name}{suffix}\n")
        self.console.print(
            Panel(lines, title="Approval required", border_style="yellow", padding=(0, 1))
        )

    def finish(self, final_reply: str) -> None:
        """Write the part of ``final_reply`` not already streamed by deltas.

        Idempotent: text written here is recorded as streamed so a second
        ``finish`` (e.g. the terminal event render followed by the run
        summary) never double-prints an unstreamed reply.
        """

        self._flush_thinking()
        if self._streamed_answer and final_reply.startswith(self._streamed_answer):
            suffix = final_reply[len(self._streamed_answer) :]
            if suffix:
                self._write_raw(suffix)
            self._streamed_answer = final_reply
            return
        if not self._streamed_answer and final_reply:
            self._write_raw(final_reply)
            self._streamed_answer = final_reply

    def flush(self) -> None:
        self._flush_thinking()

    def close(self) -> None:
        self._flush_thinking()

    # -- internals ------------------------------------------------------
    def _write_raw(self, text: str) -> None:
        if self._answer_stream is not None:
            self._answer_stream.write(text)
            self._answer_stream.flush()
        else:
            self.console.print(text, end="", markup=False, highlight=False)

    def _write_answer(self, text: str) -> None:
        if not text:
            return
        self._streamed_answer += text
        self._write_raw(text)

    def _write_snapshot(self, text: str) -> None:
        if not text:
            return
        if self._streamed_answer and text.startswith(self._streamed_answer):
            suffix = text[len(self._streamed_answer) :]
            if suffix:
                self._write_raw(suffix)
            self._streamed_answer = text
            return
        if not self._streamed_answer:
            self._write_raw(text)
            self._streamed_answer = text

    def _accumulate_thinking(self, payload: dict[str, Any], *, snapshot: bool = False) -> None:
        if not self._show_thinking:
            return
        text = str(payload.get("text") or "")
        if not text:
            return
        if snapshot:
            # Snapshot supersedes accumulated deltas — reset, don't add.
            self._thinking_chars = len(text)
            self._thinking_excerpt = text.strip().splitlines()[0][:120]
            return
        self._thinking_chars += len(text)
        if not self._thinking_excerpt:
            self._thinking_excerpt = text.strip().splitlines()[0][:120]

    def _flush_thinking(self) -> None:
        if self._thinking_chars <= 0:
            return
        excerpt = self._thinking_excerpt
        # A trailing thinking.snapshot repeats the folded deltas — print only
        # when the excerpt actually changed (or grew).
        if (
            self._thinking_chars == self._flushed_thinking_chars
            or excerpt == self._flushed_thinking_excerpt
        ):
            self._thinking_chars = 0
            self._thinking_excerpt = ""
            return
        suffix = "…" if len(excerpt) >= 120 else ""
        self.console.print(
            f"[dim]thought {self._thinking_chars} chars: "
            f"{escape(excerpt)}{suffix}[/dim]"
        )
        self._flushed_thinking_chars = self._thinking_chars
        self._flushed_thinking_excerpt = excerpt
        self._thinking_chars = 0
        self._thinking_excerpt = ""

    def _render_tool_started(self, payload: dict[str, Any]) -> None:
        if not self._show_tool_lines:
            return
        name = str(payload.get("name") or "tool")
        summary = _summarize_arguments(payload.get("arguments"))
        marker = ">" if self._ascii_only else "▶"
        suffix = f" {escape(summary)}" if summary else ""
        self.console.print(f"  [cyan]{marker} {escape(name)}[/cyan]{suffix}")

    def _render_tool_terminal(self, etype: str, payload: dict[str, Any]) -> None:
        if not self._show_tool_lines:
            return
        name = str(payload.get("name") or "tool")
        if etype == "tool.failed":
            marker = "x" if self._ascii_only else "✗"
            output = str(payload.get("output") or "")
            if len(output) > _FAILURE_OUTPUT_LIMIT:
                output = f"{output[:_FAILURE_OUTPUT_LIMIT]} {_TRUNCATED_MARKER}"
            detail = f": {escape(output)}" if output else ""
            self.console.print(f"  [red]{marker} {escape(name)} failed{detail}[/red]")
        else:
            marker = "+" if self._ascii_only else "✓"
            self.console.print(f"  [green]{marker} {escape(name)}[/green]")

    def _render_approval_resolved(self, payload: dict[str, Any]) -> None:
        status = str(
            payload.get("request_status") or payload.get("outcome") or "resolved"
        )
        self.console.print(f"[dim]approval {escape(status)}[/dim]")

    def _render_question(self, payload: dict[str, Any]) -> None:
        question = str(payload.get("question") or "")
        self.console.print(Panel(Text(question), title="Question", border_style="cyan"))


def _summarize_arguments(arguments: Any) -> str:
    if not isinstance(arguments, dict) or not arguments:
        return ""
    if len(arguments) == 1:
        key, value = next(iter(arguments.items()))
        text = f"{key}={value}"
    else:
        text = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) > 160:
        text = text[:160] + "…"
    return text


def stdout_renderer(**kwargs: Any) -> EventRenderer:
    """Renderer writing chrome to stdout — used by ``run`` and the shell."""

    return EventRenderer(console=Console(file=sys.stdout), **kwargs)


__all__ = [
    "EventRenderer",
    "TERMINAL_EVENT_TYPES",
    "event_final_reply",
    "is_interactive_event",
    "is_terminal_event",
    "stdout_renderer",
    "terminal_exit_code",
    "terminal_status",
]
