"""Opt-in local confirmation for the interactive HomeMaster CLI."""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import typer

from homemaster.events.logger import get_logger
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.permissions import PermissionMode


class CliPermissionMode(StrEnum):
    FULL_AUTO = "full_auto"
    CONFIRM = "confirm"
    PLAN = "plan"

    @property
    def policy_mode(self) -> PermissionMode:
        if self is CliPermissionMode.CONFIRM:
            return PermissionMode.DEFAULT
        return PermissionMode(self.value)


class CliConfirmationHandler:
    """Serialize local approval prompts and fail closed on non-affirmative input."""

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], Any] = typer.echo,
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self._lock = asyncio.Lock()

    async def confirm(
        self,
        tool: Any,
        arguments: dict[str, Any],
        context: Any,
        decision: Any,
    ) -> bool:
        async with self._lock:
            await _emit_confirmation_event(
                context,
                event_type="permission.confirmation_requested",
                tool_name=str(tool.name),
                payload={
                    "arguments": arguments,
                    "cwd": str(context.cwd),
                    "reason": str(decision.reason),
                    "subject_id": _subject_id(context),
                },
            )
            self._output(
                "\n".join(
                    (
                        "Approval required",
                        f"Tool: {tool.name}",
                        f"Working directory: {context.cwd}",
                        "Arguments:",
                        json.dumps(arguments, ensure_ascii=False, indent=2, sort_keys=True),
                    )
                )
            )
            approved = False
            outcome = "denied"
            try:
                response = await _read_input(self._input, "Execute? [y/N]: ")
                approved = response.strip().casefold() in {"y", "yes"}
                outcome = "approved" if approved else "denied"
            except EOFError:
                outcome = "eof"
            except (Exception, KeyboardInterrupt) as exc:
                outcome = f"input_error:{type(exc).__name__}"
            await _emit_confirmation_event(
                context,
                event_type="permission.confirmation_completed",
                tool_name=str(tool.name),
                payload={
                    "approved": approved,
                    "outcome": outcome,
                    "subject_id": _subject_id(context),
                },
            )
            return approved


async def _emit_confirmation_event(
    context: Any,
    *,
    event_type: str,
    tool_name: str,
    payload: dict[str, Any],
) -> None:
    run_context = context.metadata.get("run_context")
    sink = getattr(run_context, "event_sink", None)
    if sink is None:
        return
    event = RuntimeEvent(
        type=event_type,
        session_id=str(context.metadata.get("session_id", "")),
        run_id=str(context.metadata.get("run_id", "")),
        turn_index=context.metadata.get("turn_index"),
        tool_call_id=str(context.metadata.get("tool_call_id", "")) or None,
        name=tool_name,
        payload=payload,
    )
    try:
        emit = getattr(sink, "aemit", None)
        if callable(emit):
            await emit(event)
            return
        value = sink.emit(event)
        if inspect.isawaitable(value):
            await value
    except Exception as exc:
        get_logger().warning(
            json.dumps(
                {
                    "event": "permission.confirmation_audit_failed",
                    "event_type": event_type,
                    "exception_type": type(exc).__name__,
                },
                sort_keys=True,
            )
        )


def _subject_id(context: Any) -> str:
    subject = context.metadata.get("permission_subject")
    return str(getattr(subject, "subject_id", ""))


async def _read_input(input_fn: Callable[[str], str], prompt: str) -> str:
    completed = threading.Event()
    result: list[str] = []
    errors: list[BaseException] = []

    def read() -> None:
        try:
            result.append(input_fn(prompt))
        except BaseException as exc:
            errors.append(exc)
        finally:
            completed.set()

    threading.Thread(target=read, name="homemaster-cli-confirmation", daemon=True).start()
    while not completed.is_set():
        await asyncio.sleep(0.01)
    if errors:
        raise errors[0]
    return result[0]


__all__ = ["CliConfirmationHandler", "CliPermissionMode"]
