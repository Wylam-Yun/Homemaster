"""Tests for WebEvent → terminal rendering."""

from __future__ import annotations

import io

from rich.console import Console

from homemaster.cli.client import RESYNC_EVENT_TYPE
from homemaster.cli.event_renderer import (
    EventRenderer,
    is_interactive_event,
    is_terminal_event,
    terminal_exit_code,
    terminal_status,
)


def _ev(etype: str, **payload) -> dict:
    return {
        "type": etype,
        "session_id": "s1",
        "run_id": "r1",
        "request_id": "q1",
        "payload": payload,
    }


def _renderer() -> tuple[EventRenderer, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, color_system=None, force_terminal=False)
    return EventRenderer(console=console), buffer


def test_answer_delta_streams_text_and_completed_appends_suffix() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("answer.delta", text="hello"))
    renderer.render(_ev("answer.delta", text=" world"))
    renderer.render(_ev("run.completed", status="replied", final_reply="hello world!"))
    assert buffer.getvalue() == "hello world!"


def test_completed_with_fresh_reply_prints_full_reply() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("run.completed", status="replied", final_reply="full reply"))
    assert "full reply" in buffer.getvalue()


def test_thinking_folds_into_single_summary_line() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("thinking.delta", text="first thought line\nrest"))
    renderer.render(_ev("thinking.delta", text="more"))
    renderer.render(_ev("answer.delta", text="answer"))
    lines = buffer.getvalue().splitlines()
    thinking_lines = [line for line in lines if "thought" in line]
    assert len(thinking_lines) == 1
    assert "first thought line" in thinking_lines[0]
    assert "chars" in thinking_lines[0]


def test_thinking_snapshot_replaces_delta_fold_instead_of_duping() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("thinking.delta", text="plan the steps"))
    renderer.render(_ev("answer.delta", text="a"))
    # Snapshot of the same thought arriving late must not print a second fold.
    renderer.render(_ev("thinking.snapshot", text="plan the steps"))
    renderer.render(_ev("run.completed", status="replied", final_reply="a"))
    assert buffer.getvalue().count("thought") == 1


def test_tool_started_and_terminal_render_summary_rows() -> None:
    renderer, buffer = _renderer()
    renderer.render(
        _ev("tool.started", tool_call_id="t1", name="terminal", arguments={"command": "ls"})
    )
    renderer.render(_ev("tool.completed", tool_call_id="t1", name="terminal", output="ok"))
    renderer.render(_ev("tool.failed", tool_call_id="t2", name="bash", output="crash"))
    out = buffer.getvalue()
    assert "terminal" in out
    assert "command=ls" in out
    assert "bash" in out
    assert "crash" in out


def test_tool_failure_output_is_truncated() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("tool.failed", name="bash", output="x" * 5000))
    out = buffer.getvalue()
    assert "[truncated]" in out
    assert len(out) < 1200


def test_approval_requested_renders_card() -> None:
    renderer, buffer = _renderer()
    renderer.render(
        _ev(
            "approval.requested",
            approval_id="a1",
            intent_summary="run 2 commands",
            items=[
                {
                    "item_id": "i1",
                    "action_label": "execute",
                    "display_name": "terminal",
                    "location": "local",
                }
            ],
        )
    )
    out = buffer.getvalue()
    assert "Approval required" in out
    assert "run 2 commands" in out
    assert "terminal" in out


def test_question_and_run_failed_render_prompt_and_error() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("question.asked", question_id="q1", question="pick one?"))
    renderer.render(_ev("run.failed", code="provider_error", message="upstream blew up"))
    out = buffer.getvalue()
    assert "pick one?" in out
    assert "upstream blew up" in out


def test_silent_and_meta_events_do_not_print_chrome() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("request.accepted"))
    renderer.render(_ev("run.started"))
    renderer.render(_ev("usage.updated", total_tokens=42))
    assert buffer.getvalue() == ""
    assert renderer.last_usage == {"total_tokens": 42}


def test_resync_marker_surfaces_reconnect_notice() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev(RESYNC_EVENT_TYPE, resync=True))
    assert "resynced" in buffer.getvalue()


def test_mode_change_announcement() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("session.mode_changed", ui_mode="plan"))
    assert "plan" in buffer.getvalue()


def test_terminal_classification_and_exit_codes() -> None:
    completed = _ev("run.completed", status="replied", final_reply="x")
    failed = _ev("run.failed", code="c", message="m")
    cancelled = _ev("run.cancelled")
    waiting = _ev("run.completed", status="waiting_user", final_reply="?")

    assert is_terminal_event(completed) and is_terminal_event(failed)
    assert is_terminal_event(cancelled) and not is_terminal_event(_ev("answer.delta", text="x"))
    assert is_interactive_event(_ev("approval.requested", approval_id="a"))
    assert is_interactive_event(_ev("question.asked", question_id="q"))
    assert not is_interactive_event(completed)

    assert terminal_status(completed) == "replied"
    assert terminal_status(failed) == "failed"
    assert terminal_status(cancelled) == "cancelled"
    assert terminal_exit_code(completed) == 0
    assert terminal_exit_code(waiting) == 0
    assert terminal_exit_code(failed) == 1
    assert terminal_exit_code(cancelled) == 130
    assert terminal_exit_code(None) == 1


def test_finish_does_not_duplicate_streamed_text() -> None:
    renderer, buffer = _renderer()
    renderer.render(_ev("answer.delta", text="partial"))
    renderer.finish("partial")
    assert buffer.getvalue() == "partial"
    renderer2, buffer2 = _renderer()
    renderer2.render(_ev("answer.delta", text=""))
    renderer2.finish("new")
    assert buffer2.getvalue() == "new"


def test_finish_is_idempotent_for_unstreamed_reply() -> None:
    """A reply rendered by run.completed must not reprint on a later finish —

    the remote run path calls ``finish`` once for the terminal event and once
    for the final summary, and a reply recovered after a reconnect (no deltas
    were ever streamed) must appear exactly once.
    """

    renderer, buffer = _renderer()
    renderer.render(_ev("run.completed", status="replied", final_reply="whole reply"))
    renderer.finish("whole reply")
    assert buffer.getvalue() == "whole reply"


def test_answer_stream_redirects_answer_text_only() -> None:
    chrome = io.StringIO()
    answer = io.StringIO()
    console = Console(file=chrome, width=120)
    renderer = EventRenderer(console=console, answer_stream=answer)
    renderer.render(_ev("answer.delta", text="reply text"))
    renderer.render(_ev("tool.started", name="terminal", arguments={}))
    assert answer.getvalue() == "reply text"
    assert "reply text" not in chrome.getvalue()
    assert "terminal" in chrome.getvalue()
