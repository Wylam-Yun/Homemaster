"""Run submission route: ``POST /api/sessions/{id}/messages``.

The endpoint accepts one immutable browser request per session, hands it to
the application runtime as an owned task, and bridges ``ask_user_question``
suspensions onto the pending-question surface.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI

from homemaster.application import RunPolicy, RunRequest
from homemaster.web._shared import (
    _WEB_PERMISSION_SUBJECT,
    _error,
    _provider_selection_error,
    _session_ids,
)
from homemaster.web.context import WebAppContext
from homemaster.web.event_hub import WebEventHub
from homemaster.web.run_registry import SessionBusyError, WebRunRegistry
from homemaster.web.schemas import SendMessageRequest, WebEvent


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    run_registry = ctx.run_registry
    pending_questions = ctx.pending_questions
    hub = ctx.hub
    config = ctx.config

    @app.post("/api/sessions/{session_id}/messages", status_code=202)
    async def send_message(session_id: str, body: SendMessageRequest) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        if await run_registry.is_accepted(session_id, body.request_id):
            return {
                "accepted": True,
                "session_id": session_id,
                "request_id": body.request_id,
            }
        selection_error = _provider_selection_error(
            config,
            provider_name=body.provider_name,
            model=body.model,
        )
        if selection_error is not None:
            return selection_error
        if not await hub.has_subscriber(session_id):
            return _error(
                409,
                "event_stream_not_ready",
                "Subscribe to the session event stream before sending a message.",
                retryable=True,
            )

        async def ask_user_prompt(question: str) -> str:
            """Suspend ``ask_user_question`` until any client answers it.

            The service-tool contract invokes this callable synchronously and
            awaits the awaitable it returns, so registering the question and
            publishing ``question.asked`` happen before the run suspends.
            """

            # The hub pump binds run_id when it correlates turn_started; the
            # tool call may race ahead of it, so poll briefly before giving up.
            run_id = await run_registry.active_run_id(session_id)
            if run_id is None:
                for _ in range(20):
                    await asyncio.sleep(0.05)
                    run_id = await run_registry.active_run_id(session_id)
                    if run_id is not None:
                        break
            # The adapter sets ``_current_tool_call_id`` for the dispatching
            # tool's context, so the suspended prompt can be correlated back
            # to the exact tool call that asked.
            try:
                from homemaster.substrate.toolkit import current_tool_call_id

                tool_call_id = current_tool_call_id()
            except Exception:
                tool_call_id = ""
            record = await pending_questions.ask(
                session_id=session_id,
                run_id=run_id or "",
                request_id=body.request_id,
                question=question,
                tool_call_id=tool_call_id,
            )
            await hub.publish(
                WebEvent(
                    type="question.asked",
                    session_id=session_id,
                    run_id=record.run_id,
                    request_id=record.request_id,
                    payload={
                        "question_id": record.question_id,
                        "question": record.question,
                        "tool_call_id": record.tool_call_id,
                    },
                )
            )
            return await record.wait()

        start_gate = asyncio.Event()

        def _schedule_run_finished(done_task: asyncio.Task[object]) -> None:
            """Emit the in-order finish marker once the owned task is done.

            Terminal events correlate asynchronously on the hub pump, so the
            task's own ``finally`` cannot decide whether one was emitted. The
            marker lands on the bus strictly after every event the task
            produced; the pump consumes it only after a real terminal — if
            any — already correlated, otherwise it publishes an honest
            ``run.cancelled``/``run.failed`` and releases the session binding.
            """

            emitter = hub.note_task_finished(session_id, body.request_id, done_task)
            try:
                asyncio.get_running_loop().create_task(emitter)
            except RuntimeError:
                emitter.close()

        async def run_owned() -> object:
            owned_task = asyncio.current_task()
            if owned_task is not None:
                owned_task.add_done_callback(_schedule_run_finished)
            await start_gate.wait()
            try:
                return await _run_and_report_prestart_failure(
                    application=application,
                    request=RunRequest(
                        text=body.text,
                        session_id=session_id,
                        resume=True,
                        provider_name=body.provider_name,
                        model_override=body.model,
                        permission_subject=_WEB_PERMISSION_SUBJECT,
                        run_policy=RunPolicy(
                            max_tool_iterations=(
                                config.runtime.max_tool_iterations if config is not None else None
                            ),
                        ),
                        metadata={"web_request_id": body.request_id},
                        dependencies={"ask_user_prompt": ask_user_prompt},
                    ),
                    request_id=body.request_id,
                    run_registry=run_registry,
                    hub=hub,
                )
            finally:
                # A run's questions are only live while the run is; end or
                # cancellation drops them and broadcasts one terminal event each.
                for record in await pending_questions.cancel_request(session_id, body.request_id):
                    await hub.publish(
                        WebEvent(
                            type="question.cancelled",
                            session_id=session_id,
                            run_id=record.run_id,
                            request_id=record.request_id,
                            payload={"question_id": record.question_id},
                        )
                    )

        try:
            acceptance = await run_registry.accept(
                session_id,
                body.request_id,
                run_owned,
            )
        except SessionBusyError:
            return _error(
                409,
                "session_busy",
                "This session already has an active run.",
                retryable=True,
            )
        if acceptance.created:
            await hub.publish(
                WebEvent(
                    type="request.accepted",
                    session_id=session_id,
                    run_id="",
                    request_id=body.request_id,
                    payload={},
                )
            )
            start_gate.set()
        return {
            "accepted": True,
            "session_id": session_id,
            "request_id": body.request_id,
        }


async def _run_and_report_prestart_failure(
    *,
    application: Any,
    request: RunRequest,
    request_id: str,
    run_registry: WebRunRegistry,
    hub: WebEventHub,
) -> object | None:
    """Turn adapter failures before ``turn_started`` into one safe terminal event."""

    try:
        return await application.run(request)
    except Exception:
        session_id = request.session_id or ""
        if await run_registry.fail_before_start(session_id, request_id):
            await hub.publish(
                WebEvent(
                    type="run.failed",
                    session_id=session_id,
                    run_id="",
                    request_id=request_id,
                    payload={
                        "code": "adapter_failed",
                        "message": "The run could not be started.",
                        "retryable": True,
                    },
                )
            )
        return None


__all__ = ["register"]
