"""Durable operation ledger for the V3.8 native memory lifecycle.

One ledger row per client-supplied ``operation_id`` (scoped per project). Rows
are persisted in the dedicated ``operations`` Qdrant collection via
:class:`OperationRepository` and drive the idempotency contract of the lifecycle
endpoints:

- same ``operation_id`` + same canonical request payload → the stored result is
  replayed verbatim (``replayed: true`` in response ``data``);
- same ``operation_id`` + different payload → HTTP 409 ``conflict``;
- ``processing``/``accepted`` with a live lease → HTTP 202 ``processing``;
- ``failed`` or an expired lease → the reservation is re-acquired and the
  operation may be retried through the same durable record.

Qdrant has no multi-point transactions, so "reserve before side effects" is
implemented as an upsert + re-read handshake on a deterministic point id: the
writer stamps a fresh ``reservation_token`` on the row it writes and treats the
reservation as lost if a different token wins the re-read.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ..infra.db import DatabaseClients, QdrantRecord, resolve_database_clients
from ..infra.db.collections.operations import (
    OperationRepository,
    dumps_canonical,
    operation_point_id,
)
from ..infra.db.models import OperationPoint
from ..logging import get_logger

logger = get_logger(__name__)

OperationStatus = Literal["accepted", "processing", "completed", "no_action", "failed"]
"""Ledger lifecycle states (``accepted`` = queued for an async worker)."""

OperationKind = Literal["add", "feedback", "dreaming", "skill_evolution", "update", "delete"]
"""Business operation kinds tracked by the ledger."""

DEFAULT_LEASE_TTL_SECONDS = 300.0
"""How long a ``processing``/``accepted`` reservation is considered alive."""


class OperationRecord(BaseModel):
    """One durable operation-ledger row."""

    model_config = ConfigDict(extra="ignore")

    operation_id: str
    project_id: str
    kind: str
    request_hash: str
    status: str = "processing"
    result: dict[str, Any] | None = None
    error: str | None = None
    ref_id: str | None = None
    """Linked business record id (add_record_id / feedback_record_id / ...)."""
    attempts: int = 0
    reservation_token: str | None = None
    lease_expires_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_record(cls, record: QdrantRecord) -> OperationRecord | None:
        """Hydrate one ledger row from a Qdrant record payload."""

        payload = dict(record.payload or {})
        operation_id = str(payload.get("operation_id") or "")
        project_id = str(payload.get("project_id") or "")
        if not operation_id or not project_id:
            return None
        payload["operation_id"] = operation_id
        payload["project_id"] = project_id
        return cls.model_validate(payload)


ReserveDecision = Literal["proceed", "replay", "conflict", "processing"]
"""Reservation outcome for one ``operation_id`` submission."""


@dataclass(slots=True)
class ReserveOutcome:
    """Result of :meth:`OperationLedger.reserve`."""

    decision: ReserveDecision
    record: OperationRecord
    result: dict[str, Any] | None = None
    """Stored ``result_json`` when ``decision == 'replay'``."""


def canonical_request_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical request body (sorted JSON, ``None`` dropped).

    ``None``-valued keys are dropped recursively so that an omitted optional
    field and an explicit ``null`` produce the same fingerprint.
    """

    cleaned = _drop_nulls(payload)
    return hashlib.sha256(dumps_canonical(cleaned).encode("utf-8")).hexdigest()


