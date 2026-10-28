"""Contract tests for the implicit-feedback run inspector (observability)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from homemaster.memory.feedback_runs import FeedbackRunInspector
from homemaster.memory.memory_work_store import (
    JOB_IMPLICIT_FEEDBACK,
    SQLiteMemoryWorkStore,
)

PROJECT = "proj-dev"


class _FakeQdrant:
    """Minimal scroll/retrieve stand-in keyed by (collection, id)."""

    def __init__(self, points: dict[str, dict[str, dict]]):
        # points: {collection: {point_id: payload}}
        self._points = points

    def scroll(self, collection_name, limit, offset=None, with_payload=True, with_vectors=False):
        items = list(self._points.get(collection_name, {}).items())
        start = int(offset or 0)
        batch = items[start : start + limit]
        next_offset = start + limit if start + limit < len(items) else None
        return (
            [SimpleNamespace(id=pid, payload=payload) for pid, payload in batch],
            next_offset,
        )

    def retrieve(self, collection_name, ids, with_payload=True):
        return [
            SimpleNamespace(id=pid, payload=self._points[collection_name][pid])
            for pid in ids
            if pid in self._points.get(collection_name, {})
        ]

    def close(self):
        return None


def _add_record(
    *, session, record_id, submitted_at, processed="True", status="ok",
    messages=None, project=PROJECT,
):
    return {
        "add_record_id": record_id,
        "session_id": session,
        "project_id": project,
        "request_submitted_at": submitted_at,
        "feedback_processed": processed,
        "status": status,
        "messages": str(messages or [{"role": "user", "content": "改成苏州"}]),
    }


def _inspector(points, **kwargs):
    return FeedbackRunInspector(client=_FakeQdrant(points), project_id=PROJECT, **kwargs)


WINDOW = {"window_start": "2026-10-25T00:00:00+00:00", "window_end": "2026-10-28T00:00:00+00:00"}


@pytest.mark.asyncio
class TestScannedSessions:
    async def test_groups_processed_records_by_session_with_preview(self):
        points = {
            "add_record_v1": {
                "r1": _add_record(
                    session="s-1", record_id="r1", submitted_at="2026-10-27T10:00:00+00:00",
                    messages=[{"role": "assistant", "content": "好的"},
                              {"role": "user", "content": "其实我搬苏州了"}],
                ),
                "r2": _add_record(
                    session="s-1", record_id="r2", submitted_at="2026-10-27T11:00:00+00:00",
                ),
                "r3": _add_record(
                    session="s-2", record_id="r3", submitted_at="2026-10-26T09:00:00+00:00",
                ),
            }
        }
        sessions = await _inspector(points).scanned_sessions(WINDOW)

        assert [s.session_id for s in sessions] == ["s-1", "s-2"]
        assert sessions[0].records_count == 2
        assert sessions[0].excerpts[0].preview == "其实我搬苏州了"
        assert sessions[1].records_count == 1

    async def test_excludes_outside_window_unprocessed_nonok_and_other_projects(self):
        points = {
            "add_record_v1": {
                "inside": _add_record(
                    session="s-1", record_id="inside", submitted_at="2026-10-27T10:00:00+00:00"),
                "early": _add_record(
                    session="s-1", record_id="early", submitted_at="2026-10-20T10:00:00+00:00"),
                "unprocessed": _add_record(
                    session="s-1", record_id="unprocessed",
                    submitted_at="2026-10-27T10:00:00+00:00", processed="False"),
                "failed": _add_record(
                    session="s-1", record_id="failed",
                    submitted_at="2026-10-27T10:00:00+00:00", status="error"),
                "other_project": _add_record(
                    session="s-1", record_id="other_project",
                    submitted_at="2026-10-27T10:00:00+00:00", project="proj-other"),
            }
        }
        sessions = await _inspector(points).scanned_sessions(WINDOW)

        assert len(sessions) == 1
        assert sessions[0].records_count == 1
        assert sessions[0].excerpts[0].add_record_id == "inside"

    async def test_empty_window_returns_empty(self):
        inspector = _inspector({"add_record_v1": {}})
        assert await inspector.scanned_sessions({}) == []
        assert await inspector.scanned_sessions(WINDOW) == []


def _memory(content, *, status="active", session="s-1", parents=None):
    payload = {"content": content, "status": status, "session_id": session}
    if parents is not None:
        payload["parent_ids"] = parents
    return payload


@pytest.mark.asyncio
class TestActionDiffs:
    async def test_add_shows_new_content_only(self):
        points = {"memory_item_v1": {"m-new": _memory("住苏州")}}
        diffs = await _inspector(points).action_diffs(
            [{"action_id": "a1", "action": "add", "memory_id": "m-new"}]
        )
        assert len(diffs) == 1
        assert diffs[0].before is None
        assert diffs[0].after.content == "住苏州"
        assert diffs[0].memory_session_id == "s-1"

    async def test_update_links_archived_parent_as_before(self):
        points = {
            "memory_item_v1": {
                "m-new": _memory("住苏州", parents=["m-old"]),
                "m-old": _memory("住杭州", status="archived"),
            }
        }
        diffs = await _inspector(points).action_diffs(
            [{"action_id": "a1", "action": "update", "memory_id": "m-new"}]
        )
        assert diffs[0].before.content == "住杭州"
        assert diffs[0].before.status == "archived"
        assert diffs[0].after.content == "住苏州"

    async def test_delete_shows_content_now_archived(self):
        points = {"memory_item_v1": {"m-del": _memory("过期信息", status="archived")}}
        diffs = await _inspector(points).action_diffs(
            [{"action_id": "a1", "action": "delete", "memory_id": "m-del"}]
        )
        assert diffs[0].before.status == "active"
        assert diffs[0].after.status == "archived"

    async def test_missing_point_is_flagged_not_fatal(self):
        diffs = await _inspector({"memory_item_v1": {}}).action_diffs(
            [{"action_id": "a1", "action": "delete", "memory_id": "m-gone"}]
        )
        assert diffs[0].missing is True
        assert diffs[0].before is None


@pytest.fixture()
def store(tmp_path):
    store = SQLiteMemoryWorkStore(tmp_path / "work.db")
    store.open()
    yield store
    store.close()


class TestListJobs:
    def test_filters_kind_orders_desc_limits(self, store):
        for index in range(3):
            store.enqueue_job(
                JOB_IMPLICIT_FEEDBACK,
                f"implicitcheck:scope:{index}",
                {"scope_key": "scope"},
            )
        store.enqueue_job("turn_add", "turnadd:s:1", {})

        jobs = store.list_jobs(JOB_IMPLICIT_FEEDBACK, limit=10)

        assert len(jobs) == 3
        assert [j.operation_key for j in jobs] == [
            "implicitcheck:scope:2",
            "implicitcheck:scope:1",
            "implicitcheck:scope:0",
        ]
        assert len(store.list_jobs(JOB_IMPLICIT_FEEDBACK, limit=2)) == 2
