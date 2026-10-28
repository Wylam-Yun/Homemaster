"""Read-only reconstruction of implicit-feedback runs (observability).

The MindMemOS wire for a feedback run carries only
``{action_id, action, memory_id}`` per action — which sessions were scanned
and what each mutation looked like before/after never leaves the server.
This inspector rebuilds that view from the raw stores HomeMaster already
reads for verification:

- scanned sessions: ``add_record_v1`` records inside the job window that
  were marked ``feedback_processed``, grouped by ``session_id`` with the
  consumed conversation's last user message as evidence preview;
- action diffs: ``memory_item_v1`` payloads plus ``parent_ids`` lineage so
  an ``update`` shows archived-before → active-after, ``delete`` shows
  active → archived, ``add`` shows new content only.

Attribution is approximate by design: ``feedback_processed`` carries no run
id, so "scanned in this window" is the honest claim — the web surface labels
it exactly that. The inspector is read-only by construction (no mutating
method exists on it).
"""

from __future__ import annotations

import ast
import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_PREVIEW_LIMIT = 200
_MAX_RECORDS = 5000
_PAGE_SIZE = 200


@dataclass(frozen=True)
class ScannedExcerpt:
    add_record_id: str
    submitted_at: str | None
    preview: str


@dataclass(frozen=True)
class ScannedSession:
    session_id: str
    records_count: int
    excerpts: tuple[ScannedExcerpt, ...]


@dataclass(frozen=True)
class MemoryVersion:
    content: str
    status: str


@dataclass(frozen=True)
class ActionDiff:
    action_id: str
    action: str
    memory_id: str | None
    memory_session_id: str | None
    before: MemoryVersion | None
    after: MemoryVersion | None
    missing: bool


class NullFeedbackRunInspector:
    """Files-only/absent-backend placeholder: reports nothing, claims nothing."""

    async def scanned_sessions(self, window: dict[str, Any]) -> list[ScannedSession]:
        return []

    async def action_diffs(self, actions: Iterable[dict[str, Any]]) -> list[ActionDiff]:
        return []

    async def close(self) -> None:
        return None


