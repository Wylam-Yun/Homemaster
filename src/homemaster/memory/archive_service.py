"""Session Archive Snapshot service (V3.8 §4.5).

Replaces the V3.6 SessionFinalizer cascade. The ONLY job of this service is
to freeze an immutable boundary over the canonical trace and enqueue the
Dreaming intent bound to the Add seeds inside that boundary — in ONE store
transaction (``MemoryWorkStore.create_archive``).

Trigger matrix (spec):

- user ``/archive`` request, formal session close, ``/new`` rotation, and
  graceful process exit → :meth:`archive_session`
- a Turn finishing alone → NOT an archive trigger
- process crash → NOT an archive trigger (recovery may finalize the turn but
  never mints a snapshot)
- repeated archive requests → idempotent on the same boundary; a boundary
  that did not move produces the same ``archive_id`` and no new job
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from homemaster.memory.memory_work_store import (
    ArchiveSnapshot,
    MemoryWorkStore,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveResult:
    archive_id: str | None
    created: bool
    max_event_seq: int
    add_operation_ids: tuple[str, ...]
    status: str  # "archived" | "no_change" | "no_trace"


class ArchiveService:
    def __init__(self, store: MemoryWorkStore) -> None:
        self._store = store

    def archive_session(self, session_id: str) -> ArchiveResult:
        """Freeze the current terminal boundary of ``session_id``.

        Idempotent: calling twice with no new terminal events returns the
        same snapshot (``created=False``). Sessions with no trace rows
        return ``no_trace`` without creating anything.
        """
        max_seq = self._store.max_event_seq(session_id)
        if max_seq < 0:
            return ArchiveResult(
                archive_id=None,
                created=False,
                max_event_seq=-1,
                add_operation_ids=(),
                status="no_trace",
            )
        turns = self._store.turns_for_session(session_id)
        terminal_turns = [t for t in turns if t.status != "active"]
        if not terminal_turns:
            return ArchiveResult(
                archive_id=None,
                created=False,
                max_event_seq=max_seq,
                add_operation_ids=(),
                status="no_trace",
            )
        # Seeds are exactly the Add operations of terminal turns inside this
        # snapshot's turn range — never another session's, never an older
        # archive's.
        last_archived_turn = self._store.latest_archived_turn_index(session_id)
        new_turns = [t for t in terminal_turns if t.turn_index > last_archived_turn]
        if not new_turns:
            return ArchiveResult(
                archive_id=self._store.archives_for_session(session_id)[-1].archive_id
                if self._store.archives_for_session(session_id)
                else None,
                created=False,
                max_event_seq=max_seq,
                add_operation_ids=(),
                status="no_change",
            )
        add_ops = [t.operation_id for t in new_turns]
        snapshot = self._store.create_archive(
            session_id,
            turn_range_start=last_archived_turn + 1,
            turn_range_end=max(t.turn_index for t in new_turns),
            max_event_seq=max_seq,
            add_operation_ids=add_ops,
            intent_payload={
                "source": "homemaster.archive",
                "session_id": session_id,
                "archive_id": f"archive:{session_id}:{max_seq}",
                "turn_range": [last_archived_turn + 1, max(t.turn_index for t in new_turns)],
            },
        )
        return ArchiveResult(
            archive_id=snapshot.archive_id,
            created=True,
            max_event_seq=snapshot.max_event_seq,
            add_operation_ids=tuple(snapshot.add_operation_ids),
            status="archived",
        )

    def snapshots_for_session(self, session_id: str) -> list[ArchiveSnapshot]:
        return self._store.archives_for_session(session_id)


def archive_rejection_status(application: Any, session_id: str) -> str | None:
    """Classify why an archive admission returned ``None``.

    ``session_end_handler`` reports ``None`` both when no ``archive_service``
    is bound and when the session simply has no durable terminal turns —
    callers must not render the latter as a backend-readiness failure. The
    re-read is idempotent: ``archive_session`` only reads on the
    ``no_trace``/``no_change`` paths this helper cares about.
    """
    services = getattr(
        getattr(application, "settings", None), "application_services", None
    )
    if not isinstance(services, Mapping):
        return None
    service = services.get("archive_service")
    if service is None:
        return None
    try:
        return service.archive_session(session_id).status
    except Exception:
        return None


__all__ = ["ArchiveResult", "ArchiveService", "archive_rejection_status"]
