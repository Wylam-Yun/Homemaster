"""Opt-in local confirmation for the interactive HomeMaster CLI."""

from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from collections.abc import Callable
from enum import StrEnum
from typing import Any

import typer

from homemaster.events.logger import get_logger
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.permissions import PermissionMode
from homemaster.permissions.models import (
    ApprovalCancelled,
    ApprovalResolution,
    ApprovalSubmission,
    ItemDecision,
)


class CliPermissionMode(StrEnum):
    FULL_AUTO = "full_auto"
    CONFIRM = "confirm"
    PLAN = "plan"

    @property
    def policy_mode(self) -> PermissionMode:
        if self is CliPermissionMode.CONFIRM:
            return PermissionMode.DEFAULT
        return PermissionMode(self.value)


_CHOICES = ("allow_once", "allow_always", "reject")


class CliConfirmationHandler:
    """Decide one structured approval item by item; fail closed on bad input."""

    def __init__(
        self,
        *,
        store: Any | None = None,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], Any] = typer.echo,
    ) -> None:
        self._store = store
        self._input = input_fn
        self._output = output_fn
        self._lock = asyncio.Lock()

    def bind_store(self, store: Any) -> None:
        if store is None:
            raise TypeError("store must not be None")
        if self._store is not None and self._store is not store:
            raise ValueError("CliConfirmationHandler is already bound to a store")
        self._store = store

    async def confirm(
        self,
        request: Any,
        missing_item_ids: Any,
        context: Any,
    ) -> ApprovalResolution:
        """Read 1/2/3 per missing item, then submit once for the whole card."""
        if self._store is None:
            raise RuntimeError("CliConfirmationHandler has no PermissionStore")
        wanted = set(missing_item_ids)
        items = [item for item in request.requirements if item.item_id in wanted]
        async with self._lock:
            await _emit_confirmation_event(
                context,
                event_type="permission.confirmation_requested",
                tool_name="household",
                payload={
                    "protocol_version": 2,
                    "approval_id": request.approval_id,
                    "request_id": request.request_id,
                    "intent_summary": request.intent_summary,
                    "item_ids": [item.item_id for item in items],
                    "subject_id": _subject_id(context),
                },
            )
            try:
                decided: list[ItemDecision] = []
                for item in items:
                    decided.append(
                        ItemDecision(
                            item_id=item.item_id,
                            choice=await self._read_choice(item),
                        )
                    )
                decisions = tuple(decided)
            except (EOFError, KeyboardInterrupt) as exc:
                await self._cancel(request, context, "input ended")
                raise ApprovalCancelled("local input ended") from exc
            except Exception as exc:
                await self._cancel(request, context, "input failed")
                raise ApprovalCancelled(f"local input failed: {exc}") from exc
            resolution = self._store.submit(
                request.approval_id,
                ApprovalSubmission(
                    submission_id=f"sub-cli-{uuid.uuid4().hex[:16]}",
                    request_revision=request.revision,
                    decisions=decisions,
                ),
                _subject_id(context),
            )
            await _emit_confirmation_event(
                context,
                event_type="permission.confirmation_completed",
                tool_name="household",
                payload={
                    "protocol_version": 2,
                    "approval_id": request.approval_id,
                    "request_id": resolution.request_id,
                    "request_status": resolution.request_status,
                    "subject_id": _subject_id(context),
                },
            )
            return resolution

    async def _read_choice(self, item: Any) -> str:
        self._output(
            "\n".join(
                (
                    "Approval required",
                    f"{item.action_label}: {item.display_name} ({item.location})",
                    "1=allow once, 2=allow always, 3=reject",
                )
            )
        )
        while True:
            answer = await _read_input(self._input, "Choice [1/2/3]: ")
            normalized = answer.strip()
            if normalized == "1":
                return "allow_once"
            if normalized == "2":
                return "allow_always"
            if normalized == "3":
                return "reject"
            self._output(f"Unknown choice {answer!r}; answer 1, 2 or 3.")

    async def _cancel(self, request: Any, context: Any, reason: str) -> None:
        try:
            self._store.cancel(request.request_id, reason)
        except Exception as exc:
            get_logger().warning(f"cli approval cancel failed: {type(exc).__name__}")
        await _emit_confirmation_event(
            context,
            event_type="permission.confirmation_completed",
            tool_name="household",
            payload={
                "protocol_version": 2,
                "approval_id": request.approval_id,
                "request_id": request.request_id,
                "outcome": "cancelled",
                "request_status": "cancelled",
                "subject_id": _subject_id(context),
            },
        )


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
            f"permission confirmation audit failed: {type(exc).__name__}"
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