def _drop_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_nulls(v) for v in value]
    return value


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class OperationLedger:
    """Project-scoped durable operation ledger.

    ``clients``/``repo`` injection keeps the ledger testable with in-memory
    Qdrant; production resolves the shared event-loop clients lazily.
    """

    def __init__(
        self,
        *,
        clients: DatabaseClients | None = None,
        repo: OperationRepository | None = None,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        self._repo = repo
        self._clients = resolve_database_clients(clients) if repo is None else None
        self._lease_ttl = lease_ttl_seconds

    def _repository(self) -> OperationRepository:
        if self._repo is not None:
            return self._repo
        return self._clients.operations

    # -- read -----------------------------------------------------------------

    async def get(self, project_id: str, operation_id: str) -> OperationRecord | None:
        """Return the stored operation row, or ``None`` when unknown."""

        record = await self._repository().get(project_id, operation_id)
        return OperationRecord.from_record(record) if record is not None else None

    # -- reservation -----------------------------------------------------------

    async def reserve(
        self,
        project_id: str,
        operation_id: str,
        *,
        kind: str,
        request_hash: str,
        ref_id: str | None = None,
    ) -> ReserveOutcome:
        """Reserve ``operation_id`` for execution or short-circuit.

        Returns a :class:`ReserveOutcome` whose ``decision`` tells the caller to
        run the operation (``proceed``), replay the stored result (``replay``),
        reject as ``conflict`` (different payload) or report ``processing`` (a
        live reservation is held by another attempt).
        """

        repo = self._repository()
        now = datetime.now(UTC)
        existing = await self.get(project_id, operation_id)
        if existing is not None:
            outcome = self._evaluate_existing(existing, request_hash=request_hash, now=now)
            if outcome is not None:
                return outcome
            # failed or expired reservation → re-acquire under a new token.
            token = uuid4().hex
            await repo.patch(
                project_id,
                operation_id,
                {
                    "status": "processing",
                    "error": None,
                    "attempts": int(existing.attempts or 0) + 1,
                    "reservation_token": token,
                    "lease_expires_at": (now + timedelta(seconds=self._lease_ttl)).isoformat(),
                    "updated_at": now.isoformat(),
                },
            )
            winner = await self.get(project_id, operation_id)
            if winner is None or winner.reservation_token != token:
                # Lost a reservation race; surface the winning row's state.
                winner = winner or existing
                outcome = self._evaluate_existing(winner, request_hash=request_hash, now=now)
                if outcome is not None:
                    return outcome
                return ReserveOutcome(
                    decision="processing",
                    record=winner,
                )
            winner.ref_id = winner.ref_id or existing.ref_id or ref_id
            return ReserveOutcome(decision="proceed", record=winner)

        # First reservation: upsert a fresh processing row, then re-read to
        # detect a lost reservation race against a concurrent writer.
        token = uuid4().hex
        row = OperationRecord(
            operation_id=operation_id,
            project_id=project_id,
            kind=kind,
            request_hash=request_hash,
            status="processing",
            ref_id=ref_id,
            attempts=1,
            reservation_token=token,
            lease_expires_at=(now + timedelta(seconds=self._lease_ttl)).isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        await repo.upsert(
            OperationPoint(
                operation_point_id=operation_point_id(project_id, operation_id),
                payload=row.model_dump(mode="json"),
            )
        )
        winner = await self.get(project_id, operation_id)
        if winner is not None and winner.reservation_token != token:
            outcome = self._evaluate_existing(winner, request_hash=request_hash, now=now)
            if outcome is not None:
                return outcome
            return ReserveOutcome(decision="processing", record=winner)
        return ReserveOutcome(decision="proceed", record=winner or row)

    def _evaluate_existing(
        self,
        record: OperationRecord,
        *,
        request_hash: str,
        now: datetime,
    ) -> ReserveOutcome | None:
        """Short-circuit decision for an existing row, or ``None`` to re-reserve."""

        if record.request_hash != request_hash:
            return ReserveOutcome(decision="conflict", record=record)
        if record.status in ("completed", "no_action"):
            return ReserveOutcome(decision="replay", record=record, result=record.result)
        if record.status == "failed":
            return None  # eligible for retry through the same reservation path
        lease_expiry = _parse_dt(record.lease_expires_at)
        if lease_expiry is None or lease_expiry <= now:
            return None  # abandoned reservation → retry
        return ReserveOutcome(decision="processing", record=record)

    # -- settlement ------------------------------------------------------------

    async def complete(
        self,
        project_id: str,
        operation_id: str,
        *,
        status: str = "completed",
        result: dict[str, Any] | None = None,
        clear_lease: bool = True,
    ) -> None:
        """Mark one operation finished with its durable business result."""

        fields: dict[str, Any] = {
            "status": status,
            "result": result,
            "error": None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if clear_lease:
            fields["lease_expires_at"] = None
            fields["reservation_token"] = None
        await self._repository().patch(project_id, operation_id, fields)

    async def fail(self, project_id: str, operation_id: str, *, error: str) -> None:
        """Mark one operation failed; a matching-payload retry may re-reserve."""

        await self._repository().patch(
            project_id,
            operation_id,
            {
                "status": "failed",
                "error": error[:2000],
                "updated_at": datetime.now(UTC).isoformat(),
                "lease_expires_at": None,
                "reservation_token": None,
            },
        )


def get_operation_ledger() -> OperationLedger:
    """Process-wide operation ledger (resolves event-loop DB clients lazily)."""

    return OperationLedger()


# ---------------------------------------------------------------------------
# Canonical ``result_json`` / HTTP ``data`` shapes (shared by API + workers)
# ---------------------------------------------------------------------------


def add_operation_result(
    operation_id: str,
    add_record_id: str,
    *,
    memory_ids: list[str] | None = None,
    memories: list[dict[str, Any]] | None = None,
    status: str = "completed",
    replayed: bool = False,
) -> dict[str, Any]:
    """Canonical ``data``/``result_json`` shape for ``add`` operations."""

    return {
        "operation_id": operation_id,
        "add_record_id": add_record_id,
        "memory_ids": list(memory_ids or []),
        "memories": list(memories or []),
        "status": status,
        "replayed": replayed,
    }


def feedback_operation_result(
    operation_id: str,
    feedback_record_id: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    status: str = "completed",
    replayed: bool = False,
) -> dict[str, Any]:
    """Canonical ``data``/``result_json`` shape for ``feedback`` operations."""

    return {
        "operation_id": operation_id,
        "feedback_record_id": feedback_record_id,
        "actions": list(actions or []),
        "status": status,
        "replayed": replayed,
    }


def dreaming_operation_result(
    operation_id: str,
    dreaming_record_id: str,
    *,
    produced_memory_ids: list[str] | None = None,
    actions: list[dict[str, Any]] | None = None,
    status: str = "completed",
    replayed: bool = False,
) -> dict[str, Any]:
    """Canonical ``data``/``result_json`` shape for ``dreaming`` operations."""

    return {
        "operation_id": operation_id,
        "dreaming_record_id": dreaming_record_id,
        "produced_memory_ids": list(produced_memory_ids or []),
        "actions": list(actions or []),
        "status": status,
        "replayed": replayed,
    }