class FeedbackRunInspector:
    """Read-only view over the raw stores backing a MindMemOS deployment."""

    def __init__(
        self,
        *,
        qdrant_url: str | None = None,
        qdrant_api_key: str | None = None,
        add_record_collection: str = "add_record_v1",
        memory_collection: str = "memory_item_v1",
        project_id: str | None = None,
        page_size: int = _PAGE_SIZE,
        max_records: int = _MAX_RECORDS,
        client: Any = None,
    ) -> None:
        if client is None:
            if qdrant_url is None:
                raise ValueError("qdrant_url or client required")
            from qdrant_client import QdrantClient

            client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key or None)
        self._add_collection = add_record_collection
        self._memory_collection = memory_collection
        self._project_id = project_id
        self._page_size = page_size
        self._max_records = max_records
        self._qdrant = client

    # -- scanned sessions -----------------------------------------------------

    async def scanned_sessions(self, window: dict[str, Any]) -> list[ScannedSession]:
        start = _parse_ts(window.get("window_start"))
        end = _parse_ts(window.get("window_end"))
        if start is None or end is None:
            return []
        records = await asyncio.to_thread(self._scroll_add_records)
        grouped: dict[str, list[ScannedExcerpt]] = {}
        for payload in records:
            submitted = _parse_ts(payload.get("request_submitted_at"))
            if submitted is None or not (start <= submitted <= end):
                continue
            if not _truthy(payload.get("feedback_processed")):
                continue
            if str(payload.get("status") or "") != "ok":
                continue
            session_id = payload.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            grouped.setdefault(session_id, []).append(
                ScannedExcerpt(
                    add_record_id=str(payload.get("add_record_id") or ""),
                    submitted_at=(
                        str(payload.get("request_submitted_at"))
                        if payload.get("request_submitted_at") is not None
                        else None
                    ),
                    preview=_record_preview(payload),
                )
            )
        sessions = [
            ScannedSession(
                session_id=session_id,
                records_count=len(excerpts),
                excerpts=tuple(
                    sorted(excerpts, key=lambda item: item.submitted_at or "")
                ),
            )
            for session_id, excerpts in grouped.items()
        ]
        return sorted(sessions, key=lambda item: item.session_id)

    def _scroll_add_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        offset: Any = None
        while len(records) < self._max_records:
            batch, offset = self._qdrant.scroll(
                collection_name=self._add_collection,
                limit=self._page_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in batch:
                payload = dict(point.payload or {})
                if self._project_id and str(payload.get("project_id") or "") != self._project_id:
                    continue
                records.append(payload)
            if offset is None:
                break
        return records

    # -- action diffs ---------------------------------------------------------

    async def action_diffs(self, actions: Iterable[dict[str, Any]]) -> list[ActionDiff]:
        diffs: list[ActionDiff] = []
        for index, action in enumerate(actions or []):
            if not isinstance(action, dict):
                continue
            kind = str(action.get("action") or "noop")
            memory_id = action.get("memory_id")
            action_id = str(action.get("action_id") or index)
            if not isinstance(memory_id, str) or not memory_id:
                diffs.append(
                    ActionDiff(
                        action_id=action_id,
                        action=kind,
                        memory_id=None,
                        memory_session_id=None,
                        before=None,
                        after=None,
                        missing=True,
                    )
                )
                continue
            point = await asyncio.to_thread(
                self._retrieve_payload, self._memory_collection, memory_id
            )
            if point is None:
                diffs.append(
                    ActionDiff(
                        action_id=action_id,
                        action=kind,
                        memory_id=memory_id,
                        memory_session_id=None,
                        before=None,
                        after=None,
                        missing=True,
                    )
                )
                continue
            session_id = point.get("session_id")
            content = str(point.get("content") or point.get("memory") or "")
            status = str(point.get("status") or "")
            before: MemoryVersion | None = None
            if kind == "update":
                parents = point.get("parent_ids")
                parent_id = parents[0] if isinstance(parents, list) and parents else None
                if isinstance(parent_id, str) and parent_id:
                    parent = await asyncio.to_thread(
                        self._retrieve_payload, self._memory_collection, parent_id
                    )
                    if parent is not None:
                        before = MemoryVersion(
                            content=str(parent.get("content") or parent.get("memory") or ""),
                            status=str(parent.get("status") or ""),
                        )
            elif kind == "delete":
                before = MemoryVersion(content=content, status="active")
            diffs.append(
                ActionDiff(
                    action_id=action_id,
                    action=kind,
                    memory_id=memory_id,
                    memory_session_id=session_id if isinstance(session_id, str) else None,
                    before=before,
                    after=MemoryVersion(content=content, status=status),
                    missing=False,
                )
            )
        return diffs

    def _retrieve_payload(self, collection: str, point_id: str) -> dict[str, Any] | None:
        try:
            points = self._qdrant.retrieve(
                collection_name=collection, ids=[point_id], with_payload=True
            )
        except Exception:
            logger.warning("feedback run inspect retrieve failed", exc_info=True)
            return None
        if not points:
            return None
        return dict(points[0].payload or {})

    async def close(self) -> None:
        if self._qdrant is not None:
            await asyncio.to_thread(self._qdrant.close)
            self._qdrant = None


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes"}


def _record_preview(payload: dict[str, Any]) -> str:
    messages = payload.get("messages")
    if isinstance(messages, str):
        try:
            messages = ast.literal_eval(messages)
        except (ValueError, SyntaxError):
            messages = []
    if not isinstance(messages, list):
        return ""
    last_user = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            last_user = str(message.get("content") or "")
    preview = last_user or ""
    return preview[:_PREVIEW_LIMIT]


__all__ = [
    "ActionDiff",
    "FeedbackRunInspector",
    "MemoryVersion",
    "ScannedExcerpt",
    "ScannedSession",
    "NullFeedbackRunInspector",
]
