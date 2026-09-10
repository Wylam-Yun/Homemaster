"""Durable SQLite store for V3.4 permission requests, decisions and grants.

One Store owns six tables (design section 8) and performs every state
transition inside a single ``BEGIN IMMEDIATE`` transaction on one
connection. Business events are written in the same transaction; the
append-only JSONL mirror is best-effort and never reverses a commit.

Isolation rules: grants match only on the exact
``(environment_id, resource_kind, resource_id, action)`` key, guarded by
a partial unique index. No fuzzy, category or cross-environment matching.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from homemaster.permissions.models import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalResolution,
    ApprovalSubmission,
    ExecutionObservation,
    ItemDecision,
    PermissionStorageUnavailable,
    PreparedPhysicalRequest,
    ResourceKey,
)

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_ATTEMPTS_DEFAULT = 3

_TERMINAL_STATUSES = frozenset(
    {
        "succeeded",
        "failed",
        "outcome_unknown",
        "blocked",
        "expired",
        "cancelled",
        "interrupted",
    }
)
_SUBMITTABLE_STATUSES = frozenset({"prepared", "awaiting_approval"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS permission_grants (
  grant_id TEXT PRIMARY KEY,
  environment_id TEXT NOT NULL,
  resource_kind TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  source_request_id TEXT NOT NULL,
  source_item_id TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by TEXT,
  revision INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS active_permission_scope
ON permission_grants(environment_id, resource_kind, resource_id, action)
WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS permission_requests (
  request_id TEXT PRIMARY KEY,
  approval_id TEXT NOT NULL UNIQUE,
  environment_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  immutable_plan_json TEXT NOT NULL,
  plan_digest TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  deadline_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS permission_request_items (
  item_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES permission_requests(request_id),
  environment_id TEXT NOT NULL,
  resource_kind TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  action TEXT NOT NULL,
  display_name TEXT NOT NULL,
  location TEXT NOT NULL,
  action_label TEXT NOT NULL,
  step_ids_json TEXT NOT NULL,
  decision TEXT,
  matched_grant_id TEXT REFERENCES permission_grants(grant_id)
);
CREATE TABLE IF NOT EXISTS permission_steps (
  step_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES permission_requests(request_id),
  binding_ref TEXT NOT NULL,
  required_item_ids_json TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  backend_receipt_ref TEXT,
  occupancy_state TEXT
);
CREATE TABLE IF NOT EXISTS permission_submissions (
  submission_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL REFERENCES permission_requests(request_id),
  request_revision INTEGER NOT NULL,
  payload_digest TEXT NOT NULL,
  response_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS permission_events (
  event_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL,
  step_id TEXT,
  grant_id TEXT,
  event_type TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class GrantRecord:
    grant_id: str
    environment_id: str
    resource_kind: str
    resource_id: str
    action: str
    created_at: str
    created_by: str
    source_request_id: str
    source_item_id: str
    revoked_at: str | None
    revoked_by: str | None
    revision: int

    @property
    def key(self) -> ResourceKey:
        return ResourceKey(
            environment_id=self.environment_id,
            resource_kind=self.resource_kind,  # type: ignore[arg-type]
            resource_id=self.resource_id,
            action=self.action,
        )

    @property
    def active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class StoredItem:
    item_id: str
    key: ResourceKey
    display_name: str
    location: str
    action_label: str
    step_ids: tuple[str, ...]
    decision: str | None
    matched_grant_id: str | None


@dataclass(frozen=True)
class StoredStep:
    step_id: str
    binding_ref: str
    required_item_ids: tuple[str, ...]
    summary: str
    status: str
    attempt_count: int
    backend_receipt_ref: str | None
    occupancy_state: str | None


@dataclass(frozen=True)
class StoredRequest:
    request_id: str
    approval_id: str
    environment_id: str
    session_id: str
    run_id: str
    intent_id: str
    intent_summary: str
    revision: int
    status: str
    created_at: str
    deadline_at: str
    resolved_at: str | None
    items: tuple[StoredItem, ...]
    steps: tuple[StoredStep, ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _canonical_instant(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_instant(value: str) -> datetime:
    text = value.strip()
    candidate = f"{text[:-1]}+00:00" if text.endswith(("Z", "z")) else text
    return datetime.fromisoformat(candidate)


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _sanitize_tenant(tenant_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (tenant_id or "").strip())
    return cleaned or "default"


def default_store_path(session_dir: str | Path, tenant_id: str) -> Path:
    """Derive the default database path below the session dir with tenant partition."""
    base = Path(session_dir).expanduser()
    return base.parent / _sanitize_tenant(tenant_id) / "permissions.sqlite3"


def resolve_store_path(
    configured: str | None, *, session_dir: str | Path, tenant_id: str
) -> Path:
    """Resolve the effective database path; an explicit value wins."""
    if configured and configured.strip():
        candidate = Path(configured.strip()).expanduser()
        if candidate.is_absolute():
            return candidate
        return Path(session_dir).expanduser().parent / candidate
    return default_store_path(session_dir, tenant_id)


class PermissionStore:
    """One SQLite-backed permission state machine."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path).expanduser()
        self._clock = clock or _utc_now
        self._lock = threading.RLock()
        self._staged: list[dict[str, Any]] = []
        self._conn = self._connect(self._path)
        try:
            self._ensure_schema()
        except Exception:
            self._conn.close()
            raise

    @classmethod
    def open(
        cls, path: str | Path, *, clock: Callable[[], datetime] | None = None
    ) -> PermissionStore:
        """Create the database, verify the schema and recover safely."""
        return cls(path, clock=clock)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def mirror_path(self) -> Path:
        return self._path.with_suffix(".events.jsonl")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        except sqlite3.Error as exc:
            raise PermissionStorageUnavailable(f"cannot open permission store: {exc}") from exc
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA synchronous = NORMAL")
        except sqlite3.Error as exc:
            conn.close()
            raise PermissionStorageUnavailable(
                f"cannot configure permission store: {exc}"
            ) from exc
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._conn:
                self._conn.executescript(_SCHEMA_SQL)
                version = self._conn.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise PermissionStorageUnavailable(
                        f"unsupported permission schema version: {version}"
                    )
        except PermissionStorageUnavailable:
            raise
        except sqlite3.Error as exc:
            raise PermissionStorageUnavailable(
                f"cannot initialize permission schema: {exc}"
            ) from exc

    def _now_iso(self) -> str:
        return _canonical_instant(self._clock())

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    # -- requests ------------------------------------------------------

    def create_request(self, request: PreparedPhysicalRequest) -> str:
        """Persist one prepared call; duplicate ids with other payloads conflict."""
        plan_json = request.model_dump_json()
        digest = _digest(json.loads(plan_json))
        with self._lock:
            try:
                with self._conn:
                    self._execute(
                        "INSERT INTO permission_requests "
                        "(request_id, approval_id, environment_id, session_id, run_id,"
                        " intent_id, revision, immutable_plan_json, plan_digest, status,"
                        " created_at, deadline_at, resolved_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            request.request_id,
                            request.approval_id,
                            request.environment_id,
                            request.session_id,
                            request.run_id,
                            request.intent_id,
                            request.revision,
                            plan_json,
                            digest,
                            "prepared",
                            request.created_at,
                            request.deadline_at,
                            None,
                        ),
                    )
                    for item in request.requirements:
                        self._execute(
                            "INSERT INTO permission_request_items "
                            "(item_id, request_id, environment_id, resource_kind,"
                            " resource_id, action, display_name, location, action_label,"
                            " step_ids_json, decision, matched_grant_id)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                item.item_id,
                                request.request_id,
                                item.key.environment_id,
                                item.key.resource_kind,
                                item.key.resource_id,
                                item.key.action,
                                item.display_name,
                                item.location,
                                item.action_label,
                                json.dumps(list(item.step_ids), ensure_ascii=False),
                                None,
                                None,
                            ),
                        )
                    for step in request.steps:
                        self._execute(
                            "INSERT INTO permission_steps "
                            "(step_id, request_id, binding_ref, required_item_ids_json,"
                            " summary, status, attempt_count, backend_receipt_ref,"
                            " occupancy_state)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                step.step_id,
                                request.request_id,
                                step.binding_ref,
                                json.dumps(
                                    list(step.required_item_ids), ensure_ascii=False
                                ),
                                step.summary,
                                "prepared",
                                0,
                                None,
                                None,
                            ),
                        )
                    self._record_event(
                        request.request_id,
                        None,
                        None,
                        "request_prepared",
                        {"revision": request.revision},
                    )
            except sqlite3.IntegrityError as exc:
                raise ApprovalConflict(
                    f"permission request id already exists: {exc}"
                ) from exc
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot persist permission request: {exc}"
                ) from exc
        self._emit_pending()
        return request.request_id

    def get_request(self, approval_id: str) -> StoredRequest:
        with self._lock:
            row = self._execute(
                "SELECT * FROM permission_requests WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown approval id: {approval_id}")
            return self._bundle(row["request_id"])

    def _bundle(self, request_id: str) -> StoredRequest:
        row = self._execute(
            "SELECT * FROM permission_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown request id: {request_id}")
        plan = json.loads(row["immutable_plan_json"])
        items = tuple(
            StoredItem(
                item_id=item["item_id"],
                key=ResourceKey(
                    environment_id=item["environment_id"],
                    resource_kind=item["resource_kind"],
                    resource_id=item["resource_id"],
                    action=item["action"],
                ),
                display_name=item["display_name"],
                location=item["location"],
                action_label=item["action_label"],
                step_ids=tuple(json.loads(item["step_ids_json"])),
                decision=item["decision"],
                matched_grant_id=item["matched_grant_id"],
            )
            for item in self._execute(
                "SELECT * FROM permission_request_items WHERE request_id = ?"
                " ORDER BY item_id",
                (request_id,),
            ).fetchall()
        )
        steps = tuple(
            StoredStep(
                step_id=step["step_id"],
                binding_ref=step["binding_ref"],
                required_item_ids=tuple(json.loads(step["required_item_ids_json"])),
                summary=step["summary"],
                status=step["status"],
                attempt_count=step["attempt_count"],
                backend_receipt_ref=step["backend_receipt_ref"],
                occupancy_state=step["occupancy_state"],
            )
            for step in self._execute(
                "SELECT * FROM permission_steps WHERE request_id = ? ORDER BY step_id",
                (request_id,),
            ).fetchall()
        )
        return StoredRequest(
            request_id=row["request_id"],
            approval_id=row["approval_id"],
            environment_id=row["environment_id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            intent_id=plan.get("intent_id", ""),
            intent_summary=plan.get("intent_summary", ""),
            revision=row["revision"],
            status=row["status"],
            created_at=row["created_at"],
            deadline_at=row["deadline_at"],
            resolved_at=row["resolved_at"],
            items=items,
            steps=steps,
        )

    def mark_awaiting_approval(self, request_id: str) -> str:
        with self._lock:
            try:
                with self._conn:
                    row = self._execute(
                        "SELECT status FROM permission_requests WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"unknown request id: {request_id}")
                    if row["status"] in _TERMINAL_STATUSES:
                        raise ApprovalConflict(
                            f"request {request_id} is already {row['status']}"
                        )
                    if row["status"] == "prepared":
                        self._execute(
                            "UPDATE permission_requests SET status = ?"
                            " WHERE request_id = ?",
                            ("awaiting_approval", request_id),
                        )
                        self._record_event(
                            request_id, None, None, "approval_requested", {}
                        )
            except (KeyError, ApprovalConflict):
                raise
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot mark approval requested: {exc}"
                ) from exc
        self._emit_pending()
        return self._bundle(request_id).status

    # -- grants --------------------------------------------------------

    def matching_grants(
        self, keys: Iterable[ResourceKey]
    ) -> dict[ResourceKey, GrantRecord]:
        unique = list(dict.fromkeys(keys))
        if not unique:
            return {}
        with self._lock:
            found: dict[ResourceKey, GrantRecord] = {}
            for key in unique:
                row = self._execute(
                    "SELECT * FROM permission_grants WHERE environment_id = ?"
                    " AND resource_kind = ? AND resource_id = ? AND action = ?"
                    " AND revoked_at IS NULL",
                    (
                        key.environment_id,
                        key.resource_kind,
                        key.resource_id,
                        key.action,
                    ),
                ).fetchone()
                if row is not None:
                    found[key] = self._grant_from_row(row)
            return found

    @staticmethod
    def _grant_from_row(row: sqlite3.Row) -> GrantRecord:
        return GrantRecord(
            grant_id=row["grant_id"],
            environment_id=row["environment_id"],
            resource_kind=row["resource_kind"],
            resource_id=row["resource_id"],
            action=row["action"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            source_request_id=row["source_request_id"],
            source_item_id=row["source_item_id"],
            revoked_at=row["revoked_at"],
            revoked_by=row["revoked_by"],
            revision=row["revision"],
        )

    # -- submissions ---------------------------------------------------

    @staticmethod
    def _submission_digest(submission: ApprovalSubmission) -> str:
        return _digest(
            {
                "approval_id": None,
                "request_revision": submission.request_revision,
                "decisions": sorted(
                    (d.item_id, d.choice) for d in submission.decisions
                ),
            }
        )

    def submit(
        self, approval_id: str, submission: ApprovalSubmission, actor: str
    ) -> ApprovalResolution:
        """Atomically record per-item decisions; commit precedes any wakeup."""
        digest = self._submission_digest(submission)
        resolution: ApprovalResolution | None = None
        expired_request: str | None = None
        with self._lock:
            try:
                with self._conn:
                    stored = self._execute(
                        "SELECT * FROM permission_submissions WHERE submission_id = ?",
                        (submission.submission_id,),
                    ).fetchone()
                    if stored is not None:
                        if stored["payload_digest"] != digest:
                            raise ApprovalConflict(
                                "submission id reused with different content"
                            )
                        return ApprovalResolution.model_validate_json(
                            stored["response_json"]
                        )
                    row = self._execute(
                        "SELECT * FROM permission_requests WHERE approval_id = ?",
                        (approval_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"unknown approval id: {approval_id}")
                    request_id = row["request_id"]
                    if row["status"] not in _SUBMITTABLE_STATUSES:
                        raise ApprovalConflict(
                            f"request {request_id} is already {row['status']}"
                        )
                    if row["revision"] != submission.request_revision:
                        raise ApprovalConflict(
                            "request revision does not match the submission"
                        )
                    now = self._now_iso()
                    if _parse_instant(row["deadline_at"]) < _parse_instant(now):
                        self._execute(
                            "UPDATE permission_requests SET status = ?, resolved_at = ?"
                            " WHERE request_id = ?",
                            ("expired", now, request_id),
                        )
                        self._record_event(
                            request_id, None, None, "request_expired", {}
                        )
                        expired_request = request_id
                    else:
                        expected = {
                            item["item_id"]
                            for item in self._execute(
                                "SELECT item_id FROM permission_request_items"
                                " WHERE request_id = ?",
                                (request_id,),
                            ).fetchall()
                        }
                        decided = [d.item_id for d in submission.decisions]
                        if set(decided) != expected:
                            raise ValueError(
                                "submission must decide exactly the pending items"
                            )
                        persisted: list[str] = []
                        has_reject = False
                        for decision in submission.decisions:
                            grant_id = self._apply_decision(
                                request_id,
                                row["environment_id"],
                                decision,
                                submission,
                                actor,
                            )
                            if grant_id is not None:
                                persisted.append(grant_id)
                            if decision.choice == "reject":
                                has_reject = True
                        status = "blocked" if has_reject else "ready"
                        self._execute(
                            "UPDATE permission_requests SET status = ?, resolved_at = ?"
                            " WHERE request_id = ?",
                            (status, now, request_id),
                        )
                        resolution = ApprovalResolution(
                            approval_id=approval_id,
                            request_id=request_id,
                            request_status=status,  # type: ignore[arg-type]
                            execution_started=False,
                            persisted_grant_ids=tuple(persisted),
                            items=tuple(submission.decisions),
                        )
                        self._execute(
                            "INSERT INTO permission_submissions "
                            "(submission_id, request_id, request_revision,"
                            " payload_digest, response_json)"
                            " VALUES (?, ?, ?, ?, ?)",
                            (
                                submission.submission_id,
                                request_id,
                                submission.request_revision,
                                digest,
                                resolution.model_dump_json(),
                            ),
                        )
                        self._record_event(
                            request_id,
                            None,
                            None,
                            "request_resolved",
                            {"status": status, "grants": persisted},
                        )
            except (KeyError, ApprovalConflict, ApprovalExpired, ValueError):
                raise
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot submit approval decision: {exc}"
                ) from exc
        self._emit_pending()
        if expired_request is not None:
            raise ApprovalExpired(f"request {expired_request} passed its deadline")
        assert resolution is not None
        return resolution

    def _apply_decision(
        self,
        request_id: str,
        environment_id: str,
        decision: ItemDecision,
        submission: ApprovalSubmission,
        actor: str,
    ) -> str | None:
        item = self._execute(
            "SELECT * FROM permission_request_items WHERE item_id = ? AND request_id = ?",
            (decision.item_id, request_id),
        ).fetchone()
        if item is None:
            raise ValueError(f"unknown item id: {decision.item_id}")
        self._execute(
            "UPDATE permission_request_items SET decision = ? WHERE item_id = ?",
            (decision.choice, decision.item_id),
        )
        self._record_event(
            request_id,
            decision.item_id,
            None,
            "item_decision",
            {"choice": decision.choice},
        )
        if decision.choice != "allow_always":
            return None
        existing = self._execute(
            "SELECT * FROM permission_grants WHERE environment_id = ?"
            " AND resource_kind = ? AND resource_id = ? AND action = ?"
            " AND revoked_at IS NULL",
            (
                item["environment_id"],
                item["resource_kind"],
                item["resource_id"],
                item["action"],
            ),
        ).fetchone()
        if existing is not None:
            self._execute(
                "UPDATE permission_request_items SET matched_grant_id = ?"
                " WHERE item_id = ?",
                (existing["grant_id"], decision.item_id),
            )
            return existing["grant_id"]
        grant_id = f"grant-{uuid.uuid4().hex}"
        try:
            self._execute(
                "INSERT INTO permission_grants "
                "(grant_id, environment_id, resource_kind, resource_id, action,"
                " created_at, created_by, source_request_id, source_item_id,"
                " revoked_at, revoked_by, revision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    grant_id,
                    item["environment_id"],
                    item["resource_kind"],
                    item["resource_id"],
                    item["action"],
                    self._now_iso(),
                    actor,
                    request_id,
                    decision.item_id,
                    None,
                    None,
                    1,
                ),
            )
        except sqlite3.IntegrityError:
            raced = self._execute(
                "SELECT * FROM permission_grants WHERE environment_id = ?"
                " AND resource_kind = ? AND resource_id = ? AND action = ?"
                " AND revoked_at IS NULL",
                (
                    item["environment_id"],
                    item["resource_kind"],
                    item["resource_id"],
                    item["action"],
                ),
            ).fetchone()
            if raced is None:
                raise
            grant_id = raced["grant_id"]
        self._execute(
            "UPDATE permission_request_items SET matched_grant_id = ? WHERE item_id = ?",
            (grant_id, decision.item_id),
        )
        self._record_event(
            request_id,
            decision.item_id,
            grant_id,
            "grant_created",
            {"scope": [item["resource_kind"], item["resource_id"], item["action"]]},
        )
        return grant_id

    # -- execution tracking --------------------------------------------

    def claim_step(
        self,
        request_id: str,
        step_id: str,
        revision: int,
        max_attempts: int = MAX_ATTEMPTS_DEFAULT,
    ) -> str:
        """Linearize one step start; at most one holder runs a binding."""
        with self._lock:
            try:
                with self._conn:
                    row = self._execute(
                        "SELECT * FROM permission_requests WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"unknown request id: {request_id}")
                    if row["status"] not in ("ready", "running"):
                        raise ApprovalConflict(
                            f"request {request_id} is {row['status']}; "
                            "only ready requests may start steps"
                        )
                    if row["revision"] != revision:
                        raise ApprovalConflict("request revision changed; re-prepare")
                    step = self._execute(
                        "SELECT * FROM permission_steps WHERE step_id = ?"
                        " AND request_id = ?",
                        (step_id, request_id),
                    ).fetchone()
                    if step is None:
                        raise KeyError(f"unknown step id: {step_id}")
                    if step["status"] not in ("prepared", "no_effect_failure"):
                        raise ApprovalConflict(
                            f"step {step_id} is {step['status']}; cannot claim again"
                        )
                    if step["attempt_count"] >= max_attempts:
                        raise ApprovalConflict(
                            f"step {step_id} exhausted its attempt budget"
                        )
                    self._execute(
                        "UPDATE permission_steps SET status = ?, attempt_count = ?"
                        " WHERE step_id = ?",
                        ("running", step["attempt_count"] + 1, step_id),
                    )
                    if row["status"] == "ready":
                        self._execute(
                            "UPDATE permission_requests SET status = ?"
                            " WHERE request_id = ?",
                            ("running", request_id),
                        )
                    self._record_event(
                        request_id, step_id, None, "step_claimed", {}
                    )
                    binding_ref = step["binding_ref"]
            except (KeyError, ApprovalConflict):
                raise
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot claim step: {exc}"
                ) from exc
        self._emit_pending()
        return binding_ref

    def finish_step(
        self,
        request_id: str,
        step_id: str,
        observation: ExecutionObservation,
        max_attempts: int = MAX_ATTEMPTS_DEFAULT,
    ) -> str:
        """Record one step outcome and roll the request state forward."""
        from homemaster.permissions.models import TargetChanged

        with self._lock:
            try:
                with self._conn:
                    step = self._execute(
                        "SELECT * FROM permission_steps WHERE step_id = ?"
                        " AND request_id = ?",
                        (step_id, request_id),
                    ).fetchone()
                    if step is None:
                        raise KeyError(f"unknown step id: {step_id}")
                    if step["status"] != "running":
                        raise ApprovalConflict(
                            f"step {step_id} is {step['status']}; cannot finish"
                        )
                    if observation.binding_ref != step["binding_ref"]:
                        raise TargetChanged(
                            f"step {step_id} binding changed; approval no longer applies"
                        )
                    status = observation.outcome
                    if status == "succeeded":
                        step_status = "succeeded"
                    elif status == "unknown":
                        step_status = "unknown"
                    elif step["attempt_count"] >= max_attempts:
                        step_status = "failed"
                    else:
                        step_status = "no_effect_failure"
                    self._execute(
                        "UPDATE permission_steps SET status = ?,"
                        " backend_receipt_ref = ?, occupancy_state = ?"
                        " WHERE step_id = ?",
                        (
                            step_status,
                            observation.backend_code,
                            observation.current_area_id,
                            step_id,
                        ),
                    )
                    self._record_event(
                        request_id,
                        step_id,
                        None,
                        "step_finished",
                        {
                            "status": step_status,
                            "backend_code": observation.backend_code,
                        },
                    )
                    request_status = self._roll_up_request(request_id)
            except (KeyError, ApprovalConflict, TargetChanged):
                raise
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot finish step: {exc}"
                ) from exc
        self._emit_pending()
        return request_status

    def _roll_up_request(self, request_id: str) -> str:
        steps = self._execute(
            "SELECT status FROM permission_steps WHERE request_id = ?",
            (request_id,),
        ).fetchall()
        statuses = {row["status"] for row in steps}
        if statuses <= {"succeeded"}:
            rolled = "succeeded"
        elif "running" in statuses or "prepared" in statuses:
            rolled = "running"
        elif "no_effect_failure" in statuses:
            rolled = "running"
        elif "unknown" in statuses:
            rolled = "outcome_unknown"
        elif statuses <= {"succeeded", "failed"}:
            rolled = "failed" if "failed" in statuses else "succeeded"
        else:
            rolled = "failed"
        self._execute(
            "UPDATE permission_requests SET status = ?, resolved_at = ?"
            " WHERE request_id = ?",
            (rolled, self._now_iso(), request_id),
        )
        return rolled

    # -- revocation / cancellation / recovery --------------------------

    def list_grants(
        self,
        environment_id: str,
        *,
        resource_kind: str | None = None,
        status: str = "active",
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[GrantRecord, ...], str | None]:
        if status not in ("active", "revoked"):
            raise ValueError("grant status must be active or revoked")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be within 1..200")
        predicate = "revoked_at IS NULL" if status == "active" else "revoked_at IS NOT NULL"
        sql = (
            "SELECT * FROM permission_grants WHERE environment_id = ?"
            f" AND {predicate}"
        )
        params: list[Any] = [environment_id]
        if resource_kind is not None:
            sql += " AND resource_kind = ?"
            params.append(resource_kind)
        if cursor is not None:
            anchor = self._execute(
                "SELECT created_at FROM permission_grants WHERE grant_id = ?",
                (cursor,),
            ).fetchone()
            if anchor is None:
                raise KeyError(f"unknown grant cursor: {cursor}")
            sql += " AND (created_at, grant_id) > (?, ?)"
            params.extend([anchor["created_at"], cursor])
        sql += " ORDER BY created_at, grant_id LIMIT ?"
        params.append(limit + 1)
        with self._lock:
            rows = self._execute(sql, tuple(params)).fetchall()
        grants = tuple(self._grant_from_row(row) for row in rows[:limit])
        next_cursor = grants[-1].grant_id if len(rows) > limit else None
        return grants, next_cursor

    def list_all_grants(
        self,
        *,
        resource_kind: str | None = None,
        status: str = "active",
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[tuple[GrantRecord, ...], str | None]:
        """List grants across environments for the single-operator console.

        Prefer :meth:`list_grants` with an explicit environment whenever the
        caller knows it; this unfiltered view exists because the browser
        console is not told internal environment ids.
        """
        if status not in ("active", "revoked"):
            raise ValueError("grant status must be active or revoked")
        if not 1 <= limit <= 200:
            raise ValueError("limit must be within 1..200")
        predicate = "revoked_at IS NULL" if status == "active" else "revoked_at IS NOT NULL"
        sql = f"SELECT * FROM permission_grants WHERE {predicate}"
        params: list[Any] = []
        if resource_kind is not None:
            sql += " AND resource_kind = ?"
            params.append(resource_kind)
        if cursor is not None:
            anchor = self._execute(
                "SELECT created_at FROM permission_grants WHERE grant_id = ?",
                (cursor,),
            ).fetchone()
            if anchor is None:
                raise KeyError(f"unknown grant cursor: {cursor}")
            sql += " AND (created_at, grant_id) > (?, ?)"
            params.extend([anchor["created_at"], cursor])
        sql += " ORDER BY created_at, grant_id LIMIT ?"
        params.append(limit + 1)
        with self._lock:
            rows = self._execute(sql, tuple(params)).fetchall()
        grants = tuple(self._grant_from_row(row) for row in rows[:limit])
        next_cursor = grants[-1].grant_id if len(rows) > limit else None
        return grants, next_cursor

    def revoke(
        self, grant_id: str, submission_id: str, expected_revision: int, actor: str
    ) -> GrantRecord:
        """Revoke one grant with compare-and-swap on its revision.

        Retrying the same submission after a lost receipt returns the
        committed record; any other caller on an already revoked grant
        conflicts so stale UIs reload instead of assuming success.
        """
        _nonempty(submission_id, "submission id")
        with self._lock:
            try:
                with self._conn:
                    row = self._execute(
                        "SELECT * FROM permission_grants WHERE grant_id = ?",
                        (grant_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"unknown grant id: {grant_id}")
                    if row["revoked_at"] is not None:
                        if self._revoked_by(row["grant_id"], submission_id):
                            return self._grant_from_row(row)
                        raise ApprovalConflict("grant is already revoked")
                    if row["revision"] != expected_revision:
                        raise ApprovalConflict(
                            "grant revision changed; reload before revoking"
                        )
                    now = self._now_iso()
                    self._execute(
                        "UPDATE permission_grants SET revoked_at = ?, revoked_by = ?,"
                        " revision = ? WHERE grant_id = ?",
                        (now, actor, row["revision"] + 1, grant_id),
                    )
                    self._record_event(
                        row["source_request_id"],
                        row["source_item_id"],
                        grant_id,
                        "grant_revoked",
                        {"submission_id": submission_id},
                    )
            except (KeyError, ApprovalConflict):
                raise
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot revoke grant: {exc}"
                ) from exc
        self._emit_pending()
        with self._lock:
            row = self._execute(
                "SELECT * FROM permission_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            assert row is not None
            return self._grant_from_row(row)

    def _revoked_by(self, grant_id: str, submission_id: str) -> bool:
        row = self._execute(
            "SELECT payload_json FROM permission_events WHERE grant_id = ?"
            " AND event_type = 'grant_revoked'"
            " ORDER BY timestamp DESC, event_id DESC LIMIT 1",
            (grant_id,),
        ).fetchone()
        if row is None:
            return False
        return json.loads(row["payload_json"]).get("submission_id") == submission_id

    def cancel(self, request_id: str, reason: str) -> str:
        """Cancel a still-undecided request; decided ones keep their outcome.

        Only ``prepared``/``awaiting_approval`` requests transition to
        ``cancelled``. Anything already decided (ready, running, blocked,
        terminal, ...) is returned unchanged: cancelling never rolls back a
        committed decision or grant, and never kills an in-flight execution.
        """
        _nonempty(reason, "cancel reason")
        with self._lock:
            try:
                with self._conn:
                    row = self._execute(
                        "SELECT status FROM permission_requests WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                    if row is None:
                        raise KeyError(f"unknown request id: {request_id}")
                    if row["status"] not in ("prepared", "awaiting_approval"):
                        return row["status"]
                    self._execute(
                        "UPDATE permission_requests SET status = ?, resolved_at = ?"
                        " WHERE request_id = ?",
                        ("cancelled", self._now_iso(), request_id),
                    )
                    self._record_event(
                        request_id, None, None, "request_cancelled", {"reason": reason}
                    )
            except KeyError:
                raise
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot cancel request: {exc}"
                ) from exc
        self._emit_pending()
        return "cancelled"

    def recover(self) -> dict[str, int]:
        """Interrupt non-terminal requests; running steps become unknown.

        Call exactly once at application start. Never call while another
        live owner may hold pending requests: recovery assumes the previous
        owner is gone.
        """
        with self._lock:
            try:
                with self._conn:
                    pending = self._execute(
                        "SELECT request_id FROM permission_requests WHERE status"
                        " NOT IN ('succeeded', 'failed', 'outcome_unknown', 'blocked',"
                        " 'expired', 'cancelled', 'interrupted')"
                    ).fetchall()
                    running = self._execute(
                        "SELECT request_id, step_id FROM permission_steps"
                        " WHERE status = 'running'"
                    ).fetchall()
                    now = self._now_iso()
                    for row in pending:
                        self._execute(
                            "UPDATE permission_requests SET status = ?, resolved_at = ?"
                            " WHERE request_id = ?",
                            ("interrupted", now, row["request_id"]),
                        )
                        self._record_event(
                            row["request_id"], None, None, "request_interrupted", {}
                        )
                    for row in running:
                        self._execute(
                            "UPDATE permission_steps SET status = ? WHERE step_id = ?",
                            ("unknown", row["step_id"]),
                        )
                        self._record_event(
                            row["request_id"],
                            row["step_id"],
                            None,
                            "step_unknown_after_recover",
                            {},
                        )
            except sqlite3.Error as exc:
                raise PermissionStorageUnavailable(
                    f"cannot recover permission store: {exc}"
                ) from exc
        self._emit_pending()
        return {"interrupted_requests": len(pending), "unknown_steps": len(running)}

    # -- events --------------------------------------------------------

    def _record_event(
        self,
        request_id: str,
        step_id: str | None,
        grant_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        event = {
            "event_id": f"event-{uuid.uuid4().hex}",
            "request_id": request_id,
            "step_id": step_id,
            "grant_id": grant_id,
            "event_type": event_type,
            "timestamp": self._now_iso(),
            "payload": payload,
        }
        self._execute(
            "INSERT INTO permission_events "
            "(event_id, request_id, step_id, grant_id, event_type, timestamp,"
            " payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event["event_id"],
                request_id,
                step_id,
                grant_id,
                event_type,
                event["timestamp"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        self._staged.append(event)

    def _emit_pending(self) -> None:
        with self._lock:
            staged, self._staged = self._staged, []
        if staged:
            try:
                self._emit(staged)
            except Exception as exc:  # mirror must never reverse a commit
                log.warning("permission event mirror failed: %s", exc)

    def _emit(self, events: list[dict[str, Any]]) -> None:
        try:
            with open(self.mirror_path, "a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(
                        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
                    )
        except OSError as exc:
            log.warning("permission event mirror failed: %s", exc)


def _nonempty(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


__all__ = [
    "GrantRecord",
    "MAX_ATTEMPTS_DEFAULT",
    "PermissionStore",
    "SCHEMA_VERSION",
    "StoredItem",
    "StoredRequest",
    "StoredStep",
    "default_store_path",
    "resolve_store_path",
]
