# -*- coding: utf-8 -*-
"""Async counterpart of :class:`MemoryClient` for the ``/v1/memory/*`` API."""

from __future__ import annotations

from typing import Any

from ..transport import AsyncHttpTransport
from .core import MemoryCore, MemoryDefaults
from .models import (
    AddMode,
    AddResult,
    FeedbackMode,
    FeedbackWindow,
    GetResult,
    MemoryPageResult,
    MemoryScrollResult,
    MemorySearchHit,
    Message,
    OperationStatus,
    SearchResult,
    SearchStrategy,
    StatusResult,
)


class AsyncMemoryClient:
    """Async memory API resource client."""

    def __init__(
        self,
        transport: AsyncHttpTransport,
        *,
        default_user_id: str | None = None,
        default_app_id: str | None = None,
        default_agent_id: str | None = None,
        default_session_id: str | None = None,
    ) -> None:
        self._transport = transport
        self._core = MemoryCore(
            MemoryDefaults(
                user_id=default_user_id,
                app_id=default_app_id,
                agent_id=default_agent_id,
                session_id=default_session_id,
            )
        )

    async def add(
        self,
        messages: list[Message | dict[str, Any]],
        *,
        user_id: str | None = None,
        mode: AddMode = "sync",
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        skill_context: list[Any] | None = None,
        score: float | None = None,
        task_id: str | None = None,
        operation_id: str | None = None,
    ) -> AddResult:
        """Add content to the memory store.

        ``operation_id`` (V3.8) makes the call idempotent: resubmitting the same
        id with the same payload replays the stored server result.
        """
        request = self._core.add(
            messages=messages,
            user_id=user_id,
            mode=mode,
            app_id=app_id,
            agent_id=agent_id,
            session_id=session_id,
            metadata=metadata,
            skill_context=skill_context,
            score=score,
            task_id=task_id,
            operation_id=operation_id,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = 10,
        user_id: str | None = None,
        search_strategy: SearchStrategy = "fast",
        rerank: bool = False,
        score_threshold: float | None = None,
        token_budget: int | None = None,
        filters: dict[str, Any] | None = None,
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> SearchResult:
        """Search memories."""
        request = self._core.search(
            query,
            top_k=top_k,
            user_id=user_id,
            search_strategy=search_strategy,
            rerank=rerank,
            score_threshold=score_threshold,
            token_budget=token_budget,
            filters=filters,
            app_id=app_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def get(
        self,
        *,
        filters: dict[str, Any] | None = None,
        top_k: int | None = None,
    ) -> GetResult:
        """List or filter memories in the current project."""
        request = self._core.get(filters=filters, top_k=top_k)
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
        include_total: bool = True,
        user_id: str | None = None,
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> MemoryPageResult:
        """List memories using page/page_size pagination."""
        request = self._core.list(
            filters=filters,
            page=page,
            page_size=page_size,
            include_total=include_total,
            user_id=user_id,
            app_id=app_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def scroll(
        self,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        cursor: str | None = None,
        user_id: str | None = None,
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> MemoryScrollResult:
        """Scroll memories using an opaque cursor."""
        request = self._core.scroll(
            filters=filters,
            limit=limit,
            cursor=cursor,
            user_id=user_id,
            app_id=app_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        fields: dict[str, Any] | None = None,
        *,
        operation_id: str | None = None,
    ) -> StatusResult:
        """Update one memory by id.

        ``content`` performs the plain-text replacement; ``fields`` (V3.8)
        applies structured typed-field + metadata updates through the native
        version/graph update path.
        """
        request = self._core.update(
            memory_id,
            content=content,
            fields=fields,
            operation_id=operation_id,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def delete(
        self,
        memory_id: str,
        *,
        operation_id: str | None = None,
    ) -> StatusResult:
        """Delete one memory by id."""
        request = self._core.delete(memory_id, operation_id=operation_id)
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def dreaming(
        self,
        *,
        mode: AddMode = "async",
        user_id: str | None = None,
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        operation_id: str | None = None,
        seed_add_record_ids: list[str] | None = None,
    ) -> StatusResult:
        """Trigger the dreaming pipeline.

        ``seed_add_record_ids`` (V3.8) scopes consolidation to exactly those
        add records; an empty list is a successful no-op.
        """
        request = self._core.dreaming(
            mode=mode,
            user_id=user_id,
            app_id=app_id,
            agent_id=agent_id,
            session_id=session_id,
            operation_id=operation_id,
            seed_add_record_ids=seed_add_record_ids,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)

    async def operation_status(self, operation_id: str) -> OperationStatus:
        """Fetch the durable ledger row for ``operation_id`` (V3.8)."""
        request = self._core.operation_status(operation_id)
        envelope = await self._transport.get_envelope(request.path)
        return request.parse(envelope)

    async def feedback(
        self,
        *,
        feedback: str | None = None,
        mode: FeedbackMode | None = None,
        messages: list[Message | dict[str, Any]] | None = None,
        recalled_memories: list[MemorySearchHit | dict[str, Any]] | None = None,
        user_id: str | None = None,
        app_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
        operation_id: str | None = None,
        window: FeedbackWindow | dict[str, Any] | None = None,
    ) -> StatusResult:
        """Trigger the feedback workflow.

        Explicit feedback text requires messages context. Omit ``feedback`` to
        run implicit feedback from recent add records. ``window`` (V3.8)
        narrows implicit-mode collection.
        """
        request = self._core.feedback(
            feedback=feedback,
            mode=mode,
            messages=messages,
            recalled_memories=recalled_memories,
            user_id=user_id,
            app_id=app_id,
            agent_id=agent_id,
            session_id=session_id,
            operation_id=operation_id,
            window=window,
        )
        envelope = await self._transport.post_envelope(request.path, json=request.body)
        return request.parse(envelope)
