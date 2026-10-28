"""Durable canonical session trace + memory work store (V3.8).

One transactional domain owns everything the memory lifecycle needs:

- per-session canonical turns and ordered trace events
- terminal commit + the matching durable Outbox intent (same transaction)
- Archive Snapshots with immutable boundaries and their Dreaming intents
- dedicated Skill invocation traces and their evolution claim state
- implicit-feedback window watermarks

The single-machine backend is SQLite (WAL + ``synchronous=FULL``).  A shared
PostgreSQL backend can implement the same :class:`MemoryWorkStore` protocol for
multi-host worker deployments; business code never sees a database path.

All methods are synchronous: the store performs short committed transactions
and callers that must not block the event loop wrap calls in
``asyncio.to_thread``.  Every public method documents its idempotency rules.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Protocol

# ---------------------------------------------------------------------------
# Status vocabularies (spec §6: shared operation/job status enum)
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "budget_exhausted",
        "cancelled",
        "interrupted",
    }
)
"""Normalized terminal outcomes stored on a turn.

``interrupted`` is only assigned by authoritative recovery (a new owner
proving the previous process is gone), never inferred from missing events.
"""

TURN_ACTIVE = "active"

JobStatus = Literal[
    "accepted",
    "processing",
    "completed",
    "no_action",
    "failed",
    "outcome_unknown",
    "stale",
]

JOB_TERMINAL_STATUSES = frozenset({"completed", "no_action", "failed", "stale"})

# Job kinds persisted in the outbox.
JOB_TURN_ADD = "turn_add"
JOB_DREAMING = "archive_dreaming"
JOB_SKILL_EVOLUTION = "skill_evolution"
JOB_IMPLICIT_FEEDBACK = "implicit_feedback"
JOB_GENERIC_WORK = "generic_work"

SCHEMA_VERSION = 1


class MemoryWorkStoreError(RuntimeError):
    """Base error for the work store."""


class TerminalConflictError(MemoryWorkStoreError):
    """A second, different terminal commit arrived for the same turn."""


class TurnNotFoundError(MemoryWorkStoreError):
    """The referenced (session_id, turn_id) does not exist."""


class JobNotFoundError(MemoryWorkStoreError):
    """The referenced outbox job does not exist."""


@dataclass(frozen=True)
class TurnRecord:
    session_id: str
    turn_id: str
    turn_index: int
    run_id: str
    status: str
    message_start_index: int
    message_end_index: int | None
    operation_id: str
    add_job_id: str | None
    terminal_reason: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class TraceEventRecord:
    session_id: str
    turn_id: str
    event_seq: int
    kind: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class OutboxJob:
    job_id: str
    kind: str
    operation_key: str
    session_id: str | None
    turn_id: str | None
    archive_id: str | None
    payload: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: float | None
    last_error: str | None
    result: dict[str, Any] | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ArchiveSnapshot:
    archive_id: str
    session_id: str
    turn_range_start: int
    turn_range_end: int
    max_event_seq: int
    add_operation_ids: tuple[str, ...]
    status: str
    dreaming_job_id: str | None
    created_at: float


@dataclass(frozen=True)
class SkillInvocation:
    invocation_id: str
    session_id: str
    turn_id: str
    skill_id: str
    cloud_skill_id: str
    skill_version_id: str
    skill_content_hash: str
    status: str
    task_input: str | None
    agent_completion: str | None
    outcome: str | None
    score: float | None
    evolution_batch_id: str | None
    created_at: float
    sealed_at: float | None


@dataclass(frozen=True)
class SkillEvolutionBatch:
    batch_id: str
    cloud_skill_id: str
    trace_ids: tuple[str, ...]
    job_id: str | None
    status: str
    created_at: float


@dataclass(frozen=True)
class SkillInvocationStep:
    invocation_id: str
    step_seq: int
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    tool_result_tail: str
    tool_result_truncated: bool
    status: str


@dataclass(frozen=True)
class StoreAudit:
    """Startup audit summary: everything recovered or found inconsistent."""

    terminal_turns_without_intent: tuple[str, ...]
    rebuilt_intents: int
    expired_processing_jobs: tuple[str, ...]
    stale_jobs_marked: int
    active_turns_marked_interrupted: tuple[str, ...]
    archives_without_dreaming_job: tuple[str, ...]


@dataclass(frozen=True)
class _CommitResult:
    turn: TurnRecord
    add_job: OutboxJob


def _now() -> float:
    return time.time()


def _dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(raw: str | None) -> Any:
    if raw is None:
        return None
    return json.loads(raw)


class MemoryWorkStore(Protocol):
    """Abstract transactional store shared by every memory worker."""

    def open(self) -> None: ...
    def close(self) -> None: ...

    # -- turns / trace ----------------------------------------------------
    def begin_turn(
        self, session_id: str, turn_index: int, run_id: str, *, message_start_index: int
    ) -> TurnRecord: ...

    def commit_turn_terminal(
        self,
        session_id: str,
        turn_id: str,
        *,
        status: str,
        events: list[dict[str, Any]],
        operation_id: str,
        intent_payload: dict[str, Any],
    ) -> _CommitResult: ...

    def mark_turn_interrupted(
        self,
        session_id: str,
        turn_id: str,
        *,
        events: list[dict[str, Any]],
        intent_payload: dict[str, Any],
    ) -> _CommitResult: ...

    def turn(self, session_id: str, turn_id: str) -> TurnRecord | None: ...
    def turns_for_session(self, session_id: str) -> list[TurnRecord]: ...
    def events_for_turn(self, session_id: str, turn_id: str) -> list[TraceEventRecord]: ...
    def max_event_seq(self, session_id: str) -> int: ...
    def latest_terminal_boundary(self, session_id: str) -> int: ...

    # -- outbox ------------------------------------------------------------
    def enqueue_job(
        self,
        kind: str,
        operation_key: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        archive_id: str | None = None,
        max_attempts: int = 20,
    ) -> OutboxJob: ...

    def job(self, job_id: str) -> OutboxJob | None: ...
    def job_by_operation_key(self, operation_key: str) -> OutboxJob | None: ...
    def list_jobs(self, kind: str, *, limit: int = 50) -> list[OutboxJob]: ...

    def claim_jobs(
        self,
        owner: str,
        *,
        kinds: tuple[str, ...] | None = None,
        limit: int = 1,
        lease_seconds: float = 300.0,
    ) -> list[OutboxJob]: ...

    def complete_job(
        self, job_id: str, *, owner: str, result: dict[str, Any] | None = None
    ) -> OutboxJob: ...
    def complete_job_no_action(
        self, job_id: str, *, owner: str, result: dict[str, Any] | None = None
    ) -> OutboxJob: ...
    def fail_job(
        self, job_id: str, *, owner: str, error: str, retry_delay_seconds: float = 5.0
    ) -> OutboxJob: ...
    def defer_job(
        self, job_id: str, *, owner: str, reason: str, delay_seconds: float = 10.0
    ) -> OutboxJob: ...
    def mark_job_unknown(self, job_id: str, *, owner: str, error: str) -> OutboxJob: ...
    def terminate_job(self, job_id: str, *, owner: str, error: str) -> OutboxJob: ...
    def mark_job_stale(self, job_id: str) -> None: ...
    def renew_lease(self, job_id: str, *, owner: str, lease_seconds: float) -> bool: ...
    def requeue_expired(self, now: float | None = None) -> list[str]: ...

    # -- archives -----------------------------------------------------------
    def create_archive(
        self,
        session_id: str,
        *,
        turn_range_start: int,
        turn_range_end: int,
        max_event_seq: int,
        add_operation_ids: list[str],
        intent_payload: dict[str, Any],
    ) -> ArchiveSnapshot: ...

    def archive(self, archive_id: str) -> ArchiveSnapshot | None: ...
    def archives_for_session(self, session_id: str) -> list[ArchiveSnapshot]: ...
    def list_archives(self, *, limit: int = 500) -> list[ArchiveSnapshot]: ...
    def attach_dreaming_job(self, archive_id: str, job_id: str) -> None: ...
    def settle_archive(self, archive_id: str, status: str) -> None: ...

    # -- skill invocations ---------------------------------------------------
    def open_skill_invocation(
        self,
        invocation_id: str,
        *,
        session_id: str,
        turn_id: str,
        skill_id: str,
        cloud_skill_id: str,
        skill_version_id: str,
        skill_content_hash: str,
    ) -> SkillInvocation: ...

    def skill_invocation(self, invocation_id: str) -> SkillInvocation | None: ...
    def add_invocation_step(self, invocation_id: str, step: SkillInvocationStep) -> None: ...
    def invocation_steps(self, invocation_id: str) -> list[SkillInvocationStep]: ...
    def seal_skill_invocation(
        self,
        invocation_id: str,
        *,
        task_input: str,
        agent_completion: str,
        outcome: str,
        score: float,
    ) -> SkillInvocation: ...

    def skills_with_invocations(self) -> list[dict[str, Any]]: ...
    def skill_invocations_for_skill(
        self, cloud_skill_id: str, *, limit: int = 100
    ) -> list[SkillInvocation]: ...
    def skill_evolution_batches_for_skill(
        self, cloud_skill_id: str
    ) -> list[SkillEvolutionBatch]: ...
    def unconsumed_trace_count(self, cloud_skill_id: str) -> int: ...
    def claim_skill_traces(
        self, cloud_skill_id: str, *, batch_id: str, count: int
    ) -> list[str]: ...
    def release_skill_traces(self, invocation_ids: Iterable[str]) -> None: ...
    def skill_evolution_flag(self, skill_id: str) -> dict[str, Any] | None: ...
    def skill_evolution_enabled(self, skill_id: str) -> bool: ...
    def skill_evolution_flags(self) -> dict[str, dict[str, Any]]: ...
    def set_skill_evolution_flag(
        self,
        skill_id: str,
        *,
        enabled: bool,
        cloud_skill_id: str | None = None,
    ) -> None: ...
    def pending_evolution_skills(self, min_traces: int) -> list[str]: ...

    # -- feedback watermarks ---------------------------------------------------
    def feedback_watermark(self, scope_key: str, window_start: float) -> dict[str, Any] | None: ...
    def recent_feedback_watermarks(
        self, scope_key: str, *, limit: int = 64
    ) -> list[dict[str, Any]]: ...
    def put_feedback_watermark(
        self, scope_key: str, window_start: float, *, job_id: str, status: str
    ) -> None: ...

    # -- audit ------------------------------------------------------------------
    def audit_and_recover(self, *, owner: str) -> StoreAudit: ...


class SQLiteMemoryWorkStore:
    """SQLite backend implementing :class:`MemoryWorkStore`."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._path),
                isolation_level=None,  # autocommit; explicit transactions below
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._migrate(conn)
            self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _migrate(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turns (
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                message_start_index INTEGER NOT NULL,
                message_end_index INTEGER,
                operation_id TEXT NOT NULL,
                add_job_id TEXT,
                terminal_reason TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (session_id, turn_id),
                UNIQUE (operation_id)
            );
            CREATE TABLE IF NOT EXISTS trace_events (
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                PRIMARY KEY (session_id, turn_id, event_seq)
            );
            CREATE INDEX IF NOT EXISTS idx_trace_events_session
                ON trace_events (session_id, event_seq);
            CREATE TABLE IF NOT EXISTS outbox_jobs (
                job_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                operation_key TEXT NOT NULL UNIQUE,
                session_id TEXT,
                turn_id TEXT,
                archive_id TEXT,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 20,
                lease_owner TEXT,
                lease_expires_at REAL,
                not_before REAL,
                last_error TEXT,
                result_json TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox_jobs (status, kind);
            CREATE TABLE IF NOT EXISTS archives (
                archive_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_range_start INTEGER NOT NULL,
                turn_range_end INTEGER NOT NULL,
                max_event_seq INTEGER NOT NULL,
                add_operation_ids_json TEXT NOT NULL,
                status TEXT NOT NULL,
                dreaming_job_id TEXT,
                created_at REAL NOT NULL,
                UNIQUE (session_id, max_event_seq)
            );
            CREATE TABLE IF NOT EXISTS skill_invocations (
                invocation_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                skill_id TEXT NOT NULL,
                cloud_skill_id TEXT NOT NULL,
                skill_version_id TEXT NOT NULL,
                skill_content_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                task_input TEXT,
                agent_completion TEXT,
                outcome TEXT,
                score REAL,
                evolution_batch_id TEXT,
                created_at REAL NOT NULL,
                sealed_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_skill_invocations_skill
                ON skill_invocations (cloud_skill_id, status, sealed_at);
            CREATE TABLE IF NOT EXISTS skill_invocation_steps (
                invocation_id TEXT NOT NULL,
                step_seq INTEGER NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                tool_result_tail TEXT NOT NULL,
                tool_result_truncated INTEGER NOT NULL,
                status TEXT NOT NULL,
                PRIMARY KEY (invocation_id, step_seq)
            );
            CREATE TABLE IF NOT EXISTS skill_evolution_batches (
                batch_id TEXT PRIMARY KEY,
                cloud_skill_id TEXT NOT NULL,
                trace_ids_json TEXT NOT NULL,
                job_id TEXT,
                status TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_evolution_flags (
                skill_id TEXT PRIMARY KEY,
                cloud_skill_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_watermarks (
                scope_key TEXT NOT NULL,
                window_start REAL NOT NULL,
                job_id TEXT NOT NULL,
                status TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope_key, window_start)
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            raise MemoryWorkStoreError("memory work store is not open")
        return self._conn

    # -- row mapping -----------------------------------------------------------

    @staticmethod
    def _turn_row(row: sqlite3.Row) -> TurnRecord:
        return TurnRecord(
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            turn_index=row["turn_index"],
            run_id=row["run_id"],
            status=row["status"],
            message_start_index=row["message_start_index"],
            message_end_index=row["message_end_index"],
            operation_id=row["operation_id"],
            add_job_id=row["add_job_id"],
            terminal_reason=row["terminal_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _event_row(row: sqlite3.Row) -> TraceEventRecord:
        return TraceEventRecord(
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            event_seq=row["event_seq"],
            kind=row["kind"],
            payload=_load(row["payload_json"]),
        )

    @staticmethod
    def _job_row(row: sqlite3.Row) -> OutboxJob:
        return OutboxJob(
            job_id=row["job_id"],
            kind=row["kind"],
            operation_key=row["operation_key"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            archive_id=row["archive_id"],
            payload=_load(row["payload_json"]) or {},
            status=row["status"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            last_error=row["last_error"],
            result=_load(row["result_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _archive_row(row: sqlite3.Row) -> ArchiveSnapshot:
        return ArchiveSnapshot(
            archive_id=row["archive_id"],
            session_id=row["session_id"],
            turn_range_start=row["turn_range_start"],
            turn_range_end=row["turn_range_end"],
            max_event_seq=row["max_event_seq"],
            add_operation_ids=tuple(_load(row["add_operation_ids_json"]) or ()),
            status=row["status"],
            dreaming_job_id=row["dreaming_job_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _invocation_row(row: sqlite3.Row) -> SkillInvocation:
        return SkillInvocation(
            invocation_id=row["invocation_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            skill_id=row["skill_id"],
            cloud_skill_id=row["cloud_skill_id"],
            skill_version_id=row["skill_version_id"],
            skill_content_hash=row["skill_content_hash"],
            status=row["status"],
            task_input=row["task_input"],
            agent_completion=row["agent_completion"],
            outcome=row["outcome"],
            score=row["score"],
            evolution_batch_id=row["evolution_batch_id"],
            created_at=row["created_at"],
            sealed_at=row["sealed_at"],
        )

    # -- turns ------------------------------------------------------------------

    def begin_turn(
        self, session_id: str, turn_index: int, run_id: str, *, message_start_index: int
    ) -> TurnRecord:
        """Open a turn. Idempotent on (session_id, turn_id): replays return the
        existing row only when run identity matches, otherwise raise."""
        turn_id = f"turn-{turn_index}"
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                (session_id, turn_id),
            ).fetchone()
            if row is not None:
                existing = self._turn_row(row)
                if existing.run_id == run_id:
                    return existing
                raise MemoryWorkStoreError(
                    f"turn {session_id}/{turn_id} already bound to run {existing.run_id}"
                )
            operation_id = f"turnadd:{session_id}:{turn_id}"
            now = _now()
            self._db.execute(
                """INSERT INTO turns (
                    session_id, turn_id, turn_index, run_id, status,
                    message_start_index, message_end_index, operation_id,
                    add_job_id, terminal_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)""",
                (
                    session_id,
                    turn_id,
                    turn_index,
                    run_id,
                    TURN_ACTIVE,
                    message_start_index,
                    operation_id,
                    now,
                    now,
                ),
            )
            return TurnRecord(
                session_id=session_id,
                turn_id=turn_id,
                turn_index=turn_index,
                run_id=run_id,
                status=TURN_ACTIVE,
                message_start_index=message_start_index,
                message_end_index=None,
                operation_id=operation_id,
                add_job_id=None,
                terminal_reason=None,
                created_at=now,
                updated_at=now,
            )

    def _insert_events_locked(
        self, session_id: str, turn_id: str, events: list[dict[str, Any]]
    ) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(event_seq), -1) + 1 AS next_seq "
            "FROM trace_events WHERE session_id = ? AND turn_id = ?",
            (session_id, turn_id),
        ).fetchone()
        next_seq = int(row["next_seq"])
        for offset, event in enumerate(events):
            self._db.execute(
                "INSERT INTO trace_events (session_id, turn_id, event_seq, kind, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, turn_id, next_seq + offset, event["kind"], _dump(event["payload"])),
            )
        return next_seq + len(events) - 1

    def _enqueue_job_locked(
        self,
        job_id: str,
        kind: str,
        operation_key: str,
        payload: dict[str, Any],
        *,
        session_id: str | None,
        turn_id: str | None,
        archive_id: str | None,
        max_attempts: int,
    ) -> OutboxJob:
        now = _now()
        self._db.execute(
            """INSERT INTO outbox_jobs (
                job_id, kind, operation_key, session_id, turn_id, archive_id,
                payload_json, status, attempts, max_attempts, lease_owner,
                lease_expires_at, not_before, last_error, result_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'accepted', 0, ?, NULL, NULL, NULL, NULL, NULL, ?, ?)
            ON CONFLICT (operation_key) DO NOTHING""",
            (
                job_id,
                kind,
                operation_key,
                session_id,
                turn_id,
                archive_id,
                _dump(payload),
                max_attempts,
                now,
                now,
            ),
        )
        row = self._db.execute(
            "SELECT * FROM outbox_jobs WHERE operation_key = ?", (operation_key,)
        ).fetchone()
        return self._job_row(row)

    def commit_turn_terminal(
        self,
        session_id: str,
        turn_id: str,
        *,
        status: str,
        events: list[dict[str, Any]],
        operation_id: str,
        intent_payload: dict[str, Any],
    ) -> _CommitResult:
        """Atomically persist terminal status, canonical events and the Add
        intent. A replay with identical status is a no-op returning the
        existing commit; a *different* second terminal is rejected."""
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal status: {status}")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                (session_id, turn_id),
            ).fetchone()
            if row is None:
                raise TurnNotFoundError(f"{session_id}/{turn_id}")
            turn = self._turn_row(row)
            if turn.status != TURN_ACTIVE:
                if turn.status != status:
                    raise TerminalConflictError(
                        f"turn {session_id}/{turn_id} already terminal={turn.status}, "
                        f"refusing second terminal={status}"
                    )
                job_row = self._db.execute(
                    "SELECT * FROM outbox_jobs WHERE operation_key = ?", (turn.operation_id,)
                ).fetchone()
                return _CommitResult(turn=turn, add_job=self._job_row(job_row))
            if turn.operation_id != operation_id:
                raise TerminalConflictError(
                    f"turn {session_id}/{turn_id} operation mismatch: "
                    f"{turn.operation_id} != {operation_id}"
                )
            with self._db:
                self._insert_events_locked(session_id, turn_id, events)
                add_job = self._enqueue_job_locked(
                    job_id=f"job-{operation_id}",
                    kind=JOB_TURN_ADD,
                    operation_key=operation_id,
                    payload=intent_payload,
                    session_id=session_id,
                    turn_id=turn_id,
                    archive_id=None,
                    max_attempts=20,
                )
                now = _now()
                self._db.execute(
                    "UPDATE turns SET status = ?, message_end_index = ?, add_job_id = ?, "
                    "terminal_reason = ?, updated_at = ? "
                    "WHERE session_id = ? AND turn_id = ?",
                    (
                        status,
                        intent_payload.get("message_end_index"),
                        add_job.job_id,
                        intent_payload.get("terminal_reason"),
                        now,
                        session_id,
                        turn_id,
                    ),
                )
            updated = self.turn(session_id, turn_id)
            assert updated is not None
            return _CommitResult(turn=updated, add_job=add_job)

    def mark_turn_interrupted(
        self,
        session_id: str,
        turn_id: str,
        *,
        events: list[dict[str, Any]],
        intent_payload: dict[str, Any],
    ) -> _CommitResult:
        """Recovery-only terminal for turns left ``active`` by a dead owner."""
        return self.commit_turn_terminal(
            session_id,
            turn_id,
            status="interrupted",
            events=events,
            operation_id=f"turnadd:{session_id}:{turn_id}",
            intent_payload=intent_payload,
        )

    def turn(self, session_id: str, turn_id: str) -> TurnRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? AND turn_id = ?",
                (session_id, turn_id),
            ).fetchone()
            return None if row is None else self._turn_row(row)

    def turns_for_session(self, session_id: str) -> list[TurnRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index",
                (session_id,),
            ).fetchall()
            return [self._turn_row(row) for row in rows]

    def events_for_turn(self, session_id: str, turn_id: str) -> list[TraceEventRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM trace_events WHERE session_id = ? AND turn_id = ? "
                "ORDER BY event_seq",
                (session_id, turn_id),
            ).fetchall()
            return [self._event_row(row) for row in rows]

    def max_event_seq(self, session_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(event_seq), -1) AS m FROM trace_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return int(row["m"])

    def latest_terminal_boundary(self, session_id: str) -> int:
        """Largest archived boundary (event_seq) for a session."""
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(max_event_seq), -1) AS m FROM archives WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return int(row["m"])

    def latest_archived_turn_index(self, session_id: str) -> int:
        """Highest turn_index already covered by an archive (0 = none)."""
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(turn_range_end), 0) AS m FROM archives WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return int(row["m"])

    # -- outbox -----------------------------------------------------------------

    def enqueue_job(
        self,
        kind: str,
        operation_key: str,
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        turn_id: str | None = None,
        archive_id: str | None = None,
        max_attempts: int = 20,
    ) -> OutboxJob:
        """Persist a job. Idempotent on operation_key: an identical key returns
        the existing job regardless of kind/status."""
        if not operation_key:
            raise ValueError("operation_key must be non-empty")
        with self._lock, self._db:
            return self._enqueue_job_locked(
                job_id=f"job-{operation_key}",
                kind=kind,
                operation_key=operation_key,
                payload=payload,
                session_id=session_id,
                turn_id=turn_id,
                archive_id=archive_id,
                max_attempts=max_attempts,
            )

    def job(self, job_id: str) -> OutboxJob | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM outbox_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return None if row is None else self._job_row(row)

    def job_by_operation_key(self, operation_key: str) -> OutboxJob | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM outbox_jobs WHERE operation_key = ?", (operation_key,)
            ).fetchone()
            return None if row is None else self._job_row(row)

    def list_jobs(self, kind: str, *, limit: int = 50) -> list[OutboxJob]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM outbox_jobs WHERE kind = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
            return [self._job_row(row) for row in rows]

    def claim_jobs(
        self,
        owner: str,
        *,
        kinds: tuple[str, ...] | None = None,
        limit: int = 1,
        lease_seconds: float = 300.0,
    ) -> list[OutboxJob]:
        """Atomically claim pending jobs: ``accepted`` (ready) plus
        ``processing``/``outcome_unknown`` rows whose lease expired.

        ``outcome_unknown`` keeps its status when claimed (the executor uses it
        to re-enter reconcile), so it must also be excluded while a lease is
        live — otherwise every poll spawns a duplicate task on the same job
        and the duplicates race at settle time."""
        now = _now()
        expires = now + lease_seconds
        claimed: list[OutboxJob] = []
        with self._lock, self._db:
            if kinds:
                marks = ",".join("?" for _ in kinds)
                kind_filter = f"AND kind IN ({marks})"
                params: tuple[Any, ...] = (now, now, now, now, *kinds)
            else:
                kind_filter = ""
                params = (now, now, now, now)
            rows = self._db.execute(
                f"""SELECT job_id FROM outbox_jobs
                    WHERE (status = 'accepted' AND (not_before IS NULL OR not_before <= ?))
                       OR (status = 'outcome_unknown'
                           AND (not_before IS NULL OR not_before <= ?)
                           AND (lease_expires_at IS NULL OR lease_expires_at < ?))
                       OR (status = 'processing'
                           AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                    {kind_filter}
                    ORDER BY created_at LIMIT ?""",
                (*params, limit),
            ).fetchall()
            for row in rows:
                self._db.execute(
                    "UPDATE outbox_jobs SET lease_owner = ?, lease_expires_at = ?, "
                    "status = CASE WHEN status = 'accepted' THEN 'processing' ELSE status END, "
                    "updated_at = ? WHERE job_id = ?",
                    (owner, expires, now, row["job_id"]),
                )
            for row in rows:
                job = self.job(row["job_id"])
                if job is not None:
                    claimed.append(job)
        return claimed

    def _finish_job(
        self,
        job_id: str,
        *,
        owner: str,
        status: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        retry_delay_seconds: float | None = None,
    ) -> OutboxJob:
        now = _now()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM outbox_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            job = self._job_row(row)
            if job.lease_owner != owner:
                raise MemoryWorkStoreError(
                    f"job {job_id} owned by {job.lease_owner}, not {owner}"
                )
            if job.status in JOB_TERMINAL_STATUSES:
                return job
            if status is None:
                # retry or final failure
                attempts = job.attempts + 1
                if attempts >= job.max_attempts:
                    new_status: str = "failed"
                    not_before = None
                else:
                    new_status = "accepted"
                    not_before = now + (
                        5.0 if retry_delay_seconds is None else retry_delay_seconds
                    )
                self._db.execute(
                    "UPDATE outbox_jobs SET status = ?, attempts = ?, lease_owner = NULL, "
                    "lease_expires_at = NULL, not_before = ?, last_error = ?, "
                    "updated_at = ? WHERE job_id = ?",
                    (new_status, attempts, not_before, error, now, job_id),
                )
            else:
                self._db.execute(
                    "UPDATE outbox_jobs SET status = ?, lease_owner = NULL, "
                    "lease_expires_at = NULL, not_before = NULL, last_error = ?, "
                    "result_json = ?, updated_at = ? WHERE job_id = ?",
                    (status, error, _dump(result) if result is not None else None, now, job_id),
                )
            updated = self.job(job_id)
            assert updated is not None
            return updated

    def complete_job(
        self, job_id: str, *, owner: str, result: dict[str, Any] | None = None
    ) -> OutboxJob:
        return self._finish_job(job_id, owner=owner, status="completed", result=result)

    def complete_job_no_action(
        self, job_id: str, *, owner: str, result: dict[str, Any] | None = None
    ) -> OutboxJob:
        return self._finish_job(job_id, owner=owner, status="no_action", result=result)

    def fail_job(
        self, job_id: str, *, owner: str, error: str, retry_delay_seconds: float = 5.0
    ) -> OutboxJob:
        return self._finish_job(
            job_id, owner=owner, error=error, retry_delay_seconds=retry_delay_seconds
        )

    def defer_job(
        self, job_id: str, *, owner: str, reason: str, delay_seconds: float = 10.0
    ) -> OutboxJob:
        """Reschedule a job that is waiting on an external dependency (e.g. a
        seed operation still processing server-side). A dependency that is
        merely in flight is not a failure, so ``attempts`` is NOT consumed —
        the executor bounds the wait with its own budget."""
        now = _now()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM outbox_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            job = self._job_row(row)
            if job.lease_owner != owner:
                raise MemoryWorkStoreError(
                    f"job {job_id} owned by {job.lease_owner}, not {owner}"
                )
            if job.status in JOB_TERMINAL_STATUSES:
                return job
            # outcome_unknown keeps its status so the next claim re-enters the
            # reconcile branch instead of resubmitting the operation.
            new_status = (
                "outcome_unknown" if job.status == "outcome_unknown" else "accepted"
            )
            self._db.execute(
                "UPDATE outbox_jobs SET status = ?, lease_owner = NULL, "
                "lease_expires_at = NULL, not_before = ?, last_error = ?, "
                "updated_at = ? WHERE job_id = ?",
                (new_status, now + delay_seconds, reason, now, job_id),
            )
            updated = self.job(job_id)
            assert updated is not None
            return updated

    def mark_job_unknown(self, job_id: str, *, owner: str, error: str) -> OutboxJob:
        return self._finish_job(job_id, owner=owner, status="outcome_unknown", error=error)

    def terminate_job(self, job_id: str, *, owner: str, error: str) -> OutboxJob:
        """Permanently fail a job without consuming a retry attempt."""
        return self._finish_job(job_id, owner=owner, status="failed", error=error)

    def mark_job_stale(self, job_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE outbox_jobs SET status = 'stale', updated_at = ? WHERE job_id = ?",
                (_now(), job_id),
            )

    def renew_lease(self, job_id: str, *, owner: str, lease_seconds: float) -> bool:
        with self._lock, self._db:
            cur = self._db.execute(
                "UPDATE outbox_jobs SET lease_expires_at = ?, updated_at = ? "
                "WHERE job_id = ? AND lease_owner = ?",
                (_now() + lease_seconds, _now(), job_id, owner),
            )
            return cur.rowcount == 1

    def requeue_expired(self, now: float | None = None) -> list[str]:
        """Requeue leased jobs past their lease; returns their job_ids."""
        now = _now() if now is None else now
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT job_id FROM outbox_jobs WHERE status IN ('processing', 'outcome_unknown') "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (now,),
            ).fetchall()
            ids = [row["job_id"] for row in rows]
            if ids:
                self._db.execute(
                    "UPDATE outbox_jobs SET lease_owner = NULL, lease_expires_at = NULL, "
                    "not_before = NULL, status = 'accepted', updated_at = ? "
                    f"WHERE job_id IN ({','.join('?' for _ in ids)})",
                    (now, *ids),
                )
            return ids

    # -- archives -----------------------------------------------------------------

    def create_archive(
        self,
        session_id: str,
        *,
        turn_range_start: int,
        turn_range_end: int,
        max_event_seq: int,
        add_operation_ids: list[str],
        intent_payload: dict[str, Any],
    ) -> ArchiveSnapshot:
        """Atomically lock the boundary and enqueue the Dreaming intent.
        Idempotent on the immutable boundary (session_id, max_event_seq)."""
        if turn_range_end < turn_range_start:
            raise ValueError("empty archive turn range")
        if max_event_seq < 0:
            raise ValueError("max_event_seq must be >= 0")
        archive_id = f"archive:{session_id}:{max_event_seq}"
        with self._lock, self._db:
            existing = self._db.execute(
                "SELECT * FROM archives WHERE session_id = ? AND max_event_seq = ?",
                (session_id, max_event_seq),
            ).fetchone()
            if existing is not None:
                return self._archive_row(existing)
            self._db.execute(
                """INSERT INTO archives (
                    archive_id, session_id, turn_range_start, turn_range_end,
                    max_event_seq, add_operation_ids_json, status, dreaming_job_id,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, ?)""",
                (
                    archive_id,
                    session_id,
                    turn_range_start,
                    turn_range_end,
                    max_event_seq,
                    _dump(list(add_operation_ids)),
                    _now(),
                ),
            )
            job = self._enqueue_job_locked(
                job_id=f"job-dreaming:{archive_id}",
                kind=JOB_DREAMING,
                operation_key=f"dreaming:{archive_id}",
                payload=intent_payload,
                session_id=session_id,
                turn_id=None,
                archive_id=archive_id,
                max_attempts=20,
            )
            self._db.execute(
                "UPDATE archives SET dreaming_job_id = ? WHERE archive_id = ?",
                (job.job_id, archive_id),
            )
            snapshot = self.archive(archive_id)
            assert snapshot is not None
            return snapshot

    def archive(self, archive_id: str) -> ArchiveSnapshot | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM archives WHERE archive_id = ?", (archive_id,)
            ).fetchone()
            return None if row is None else self._archive_row(row)

    def archives_for_session(self, session_id: str) -> list[ArchiveSnapshot]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM archives WHERE session_id = ? ORDER BY max_event_seq",
                (session_id,),
            ).fetchall()
            return [self._archive_row(row) for row in rows]

    def list_archives(self, *, limit: int = 500) -> list[ArchiveSnapshot]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM archives ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._archive_row(row) for row in rows]

    def attach_dreaming_job(self, archive_id: str, job_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE archives SET dreaming_job_id = ? WHERE archive_id = ?",
                (job_id, archive_id),
            )

    def settle_archive(self, archive_id: str, status: str) -> None:
        """Mark the archive's lifecycle state once its Dreaming job reaches a
        terminal outcome: ``dreamed`` on completed/no_action, ``failed`` on a
        permanent job failure. ``pending`` means the snapshot is frozen but
        its Dreaming intent has not settled yet."""
        with self._lock, self._db:
            self._db.execute(
                "UPDATE archives SET status = ? WHERE archive_id = ?",
                (status, archive_id),
            )

    # -- skill invocations ---------------------------------------------------------

    def open_skill_invocation(
        self,
        invocation_id: str,
        *,
        session_id: str,
        turn_id: str,
        skill_id: str,
        cloud_skill_id: str,
        skill_version_id: str,
        skill_content_hash: str,
    ) -> SkillInvocation:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO skill_invocations (
                    invocation_id, session_id, turn_id, skill_id, cloud_skill_id,
                    skill_version_id, skill_content_hash, status, task_input,
                    agent_completion, outcome, score, evolution_batch_id,
                    created_at, sealed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', NULL, NULL, NULL, NULL, NULL, ?, NULL)
                ON CONFLICT (invocation_id) DO NOTHING""",
                (
                    invocation_id,
                    session_id,
                    turn_id,
                    skill_id,
                    cloud_skill_id,
                    skill_version_id,
                    skill_content_hash,
                    _now(),
                ),
            )
            inv = self.skill_invocation(invocation_id)
            assert inv is not None
            return inv

    def skill_invocation(self, invocation_id: str) -> SkillInvocation | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM skill_invocations WHERE invocation_id = ?", (invocation_id,)
            ).fetchone()
            return None if row is None else self._invocation_row(row)

    def add_invocation_step(self, invocation_id: str, step: SkillInvocationStep) -> None:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO skill_invocation_steps (
                    invocation_id, step_seq, tool_call_id, tool_name, arguments_json,
                    tool_result_tail, tool_result_truncated, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (invocation_id, step_seq) DO NOTHING""",
                (
                    invocation_id,
                    step.step_seq,
                    step.tool_call_id,
                    step.tool_name,
                    _dump(step.arguments),
                    step.tool_result_tail,
                    1 if step.tool_result_truncated else 0,
                    step.status,
                ),
            )

    def invocation_steps(self, invocation_id: str) -> list[SkillInvocationStep]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM skill_invocation_steps WHERE invocation_id = ? ORDER BY step_seq",
                (invocation_id,),
            ).fetchall()
            return [
                SkillInvocationStep(
                    invocation_id=row["invocation_id"],
                    step_seq=row["step_seq"],
                    tool_call_id=row["tool_call_id"],
                    tool_name=row["tool_name"],
                    arguments=_load(row["arguments_json"]) or {},
                    tool_result_tail=row["tool_result_tail"],
                    tool_result_truncated=bool(row["tool_result_truncated"]),
                    status=row["status"],
                )
                for row in rows
            ]

    def seal_skill_invocation(
        self,
        invocation_id: str,
        *,
        task_input: str,
        agent_completion: str,
        outcome: str,
        score: float,
    ) -> SkillInvocation:
        if outcome not in {"success", "failure"}:
            raise ValueError(f"invalid outcome: {outcome}")
        if outcome == "success" and not (0.5 <= score <= 1.0):
            raise ValueError("success score must be in [0.5, 1]")
        if outcome == "failure" and not (0.0 <= score <= 0.5):
            raise ValueError("failure score must be in [0, 0.5]")
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT status FROM skill_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                raise MemoryWorkStoreError(f"unknown invocation {invocation_id}")
            if row["status"] == "open":
                self._db.execute(
                    "UPDATE skill_invocations SET status = 'sealed', task_input = ?, "
                    "agent_completion = ?, outcome = ?, score = ?, sealed_at = ? "
                    "WHERE invocation_id = ?",
                    (task_input, agent_completion, outcome, score, _now(), invocation_id),
                )
            inv = self.skill_invocation(invocation_id)
            assert inv is not None
            return inv

    def unconsumed_trace_count(self, cloud_skill_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS c FROM skill_invocations WHERE cloud_skill_id = ? "
                "AND status = 'sealed'",
                (cloud_skill_id,),
            ).fetchone()
            return int(row["c"])

    def claim_skill_traces(
        self, cloud_skill_id: str, *, batch_id: str, count: int
    ) -> list[str]:
        """Atomically claim exactly ``count`` sealed traces for one batch.

        Returns the claimed invocation ids; returns fewer than ``count`` only
        when fewer sealed traces exist (the caller then decides whether to
        create the job).  A trace already claimed is never returned twice.
        """
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT invocation_id FROM skill_invocations WHERE cloud_skill_id = ? "
                "AND status = 'sealed' ORDER BY sealed_at, invocation_id LIMIT ?",
                (cloud_skill_id, count),
            ).fetchall()
            ids = [row["invocation_id"] for row in rows]
            if ids:
                self._db.execute(
                    "UPDATE skill_invocations SET status = 'consumed', "
                    "evolution_batch_id = ? WHERE invocation_id IN "
                    f"({','.join('?' for _ in ids)}) AND status = 'sealed'",
                    (batch_id, *ids),
                )
            return ids

    def release_skill_traces(self, invocation_ids: Iterable[str]) -> None:
        """Undo a claim that did not form a full batch.

        A concurrent claim can leave a caller holding fewer than the batch
        threshold; the loser must hand its partial claim back so a later seal
        can still reach it.
        """
        ids = list(invocation_ids)
        if not ids:
            return
        with self._lock, self._db:
            self._db.execute(
                "UPDATE skill_invocations SET status = 'sealed', "
                "evolution_batch_id = NULL WHERE invocation_id IN "
                f"({','.join('?' for _ in ids)}) AND status = 'consumed'",
                ids,
            )

    def create_evolution_batch(
        self, batch_id: str, *, cloud_skill_id: str, trace_ids: list[str], job_id: str | None
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO skill_evolution_batches (
                    batch_id, cloud_skill_id, trace_ids_json, job_id, status, created_at
                ) VALUES (?, ?, ?, ?, 'created', ?)
                ON CONFLICT (batch_id) DO UPDATE SET
                    job_id = excluded.job_id
                WHERE skill_evolution_batches.job_id IS NULL
                    AND excluded.job_id IS NOT NULL""",
                (batch_id, cloud_skill_id, _dump(list(trace_ids)), job_id, _now()),
            )

    def set_skill_evolution_flag(
        self,
        skill_id: str,
        *,
        enabled: bool,
        cloud_skill_id: str | None = None,
    ) -> None:
        """Upsert the per-skill evolution toggle.

        ``cloud_skill_id`` is the server-minted id captured at enable time
        (``POST /v1/skills/register``); disabling or re-enabling without a
        fresh registration keeps the previously stored id.
        """
        with self._lock, self._db:
            self._db.execute(
                """INSERT INTO skill_evolution_flags (skill_id, cloud_skill_id, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (skill_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    cloud_skill_id = COALESCE(excluded.cloud_skill_id,
                        skill_evolution_flags.cloud_skill_id),
                    updated_at = excluded.updated_at""",
                (skill_id, cloud_skill_id, 1 if enabled else 0, _now()),
            )

    def skill_evolution_flag(self, skill_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT skill_id, cloud_skill_id, enabled, updated_at "
                "FROM skill_evolution_flags WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "skill_id": row["skill_id"],
            "cloud_skill_id": row["cloud_skill_id"],
            "enabled": bool(row["enabled"]),
            "updated_at": row["updated_at"],
        }

    def skill_evolution_enabled(self, skill_id: str) -> bool:
        flag = self.skill_evolution_flag(skill_id)
        return bool(flag and flag["enabled"])

    def skill_evolution_flags(self) -> dict[str, dict[str, Any]]:
        """All flag rows keyed by skill_id (for list views)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT skill_id, cloud_skill_id, enabled, updated_at "
                "FROM skill_evolution_flags"
            ).fetchall()
        return {
            row["skill_id"]: {
                "skill_id": row["skill_id"],
                "cloud_skill_id": row["cloud_skill_id"],
                "enabled": bool(row["enabled"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def pending_evolution_skills(self, min_traces: int) -> list[str]:
        """Skills having at least ``min_traces`` sealed-but-unclaimed traces
        (used by startup recovery to create missing evolution jobs)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT cloud_skill_id FROM skill_invocations WHERE status = 'sealed' "
                "GROUP BY cloud_skill_id HAVING COUNT(*) >= ?",
                (min_traces,),
            ).fetchall()
            return [row["cloud_skill_id"] for row in rows]

    def skills_with_invocations(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT cloud_skill_id, MIN(skill_id) AS skill_id, "
                "COUNT(*) AS invocation_count, MAX(sealed_at) AS last_sealed_at "
                "FROM skill_invocations GROUP BY cloud_skill_id "
                "ORDER BY last_sealed_at DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def skill_invocations_for_skill(
        self, cloud_skill_id: str, *, limit: int = 100
    ) -> list[SkillInvocation]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM skill_invocations WHERE cloud_skill_id = ? "
                "ORDER BY COALESCE(sealed_at, created_at) DESC LIMIT ?",
                (cloud_skill_id, limit),
            ).fetchall()
            return [self._invocation_row(row) for row in rows]

    def skill_evolution_batches_for_skill(
        self, cloud_skill_id: str
    ) -> list[SkillEvolutionBatch]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM skill_evolution_batches WHERE cloud_skill_id = ? "
                "ORDER BY created_at DESC",
                (cloud_skill_id,),
            ).fetchall()
            return [
                SkillEvolutionBatch(
                    batch_id=row["batch_id"],
                    cloud_skill_id=row["cloud_skill_id"],
                    trace_ids=tuple(_load(row["trace_ids_json"]) or ()),
                    job_id=row["job_id"],
                    status=row["status"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]

    # -- feedback watermarks ---------------------------------------------------------

    def feedback_watermark(self, scope_key: str, window_start: float) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM feedback_watermarks WHERE scope_key = ? AND window_start = ?",
                (scope_key, window_start),
            ).fetchone()
            if row is None:
                return None
            return {
                "scope_key": row["scope_key"],
                "window_start": row["window_start"],
                "job_id": row["job_id"],
                "status": row["status"],
                "updated_at": row["updated_at"],
            }

    def recent_feedback_watermarks(
        self, scope_key: str, *, limit: int = 64
    ) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._db.execute(
                "SELECT * FROM feedback_watermarks WHERE scope_key = ? "
                "ORDER BY window_start DESC LIMIT ?",
                (scope_key, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def put_feedback_watermark(
        self, scope_key: str, window_start: float, *, job_id: str, status: str
    ) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO feedback_watermarks "
                "(scope_key, window_start, job_id, status, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (scope_key, window_start) DO UPDATE SET "
                "job_id = excluded.job_id, status = excluded.status, "
                "updated_at = excluded.updated_at",
                (scope_key, window_start, job_id, status, _now()),
            )

    # -- audit ------------------------------------------------------------------------

    def audit_and_recover(
        self, *, owner: str, exclude_turns: tuple[tuple[str, str], ...] = ()
    ) -> StoreAudit:
        """Startup recovery in one pass:

        - requeue jobs whose lease expired (workers that died)
        - build intents for terminal turns missing their Add job
        - mark ``active`` turns interrupted — only turns NOT in
          ``exclude_turns`` (the live turns the calling process owns). Under
          the single-writer contract every other ``active`` row belongs to a
          dead owner.
        - report archives lacking a dreaming job

        Returns a full audit record for structured logging.
        """
        with self._lock, self._db:
            expired = self.requeue_expired()
            missing_intent: list[str] = []
            rebuilt = 0
            rows = self._db.execute(
                "SELECT t.session_id, t.turn_id, t.operation_id, t.status FROM turns t "
                "WHERE t.status != 'active' AND t.add_job_id IS NULL"
            ).fetchall()
            for row in rows:
                payload = {"recovery": "audit_rebuilt_intent"}
                job = self._enqueue_job_locked(
                    job_id=f"job-{row['operation_id']}",
                    kind=JOB_TURN_ADD,
                    operation_key=row["operation_id"],
                    payload=payload,
                    session_id=row["session_id"],
                    turn_id=row["turn_id"],
                    archive_id=None,
                    max_attempts=20,
                )
                self._db.execute(
                    "UPDATE turns SET add_job_id = ?, updated_at = ? "
                    "WHERE session_id = ? AND turn_id = ?",
                    (job.job_id, _now(), row["session_id"], row["turn_id"]),
                )
                missing_intent.append(f"{row['session_id']}/{row['turn_id']}")
                rebuilt += 1
            # Active turns are owned by a live process. Recovery runs under the
            # new owner only after proving the old owner is gone — the store is
            # per-(data_root) and a second live application is excluded by the
            # single-writer contract documented for the SQLite backend. Turns in
            # ``exclude_turns`` belong to this process and stay active.
            excluded = set(exclude_turns)
            interrupted: list[str] = []
            rows = self._db.execute(
                "SELECT session_id, turn_id, operation_id FROM turns WHERE status = 'active'"
            ).fetchall()
            for row in rows:
                if (row["session_id"], row["turn_id"]) in excluded:
                    continue
                intent_payload = {"recovery": "interrupted_turn", "terminal_reason": "recovered"}
                self._insert_events_locked(row["session_id"], row["turn_id"], [])
                job = self._enqueue_job_locked(
                    job_id=f"job-{row['operation_id']}",
                    kind=JOB_TURN_ADD,
                    operation_key=row["operation_id"],
                    payload=intent_payload,
                    session_id=row["session_id"],
                    turn_id=row["turn_id"],
                    archive_id=None,
                    max_attempts=20,
                )
                self._db.execute(
                    "UPDATE turns SET status = 'interrupted', add_job_id = ?, "
                    "terminal_reason = 'recovered', updated_at = ? "
                    "WHERE session_id = ? AND turn_id = ?",
                    (job.job_id, _now(), row["session_id"], row["turn_id"]),
                )
                interrupted.append(f"{row['session_id']}/{row['turn_id']}")
            rows = self._db.execute(
                "SELECT archive_id FROM archives WHERE dreaming_job_id IS NULL"
            ).fetchall()
            archives_missing = tuple(row["archive_id"] for row in rows)
            return StoreAudit(
                terminal_turns_without_intent=tuple(missing_intent),
                rebuilt_intents=rebuilt,
                expired_processing_jobs=tuple(expired),
                stale_jobs_marked=0,
                active_turns_marked_interrupted=tuple(interrupted),
                archives_without_dreaming_job=archives_missing,
            )


__all__ = [
    "ArchiveSnapshot",
    "JOB_DREAMING",
    "JOB_IMPLICIT_FEEDBACK",
    "JOB_SKILL_EVOLUTION",
    "JOB_TURN_ADD",
    "JOB_GENERIC_WORK",
    "JobNotFoundError",
    "JobStatus",
    "MemoryWorkStore",
    "MemoryWorkStoreError",
    "OutboxJob",
    "SQLiteMemoryWorkStore",
    "SkillEvolutionBatch",
    "SkillInvocation",
    "SkillInvocationStep",
    "StoreAudit",
    "TERMINAL_STATUSES",
    "TerminalConflictError",
    "TraceEventRecord",
    "TurnNotFoundError",
    "TurnRecord",
    "TURN_ACTIVE",
]
