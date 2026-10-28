"""Implicit feedback material collection.

This module only prepares session-level context for later implicit feedback
analysis. It does not classify feedback signals or execute memory actions.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from itertools import groupby
from typing import Any

from ...components.activity import RecentActivityCollector
from ...components.feedback import (
    FeedbackRoundCompactor,
    ImplicitFeedbackActionPlanner,
    ImplicitFeedbackQueryRewriter,
    ImplicitFeedbackSignalDetector,
)
from ...infra.db import DatabaseClients, datetime_range, match_value, resolve_database_clients
from ...logging import get_logger
from ...typing import (
    ActivityMessage,
    ActivityScope,
    AddActivityEvent,
    FeedbackPipelineInput,
    FeedbackPipelineResult,
    FeedbackWindow,
    FieldCondition,
    ImplicitFeedbackRound,
    ImplicitFeedbackSessionMaterial,
    MemoryRequestContext,
    MemorySearchItem,
    MemoryView,
    SearchActivityEvent,
    SearchFilter,
    SearchPipelineInput,
)
from ..memory_db import MemoryDbReader, MemoryDbWriter
from ..registry import create_pipeline
from ..search import SearchPipeline
from .executor import FeedbackActionExecutor


class ImplicitFeedbackHandler:
    """Run implicit feedback collection and signal detection."""

    def __init__(
        self,
        *,
        collector: "ImplicitFeedbackRecordCollector | None" = None,
        signal_detector: ImplicitFeedbackSignalDetector | None = None,
        action_planner: ImplicitFeedbackActionPlanner | None = None,
        executor: FeedbackActionExecutor | None = None,
    ) -> None:
        self._collector = collector or ImplicitFeedbackRecordCollector()
        self._signal_detector = signal_detector or ImplicitFeedbackSignalDetector()
        self._action_planner = action_planner or ImplicitFeedbackActionPlanner()
        self._executor = executor or FeedbackActionExecutor()

    async def run(self, inp: FeedbackPipelineInput, context: MemoryRequestContext) -> FeedbackPipelineResult:
        """Collect session materials and detect negative feedback rounds."""

        t0 = time.monotonic()
        logger = get_logger(__name__)

        t_collect = time.monotonic()
        sessions = await self._collector.collect(context, window=inp.window)
        collect_elapsed = time.monotonic() - t_collect
        logger.info(
            "feedback.implicit.collect",
            sessions=len(sessions),
            elapsed=round(collect_elapsed, 2),
            user_id=context.user_id,
        )

        signal_count = 0
        actions = []
        for idx, material in enumerate(sessions):
            t_detect = time.monotonic()
            try:
                result = await self._signal_detector.detect(material)
            except Exception:
                logger.warning(
                    "feedback.implicit.detect failed; session skipped for retry",
                    session=f"{idx + 1}/{len(sessions)}",
                    session_id=material.session_id,
                    exc_info=True,
                )
                continue
            detect_elapsed = time.monotonic() - t_detect
            signal_count += len(result.signals)
            logger.info(
                "feedback.implicit.detect",
                session=f"{idx + 1}/{len(sessions)}",
                rounds=len(material.rounds),
                memories=len(material.memories),
                signals=len(result.signals),
                elapsed=round(detect_elapsed, 2),
            )

            valid_signals = [signal for signal in result.signals if 0 <= signal.round_index < len(material.rounds)]
            valid_signals.sort(key=lambda item: item.round_index)
            for round_index, grouped in groupby(valid_signals, key=lambda item: item.round_index):
                signals = list(grouped)
                if not signals:
                    continue
                round_ = material.rounds[round_index]
                t_plan = time.monotonic()
                try:
                    planned = await self._action_planner.plan(
                        round_=round_,
                        signals=signals,
                        memories=material.memories,
                    )
                except Exception:
                    logger.warning(
                        "feedback.implicit.plan failed; round skipped",
                        session_id=material.session_id,
                        round_index=round_index,
                        exc_info=True,
                    )
                    continue
                plan_elapsed = time.monotonic() - t_plan
                logger.info(
                    "feedback.implicit.plan",
                    round_index=round_index,
                    signals=len(signals),
                    actions=len(planned),
                    elapsed=round(plan_elapsed, 2),
                )
                actions.extend(await self._executor.execute(planned, context))
            await self._collector.mark_feedback_processed(context, material.source_add_record_ids)

        total_elapsed = time.monotonic() - t0
        logger.info(
            "feedback.implicit.done",
            signals=signal_count,
            actions=len(actions),
            sessions=len(sessions),
            elapsed=round(total_elapsed, 2),
        )
        return FeedbackPipelineResult(
            status="ok",
            message=f"processed {signal_count} implicit feedback signals in {len(sessions)} sessions",
            actions=actions,
        )


class ImplicitFeedbackRecordCollector:
    """Collect add/search records for implicit feedback by session."""

    def __init__(
        self,
        *,
        window_days: int = 3,
        page_size: int = 100,
        memory_reader: MemoryDbReader | None = None,
        memory_writer: MemoryDbWriter | None = None,
        activity_collector: RecentActivityCollector | None = None,
        clients: DatabaseClients | None = None,
        query_rewriter: "ImplicitFeedbackQueryRewriter | None" = None,
        search_pipeline: SearchPipeline | None = None,
        round_compactor: FeedbackRoundCompactor | None = None,
    ) -> None:
        self._window_days = window_days
        self._page_size = page_size
        inferred_clients = clients
        if inferred_clients is None and memory_reader is not None:
            inferred_clients = getattr(memory_reader, "clients", None)
        self._clients = resolve_database_clients(inferred_clients)
        self._memory_reader = memory_reader
        self._memory_writer = memory_writer
        self._activity_collector = activity_collector
        self._query_rewriter = query_rewriter or ImplicitFeedbackQueryRewriter()
        self._search = search_pipeline
        self._round_compactor = round_compactor or FeedbackRoundCompactor()

    async def collect(
        self,
        context: MemoryRequestContext,
        *,
        scope: str = "user",
        window: FeedbackWindow | None = None,
    ) -> list[ImplicitFeedbackSessionMaterial]:
        """Collect messages from add records and memories from add/search records.

        ``window`` (V3.8) narrows the implicit-mode collection: ``window_start``
        / ``window_end`` bound ``request_submitted_at`` and
        ``since_activity_id`` keeps only activity strictly newer than that
        add/search record's submission time.
        """

        window_start = window.window_start if window is not None else None
        window_end = window.window_end if window is not None else None
        add_must: list[Any] = []
        search_must: list[Any] = []
        if window is not None and window.since_activity_id:
            since_ts = await self._activity_submitted_at(context, window.since_activity_id)
            if since_ts is not None:
                bound = datetime_range("request_submitted_at", gt=since_ts)
                add_must.append(bound)
                search_must.append(bound)
            else:
                logger = get_logger(__name__)
                logger.info(
                    "feedback.implicit.window_since_unknown",
                    since_activity_id=window.since_activity_id,
                )

        activity = await self._collector.collect(
            _activity_scope_from_context(context, scope=scope),
            lookback=timedelta(days=self._window_days),
            window_start=window_start,
            window_end=window_end,
            max_records=self._page_size,
            add_must=add_must or None,
            add_must_not=[match_value("feedback_processed", True)],
            search_must=search_must or None,
        )

        sessions: dict[str, ImplicitFeedbackSessionMaterial] = {}
        for conversation in activity.conversations:
            if not conversation.session_id or not conversation.add_events:
                continue
            session_id = conversation.session_id
            material = sessions.setdefault(session_id, ImplicitFeedbackSessionMaterial(session_id=session_id))
            add_events = sorted(conversation.add_events, key=_add_event_time)
            round_message_groups = self._round_messages_from_add_events(add_events)
            for event in add_events:
                material.source_add_record_ids.append(event.add_record_id)
                material.memories = _merge_memories(
                    material.memories,
                    await self._add_event_memories(context, event),
                )
            for group in round_message_groups:
                round_messages = self._round_compactor.compact(group)
                if round_messages:
                    material.rounds.append(ImplicitFeedbackRound(messages=round_messages))
                    material.messages.extend(round_messages)

            for event in sorted(conversation.search_events, key=_search_event_time):
                material.memories = _merge_memories(material.memories, _search_event_memories(event))
                material.memories = _merge_memories(
                    material.memories,
                    await self._supplemental_search_memories(context, event),
                )

        return sorted(
            (material for material in sessions.values() if material.source_add_record_ids),
            key=lambda item: item.session_id,
        )

    async def _activity_submitted_at(self, context: MemoryRequestContext, activity_id: str) -> datetime | None:
        """Resolve ``request_submitted_at`` for an add/search record id (V3.8 window)."""

        payload: dict[str, Any] | None = None
        try:
            records = await self._clients.qdrant.get_add_records_by_ids(context.project_id, [activity_id])
            payload = dict(records[0].payload) if records else None
        except Exception:
            payload = None
        if payload is None:
            try:
                from qdrant_client import models as qmodels

                search_records, _ = await self._clients.qdrant.scroll_search_records(
                    context.project_id,
                    filter_=qmodels.Filter(must=[qmodels.HasIdCondition(has_id=[activity_id])]),
                    limit=1,
                )
                payload = dict(search_records[0].payload) if search_records else None
            except Exception:
                payload = None
        if payload is None:
            return None
        value = payload.get("request_submitted_at")
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    def _round_messages_from_add_events(self, events: list[AddActivityEvent]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        for event in events:
            for message in _activity_messages_to_dicts(event.messages):
                if message.get("role") == "user" and current:
                    groups.append(current)
                    current = []
                current.append(message)
        if current:
            groups.append(current)
        return groups

    async def mark_feedback_processed(self, context: MemoryRequestContext, add_record_ids: list[str]) -> None:
        """Mark add records as processed by implicit feedback."""

        for add_record_id in add_record_ids:
            await self._writer.patch_add_record(context, add_record_id, {"feedback_processed": True})

    async def _add_event_memories(
        self,
        context: MemoryRequestContext,
        event: AddActivityEvent,
    ) -> list[MemorySearchItem]:
        if not event.memory_payloads:
            return []

        hydrated = await self._hydrate_add_event_memories(context, event)
        if not hydrated:
            return []

        by_content = {memory.content: memory for memory in hydrated}
        result: list[MemorySearchItem] = []
        for raw in event.memory_payloads:
            content = raw.get("content")
            if not isinstance(content, str):
                continue
            memory = by_content.get(content)
            if memory is not None:
                result.append(_memory_view_to_search_item(memory))
        return result

    async def _hydrate_add_event_memories(
        self,
        context: MemoryRequestContext,
        event: AddActivityEvent,
    ) -> list[MemoryView]:
        request_id = event.context.request_id
        if not isinstance(request_id, str) or not request_id:
            return []

        must: list[FieldCondition] = [FieldCondition(field="request_id", op="match", value=request_id)]
        session_id = event.context.session_id
        if isinstance(session_id, str) and session_id:
            must.append(FieldCondition(field="session_id", op="match", value=session_id))
        filters = SearchFilter(must=must)
        memories, _ = await self._reader.list_memories(context, filters=filters, limit=100)
        return memories

    @property
    def _reader(self) -> MemoryDbReader:
        if self._memory_reader is None:
            self._memory_reader = MemoryDbReader()
        return self._memory_reader

    @property
    def _writer(self) -> MemoryDbWriter:
        if self._memory_writer is None:
            self._memory_writer = MemoryDbWriter(clients=self._clients)
        return self._memory_writer

    @property
    def _collector(self) -> RecentActivityCollector:
        if self._activity_collector is None:
            self._activity_collector = RecentActivityCollector(self._clients.qdrant)
        return self._activity_collector

    @property
    def _search_pipeline(self) -> SearchPipeline:
        if self._search is None:
            self._search = create_pipeline(type="search", name="search_pipeline")
        return self._search

    async def _supplemental_search_memories(
        self,
        context: MemoryRequestContext,
        event: SearchActivityEvent,
    ) -> list[MemorySearchItem]:
        original_query = event.query
        if not isinstance(original_query, str) or not original_query.strip():
            return []
        try:
            rewritten = await self._query_rewriter.rewrite(original_query)
        except Exception:
            get_logger(__name__).warning(
                "implicit feedback supplemental query rewrite failed; skipping",
                exc_info=True,
            )
            return []
        if not rewritten.query.strip():
            return []
        result = await self._search_pipeline.search(
            SearchPipelineInput(
                query=rewritten.query,
                filters=_supplemental_search_filters(context),
                search_pipeline="vanilla",
            ),
            context,
        )
        return result.memories


def _supplemental_search_filters(context: MemoryRequestContext) -> dict[str, str]:
    filters: dict[str, str] = {}
    if context.user_id:
        filters["user_id"] = context.user_id
    if context.session_id:
        filters["session_id"] = context.session_id
    if context.app_id:
        filters["app_id"] = context.app_id
    if context.agent_id:
        filters["agent_id"] = context.agent_id
    return filters


def _activity_scope_from_context(context: MemoryRequestContext, *, scope: str = "user") -> ActivityScope:
    data: dict[str, str | None] = {
        "project_id": context.project_id,
        "account_id": context.account_id,
        "api_key_uuid": context.api_key_uuid,
        "user_id": context.user_id,
        "app_id": context.app_id,
        "agent_id": context.agent_id,
    }
    if scope == "session":
        data["session_id"] = context.session_id
    return ActivityScope(**data)


def _activity_messages_to_dicts(messages: list[ActivityMessage]) -> list[dict[str, Any]]:
    return [message.model_dump(exclude_none=True) for message in messages]


def _add_event_time(event: AddActivityEvent) -> str:
    return event.occurred_at.isoformat() if event.occurred_at is not None else ""


def _memory_view_to_search_item(memory: MemoryView) -> MemorySearchItem:
    updated_at = memory.update_at or memory.created_at
    return MemorySearchItem(
        id=memory.memory_id,
        memory=memory.content,
        last_update_at=updated_at.strftime("%Y-%m-%d %H:%M:%S") if updated_at else "",
    )


def _search_event_time(event: SearchActivityEvent) -> str:
    return event.occurred_at.isoformat() if event.occurred_at is not None else ""


def _search_event_memories(event: SearchActivityEvent) -> list[MemorySearchItem]:
    memories: list[MemorySearchItem] = []
    for ref in event.recalled_memories:
        payload = dict(ref.payload)
        payload.setdefault("id", ref.memory_id)
        if ref.content is not None:
            payload.setdefault("memory", ref.content)
        memories.append(MemorySearchItem.model_validate(payload))
    return memories


def _merge_memories(existing: list[MemorySearchItem], supplemental: list[MemorySearchItem]) -> list[MemorySearchItem]:
    by_id = {memory.id: memory for memory in existing}
    for memory in supplemental:
        by_id.setdefault(memory.id, memory)
    return list(by_id.values())
