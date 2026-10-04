"""Automatic memory recall path for application runs.

Owns the mindmemos automatic-recall path: consume_recall gating, query
construction, deadline-bounded search, recall event emission, and user
statement evidence registration. Reads ``settings.application_services``
lazily on every call — the settings object is mutable by contract.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from typing import Any

from homemaster.application.session import SessionGenerationError
from homemaster.events.runtime_events import RuntimeEvent
from homemaster.memory.automatic_recall import (
    build_automatic_recall_context,
    build_automatic_recall_query,
    build_mindmemos_request_context,
)


class AutomaticRecallRunDeadlineExceeded(TimeoutError):
    """The shared run deadline expired while automatic recall was pending."""

    # Must surface raw to the caller — see SessionGenerationError.
    _hm_propagate = True


class AutomaticRecallService:
    """MindMemOS automatic recall + user memory evidence registration."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    async def recall(
        self,
        *,
        request: Any,
        runtime: Any,
        generation: int,
        run_id: str,
        task_state_store: Any,
        event_sink: Any,
        deadline: Any,
    ) -> tuple[bool, str | None, tuple[Any, ...]]:
        if not runtime.consume_recall(generation):
            return False, None, ()

        services = getattr(self._settings, "application_services", {})
        service = services.get("mindmemos") if isinstance(services, Mapping) else None
        search = getattr(service, "search", None)
        if not callable(search):
            await _emit_automatic_recall_event(
                event_sink,
                session_id=runtime.session.session_id,
                run_id=run_id,
                status="unavailable",
                count=0,
            )
            return True, None, ()

        query = build_automatic_recall_query(
            current_user_message=request.text,
            messages=runtime.session.messages,
            task_state_store=task_state_store,
        )
        context = build_mindmemos_request_context(
            request_id=f"automatic-recall:{run_id}",
            tenant_id=request.permission_subject.tenant_id,
            session_id=runtime.session.session_id,
        )
        try:
            result = await _await_with_remaining_deadline(
                search(
                    query,
                    context,
                    top_k=5,
                    search_pipeline="vanilla",
                    rerank=False,
                    filters=None,
                ),
                deadline,
            )
        except AutomaticRecallRunDeadlineExceeded:
            raise
        except (asyncio.CancelledError, SessionGenerationError):
            raise
        except Exception as exc:
            await _emit_automatic_recall_event(
                event_sink,
                session_id=runtime.session.session_id,
                run_id=run_id,
                status="error",
                count=0,
                error=str(exc),
            )
            return True, None, ()

        memories = list(getattr(result, "memories", ()))[:5]
        await _emit_automatic_recall_event(
            event_sink,
            session_id=runtime.session.session_id,
            run_id=run_id,
            status="ok" if memories else "empty",
            count=len(memories),
        )
        return True, build_automatic_recall_context(memories), tuple(memories)

    def register_user_evidence(
        self,
        *,
        request: Any,
        session_id: str,
        run_id: str,
        turn_index: int,
    ) -> tuple[str, ...]:
        services = getattr(self._settings, "application_services", {})
        ledger = services.get("memory_evidence_ledger") if isinstance(services, Mapping) else None
        register = getattr(ledger, "register", None)
        if not callable(register):
            return ()
        evidence = register(
            kind="user_statement",
            tenant_id=request.permission_subject.tenant_id,
            session_id=session_id,
            run_id=run_id,
            turn_id=f"turn-{turn_index}",
        )
        return (evidence.ref,)


async def _await_with_remaining_deadline(awaitable: Any, deadline: Any) -> Any:
    remaining = deadline.remaining_s() if deadline is not None else None
    if remaining is None:
        return await awaitable
    if remaining <= 0:
        if inspect.iscoroutine(awaitable):
            awaitable.close()
        raise AutomaticRecallRunDeadlineExceeded("automatic recall exceeded the run deadline")
    timeout = asyncio.timeout(remaining)
    try:
        async with timeout:
            return await awaitable
    except TimeoutError as exc:
        if not timeout.expired():
            raise
        raise AutomaticRecallRunDeadlineExceeded(
            "automatic recall exceeded the run deadline"
        ) from exc


async def _emit_automatic_recall_event(
    event_sink: Any,
    *,
    session_id: str,
    run_id: str,
    status: str,
    count: int,
    error: str | None = None,
) -> None:
    if event_sink is None:
        return
    event = RuntimeEvent(
        type="memory.automatic_recall",
        session_id=session_id,
        run_id=run_id,
        turn_index=None,
        payload={"status": status, "count": count, "error": error},
    )
    aemit = getattr(event_sink, "aemit", None)
    if callable(aemit):
        await aemit(event)
        return
    emit = getattr(event_sink, "emit", None)
    if callable(emit):
        result = emit(event)
        if inspect.isawaitable(result):
            await result
