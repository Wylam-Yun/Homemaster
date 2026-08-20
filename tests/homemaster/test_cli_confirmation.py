from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.cli.confirmation import CliConfirmationHandler, CliPermissionMode
from homemaster.permissions import PermissionMode
from homemaster.tools import ToolExecutionContext
from homemaster.tools.contracts import PermissionSubject


class _EventSink:
    def __init__(self) -> None:
        self.events = []

    async def aemit(self, event) -> None:
        self.events.append(event)


def _context(tmp_path: Path, sink: _EventSink) -> ToolExecutionContext:
    return ToolExecutionContext(
        tmp_path,
        metadata={
            "session_id": "session-1",
            "run_id": "run-1",
            "turn_index": 3,
            "tool_call_id": "call-1",
            "permission_subject": PermissionSubject("operator-1", "cli", capabilities=()),
            "run_context": SimpleNamespace(event_sink=sink),
        },
    )


def test_cli_permission_modes_map_to_existing_policy_modes() -> None:
    assert CliPermissionMode.FULL_AUTO.policy_mode is PermissionMode.FULL_AUTO
    assert CliPermissionMode.CONFIRM.policy_mode is PermissionMode.DEFAULT
    assert CliPermissionMode.PLAN.policy_mode is PermissionMode.PLAN


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES "])
async def test_confirmation_accepts_only_explicit_yes(tmp_path: Path, answer: str) -> None:
    handler = CliConfirmationHandler(input_fn=lambda prompt: answer, output_fn=lambda value: None)

    approved = await handler.confirm(
        SimpleNamespace(name="write_note"),
        {"value": "hello"},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is True


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["", "n", "no", "true", "1"])
async def test_confirmation_denies_every_other_answer(tmp_path: Path, answer: str) -> None:
    handler = CliConfirmationHandler(input_fn=lambda prompt: answer, output_fn=lambda value: None)

    approved = await handler.confirm(
        SimpleNamespace(name="write_note"),
        {},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [EOFError(), OSError("stdin unavailable"), KeyboardInterrupt()])
async def test_confirmation_input_failures_deny(tmp_path: Path, error: BaseException) -> None:
    def fail(prompt: str) -> str:
        raise error

    handler = CliConfirmationHandler(input_fn=fail, output_fn=lambda value: None)

    approved = await handler.confirm(
        SimpleNamespace(name="write_note"),
        {},
        _context(tmp_path, _EventSink()),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is False


@pytest.mark.asyncio
async def test_confirmation_emits_complete_structured_audit(tmp_path: Path) -> None:
    sink = _EventSink()
    output = []
    handler = CliConfirmationHandler(input_fn=lambda prompt: "yes", output_fn=output.append)

    approved = await handler.confirm(
        SimpleNamespace(name="write_note"),
        {"path": "note.txt", "value": "hello"},
        _context(tmp_path, sink),
        SimpleNamespace(reason="confirmation required"),
    )

    assert approved is True
    assert len(sink.events) == 2
    requested, completed = sink.events
    assert (requested.type, completed.type) == (
        "permission.confirmation_requested",
        "permission.confirmation_completed",
    )
    for event in sink.events:
        assert event.session_id == "session-1"
        assert event.run_id == "run-1"
        assert event.turn_index == 3
        assert event.tool_call_id == "call-1"
        assert event.name == "write_note"
        assert event.payload["subject_id"] == "operator-1"
    assert requested.payload == {
        "arguments": {"path": "note.txt", "value": "hello"},
        "cwd": str(tmp_path.resolve()),
        "reason": "confirmation required",
        "subject_id": "operator-1",
    }
    assert completed.payload == {
        "approved": True,
        "outcome": "approved",
        "subject_id": "operator-1",
    }
    assert "Tool: write_note" in output[0]


@pytest.mark.asyncio
async def test_concurrent_confirmations_serialize_stdin_access(tmp_path: Path) -> None:
    active = 0
    max_active = 0
    guard = threading.Lock()

    def input_fn(prompt: str) -> str:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with guard:
            active -= 1
        return "yes"

    handler = CliConfirmationHandler(input_fn=input_fn, output_fn=lambda value: None)
    context = _context(tmp_path, _EventSink())

    results = await asyncio.gather(
        *(
            handler.confirm(
            SimpleNamespace(name=f"write_{index}"),
            {},
            context,
            SimpleNamespace(reason="confirmation required"),
            )
            for index in range(3)
        )
    )

    assert results == [True, True, True]
    assert max_active == 1
