"""Server-durable pending ``ask_user`` questions for the Web Console.

A model tool call (``ask_user_question``) suspends inside the run while the
operator answers through a channel request.  The registry keeps the pending
question as server-side state so disconnecting and reconnecting clients can
re-list and answer it — mirroring how approvals survive WebSocket churn.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any


class QuestionCancelledError(Exception):
    """The pending question was torn down before an answer arrived."""


class PendingQuestion:
    """Handle for one in-flight question owned by a single run's tool call."""

    __slots__ = (
        "question_id",
        "session_id",
        "run_id",
        "request_id",
        "tool_call_id",
        "question",
        "_future",
        "_waiter_lost",
    )

    def __init__(
        self,
        *,
        question_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        tool_call_id: str,
        question: str,
        future: asyncio.Future[str],
    ) -> None:
        self.question_id = question_id
        self.session_id = session_id
        self.run_id = run_id
        self.request_id = request_id
        self.tool_call_id = tool_call_id
        self.question = question
        self._future = future
        self._waiter_lost = False

    @property
    def future(self) -> asyncio.Future[str]:
        return self._future

    @property
    def cancelled(self) -> bool:
        """True once the record is a tombstone awaiting terminal broadcast."""

        return self._waiter_lost or self._future.done()

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "request_id": self.request_id,
            "tool_call_id": self.tool_call_id,
            "question": self.question,
        }

    async def wait(self) -> str:
        """Await the operator's answer; mark the record when the run task dies."""

        try:
            return await self._future
        except asyncio.CancelledError:
            self._waiter_lost = True
            raise


class PendingQuestionRegistry:
    """Coroutine-safe registry keyed by question id, scoped to one web app."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[str, PendingQuestion] = {}
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    async def ask(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        question: str,
        tool_call_id: str,
    ) -> PendingQuestion:
        """Register one question and return its awaitable handle."""

        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        # Silently absorb terminal exceptions on abandoned waiters so an
        # interrupted run never logs "exception was never retrieved".
        future.add_done_callback(_absorb_terminal_exception)
        record = PendingQuestion(
            question_id=f"question-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            tool_call_id=tool_call_id,
            question=question,
            future=future,
        )
        async with self._lock:
            if self._closed:
                raise QuestionCancelledError("pending question registry is closed")
            self._pending[record.question_id] = record
        return record

    async def answer(
        self,
        *,
        session_id: str,
        question_id: str,
        text: str,
    ) -> PendingQuestion:
        """Resolve one live question; ``KeyError`` when unknown or dead."""

        if not isinstance(text, str):
            raise TypeError("text must be a string")
        async with self._lock:
            record = self._pending.get(question_id)
            if record is None or record.session_id != session_id or record.cancelled:
                raise KeyError(question_id)
            del self._pending[question_id]
        if not record.future.done():
            record.future.set_result(text)
        return record

    async def pending_for_session(self, session_id: str) -> list[PendingQuestion]:
        """List live questions for reconnect hydration, tombstones excluded."""

        async with self._lock:
            return [
                record
                for record in self._pending.values()
                if record.session_id == session_id and not record.cancelled
            ]

    async def cancel_request(
        self,
        session_id: str,
        request_id: str,
    ) -> list[PendingQuestion]:
        """Drop pending questions owned by one Web request and fail waiters.

        The scope is the immutable request id — never the whole session — so a
        later run on the same session cannot lose its freshly registered
        questions to a previous run's teardown.  Returns the popped records so
        the caller can broadcast the terminal ``question.cancelled`` events.
        """

        async with self._lock:
            records = [
                record
                for record in self._pending.values()
                if record.session_id == session_id and record.request_id == request_id
            ]
            for record in records:
                del self._pending[record.question_id]
        for record in records:
            _fail(record, QuestionCancelledError("run ended before the question was answered"))
        return records

    async def aclose(self) -> None:
        """Reject every pending question exactly once during app shutdown."""

        async with self._lock:
            if self._closed:
                return
            self._closed = True
            records = list(self._pending.values())
            self._pending.clear()
        for record in records:
            _fail(record, QuestionCancelledError("pending question registry closed"))


def _fail(record: PendingQuestion, error: BaseException) -> None:
    if record.future.done():
        return
    try:
        record.future.set_exception(error)
    except asyncio.InvalidStateError:
        pass


def _absorb_terminal_exception(future: asyncio.Future[str]) -> None:
    if future.cancelled():
        return
    try:
        future.exception()
    except asyncio.CancelledError:
        pass


__all__ = [
    "PendingQuestion",
    "PendingQuestionRegistry",
    "QuestionCancelledError",
]
