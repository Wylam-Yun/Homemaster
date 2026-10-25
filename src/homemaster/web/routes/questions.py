"""Pending ``ask_user_question`` routes: list for reconnect, submit answers."""

from __future__ import annotations

from fastapi import FastAPI

from homemaster.web._shared import _error, _session_ids
from homemaster.web.context import WebAppContext
from homemaster.web.schemas import AnswerQuestionRequest, WebEvent


def register(app: FastAPI, ctx: WebAppContext) -> None:
    application = ctx.application
    pending_questions = ctx.pending_questions
    hub = ctx.hub

    @app.get("/api/sessions/{session_id}/questions")
    async def list_pending_questions(session_id: str) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        records = await pending_questions.pending_for_session(session_id)
        return {"questions": [record.to_dict() for record in records]}

    @app.post("/api/sessions/{session_id}/questions/{question_id}/answer")
    async def answer_question(
        session_id: str,
        question_id: str,
        body: AnswerQuestionRequest,
    ) -> object:
        if session_id not in _session_ids(application.session_manager):
            return _error(
                404,
                "session_not_found",
                "The requested session does not exist.",
                retryable=False,
            )
        try:
            record = await pending_questions.answer(
                session_id=session_id,
                question_id=question_id,
                text=body.text,
            )
        except KeyError:
            return _error(
                404,
                "question_not_found",
                "The question is unknown or already resolved.",
                retryable=False,
            )
        await hub.publish(
            WebEvent(
                type="question.answered",
                session_id=session_id,
                run_id=record.run_id,
                request_id=record.request_id,
                payload={"question_id": record.question_id},
            )
        )
        return {"accepted": True}


__all__ = ["register"]
