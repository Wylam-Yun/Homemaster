"""Contract tests for the daily implicit-feedback scheduler (V3.8 Task 6)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homemaster.memory.feedback_scheduler import (
    ImplicitFeedbackConfig,
    ImplicitFeedbackScheduler,
)
from homemaster.memory.memory_work_store import (
    JOB_IMPLICIT_FEEDBACK,
    SQLiteMemoryWorkStore,
)

_CST = ZoneInfo("Asia/Shanghai")


@pytest.fixture()
def store(tmp_path):
    store = SQLiteMemoryWorkStore(tmp_path / "work.db")
    store.open()
    yield store
    store.close()


def _scheduler(store, **overrides) -> ImplicitFeedbackScheduler:
    return ImplicitFeedbackScheduler(
        store, ImplicitFeedbackConfig(scope_key="scope-1", **overrides)
    )


def _window_start_epoch(job) -> float:
    return float(job.operation_key.rsplit(":", 1)[1])


class TestEnqueueManual:
    def test_creates_job_with_three_day_window_and_no_watermark(self, store):
        scheduler = _scheduler(store)
        now = datetime(2026, 10, 28, 12, 0, tzinfo=_CST)

        job = scheduler.enqueue_manual(now=now)

        assert job.kind == JOB_IMPLICIT_FEEDBACK
        assert job.status == "accepted"
        assert job.operation_key == (
            f"implicitcheck:scope-1:manual:{int(now.timestamp())}"
        )
        assert job.payload["scope_key"] == "scope-1"
        window = job.payload["window"]
        assert datetime.fromisoformat(window["window_start"]) == now - timedelta(days=3)
        assert datetime.fromisoformat(window["window_end"]) == now
        # Manual runs never write feedback_watermarks rows — the daily
        # catch-up anchor stays owned by scheduled windows only.
        assert store.recent_feedback_watermarks("scope-1", limit=10) == []

    def test_dedupes_same_second_but_allows_later_clicks(self, store):
        scheduler = _scheduler(store)
        now = datetime(2026, 10, 28, 12, 0, tzinfo=_CST)

        first = scheduler.enqueue_manual(now=now)
        assert scheduler.enqueue_manual(now=now).job_id == first.job_id

        later = scheduler.enqueue_manual(now=now + timedelta(seconds=1))
        assert later.job_id != first.job_id

    def test_ignores_disabled_schedule_flag(self, store):
        # ``enabled`` gates the daily schedule; a manual click is explicit
        # user intent and still executes through the shared worker pool.
        scheduler = _scheduler(store, enabled=False)
        job = scheduler.enqueue_manual(now=datetime(2026, 10, 28, tzinfo=_CST))
        assert job.kind == JOB_IMPLICIT_FEEDBACK


class TestScheduledTick:
    def test_tick_enqueues_one_job_per_due_window_and_dedupes(self, store):
        scheduler = _scheduler(store)
        # Noon Asia/Shanghai — the 03:00 window is due; fresh installs get the
        # bounded 7-day catch-up lookback.
        now = datetime(2026, 10, 28, 12, 0, tzinfo=_CST)

        created = scheduler.tick(now=now)
        assert len(created) == 7

        assert scheduler.tick(now=now) == []

        watermarks = store.recent_feedback_watermarks("scope-1", limit=10)
        assert len(watermarks) == 7
        resolved = {
            store.job_by_operation_key(
                f"implicitcheck:scope-1:{int(row['window_start'])}"
            ).job_id
            for row in watermarks
        }
        assert resolved == set(created)

    def test_watermark_reflects_job_terminal_status_after_tick(self, store):
        scheduler = _scheduler(store)
        now = datetime(2026, 10, 28, 12, 0, tzinfo=_CST)
        scheduler.tick(now=now)

        job = store.claim_jobs("w1", kinds=(JOB_IMPLICIT_FEEDBACK,), limit=1)[0]
        store.complete_job(job.job_id, owner="w1", result={})

        # Terminal status reaches the watermark lazily on the next tick.
        scheduler.tick(now=now + timedelta(minutes=1))
        watermark = store.feedback_watermark("scope-1", _window_start_epoch(job))
        assert watermark["status"] == "completed"
        assert watermark["job_id"] == job.job_id

    def test_manual_jobs_coexist_with_scheduled_ones(self, store):
        scheduler = _scheduler(store)
        now = datetime(2026, 10, 28, 12, 0, tzinfo=_CST)
        scheduled = scheduler.tick(now=now)

        manual = scheduler.enqueue_manual(now=now)
        assert manual.job_id not in scheduled
        assert manual.operation_key.endswith(f":manual:{int(now.timestamp())}")
        assert len(store.recent_feedback_watermarks("scope-1", limit=10)) == 7
