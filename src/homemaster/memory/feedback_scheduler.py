"""Daily implicit-feedback scheduler (V3.8 §4.4).

Replaces the finalizer-driven implicit feedback: instead of running after
every session, implicit feedback runs once per daily window (default
``03:00 Asia/Shanghai`` — ADR provisional) per tenant scope.

Durability contract:

- each due window enqueues exactly ONE ``implicit_feedback`` outbox job
  keyed ``implicitcheck:<scope>:<window_start>`` — restarting cannot
  double-submit a window;
- per-window watermarks (``feedback_watermarks`` table) record the job id
  and status; a window whose job is not terminal is re-attached, not
  re-created;
- on startup the scheduler catches up every missed window since the last
  completed watermark (bounded lookback), so downtime doesn't silently
  drop daily feedback;
- the scheduler only ADMITS work — the outbox worker executes it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from homemaster.memory.memory_work_store import (
    JOB_IMPLICIT_FEEDBACK,
    MemoryWorkStore,
    OutboxJob,
)

logger = logging.getLogger(__name__)

# Catch-up lookback guard: never schedule windows older than this even if
# the watermark trail is missing entirely (fresh installs).
_CATCH_UP_LOOKBACK_DAYS = 7

# Manual (user-triggered) runs analyze the same lookback the upstream
# collector uses by default. Server-side ``feedback_processed`` marks make
# a wide window safe: already-consumed records are skipped, not re-analyzed.
_MANUAL_LOOKBACK_DAYS = 3


@dataclass(frozen=True)
class ImplicitFeedbackConfig:
    enabled: bool = True
    hour: int = 3
    minute: int = 0
    timezone_name: str = "Asia/Shanghai"
    scope_key: str = "local"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


class ImplicitFeedbackScheduler:
    """Watermark-driven daily-window scheduler.

    Call :meth:`tick` from the application's periodic loop (or
    :meth:`catch_up` once at startup). All state lives in the work store.
    """

    def __init__(self, store: MemoryWorkStore, config: ImplicitFeedbackConfig) -> None:
        self._store = store
        self._config = config

    def _window_start_for(self, instant: datetime) -> datetime:
        """The most recent scheduled instant at-or-before ``instant``."""
        local = instant.astimezone(self._config.tz)
        candidate = datetime.combine(
            local.date(), dt_time(self._config.hour, self._config.minute), tzinfo=self._config.tz
        )
        if candidate > local:
            candidate -= timedelta(days=1)
        return candidate

    def due_windows(self, *, now: datetime | None = None) -> list[datetime]:
        """All scheduled instants after the last completed window up to now."""
        if not self._config.enabled:
            return []
        now = now or datetime.now(tz=UTC)
        latest_due = self._window_start_for(now)
        anchor = self._last_completed_window()
        windows: list[datetime] = []
        if anchor is not None:
            cursor = anchor + timedelta(days=1)
        else:
            cursor = latest_due - timedelta(days=_CATCH_UP_LOOKBACK_DAYS - 1)
        while cursor <= latest_due:
            windows.append(cursor)
            cursor += timedelta(days=1)
        return windows

    def _last_completed_window(self) -> datetime | None:
        """Newest window whose watermark reached a terminal state.

        Watermark rows are keyed (scope, window_start); scan descending for
        the first non-failed terminal marker — a ``failed`` window still
        counts as consumed (the outbox already exhausted retries).
        """
        rows = self._recent_watermarks(limit=64)
        for row in rows:
            if row["status"] in {"completed", "no_action", "failed"}:
                return datetime.fromtimestamp(row["window_start"], tz=UTC).astimezone(
                    self._config.tz
                )
        return None

    def _recent_watermarks(self, *, limit: int) -> list[dict[str, Any]]:
        return self._store.recent_feedback_watermarks(self._config.scope_key, limit=limit)

    def catch_up(self, *, now: datetime | None = None) -> list[str]:
        """Enqueue jobs for every due window; returns created job ids."""
        return self.tick(now=now)

    def tick(self, *, now: datetime | None = None) -> list[str]:
        created: list[str] = []
        for window in self.due_windows(now=now):
            window_start = window.timestamp()
            op_key = f"implicitcheck:{self._config.scope_key}:{int(window_start)}"
            existing = self._store.job_by_operation_key(op_key)
            if existing is None:
                job = self._store.enqueue_job(
                    JOB_IMPLICIT_FEEDBACK,
                    op_key,
                    {
                        "scope_key": self._config.scope_key,
                        "window": {
                            "window_start": window.isoformat(),
                            "window_end": (window + timedelta(days=1)).isoformat(),
                        },
                    },
                )
                self._store.put_feedback_watermark(
                    self._config.scope_key, window_start, job_id=job.job_id, status="accepted"
                )
                created.append(job.job_id)
            else:
                # Re-attach the watermark; terminal state lives on the job.
                self._store.put_feedback_watermark(
                    self._config.scope_key,
                    window_start,
                    job_id=existing.job_id,
                    status=existing.status,
                )
        return created

    def enqueue_manual(self, *, now: datetime | None = None) -> OutboxJob:
        """Enqueue one user-triggered implicit-feedback run; returns the job.

        Uses a ``manual:`` operation key so it never collides with the daily
        ``implicitcheck:{scope}:{window_start}`` jobs, and writes no
        watermark — the catch-up anchor stays owned by scheduled windows.
        ``config.enabled`` gates the *schedule* only; a manual click is
        explicit user intent and still goes through the shared executor and
        raw-readback gate. Same-second re-clicks dedupe on the operation key.
        """
        now = now or datetime.now(tz=UTC)
        window_start = now - timedelta(days=_MANUAL_LOOKBACK_DAYS)
        op_key = f"implicitcheck:{self._config.scope_key}:manual:{int(now.timestamp())}"
        return self._store.enqueue_job(
            JOB_IMPLICIT_FEEDBACK,
            op_key,
            {
                "scope_key": self._config.scope_key,
                "window": {
                    "window_start": window_start.isoformat(),
                    "window_end": now.isoformat(),
                },
            },
        )

    def mark_window_result(self, window_start: float, *, status: str) -> None:
        row = self._store.feedback_watermark(self._config.scope_key, window_start)
        if row is not None:
            self._store.put_feedback_watermark(
                self._config.scope_key, window_start, job_id=row["job_id"], status=status
            )


__all__ = ["ImplicitFeedbackConfig", "ImplicitFeedbackScheduler"]
