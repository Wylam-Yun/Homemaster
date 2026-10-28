"""Unit tests for the V3.8 durable operation ledger.

Covers the reserve-before-side-effects contract: replay on identical
``operation_id`` + payload, conflict on divergent payload, ``processing`` on a
live lease, and retry after failure or lease expiry — all against an in-memory
``OperationRepository`` fake.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from mindmemos.infra.db.collections.operations import operation_point_id
from mindmemos.infra.db.models import OperationPoint, QdrantRecord
from mindmemos.services.operations import (
    OperationLedger,
    add_operation_result,
    canonical_request_hash,
    dreaming_operation_result,
    feedback_operation_result,
)


class _FakeOperationRepo:
    """In-memory stand-in for ``OperationRepository`` (upsert/get/patch)."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}
        self.upsert_calls = 0
        self.patch_calls = 0

    async def get(self, project_id: str, operation_id: str) -> QdrantRecord | None:
        key = operation_point_id(project_id, operation_id)
        row = self._store.get(key)
        return QdrantRecord(point_id=key, payload=dict(row)) if row is not None else None

    async def upsert(self, point: OperationPoint) -> None:
        self.upsert_calls += 1
        self._store[point.operation_point_id] = dict(point.payload)

    async def patch(self, project_id: str, operation_id: str, fields: dict) -> None:
        self.patch_calls += 1
        key = operation_point_id(project_id, operation_id)
        self._store.setdefault(key, {"operation_id": operation_id, "project_id": project_id})
        self._store[key].update(fields)


PROJECT = "proj-1"
OP = "op-1"
HASH_A = canonical_request_hash({"messages": [{"role": "user", "content": "hi"}]})
HASH_B = canonical_request_hash({"messages": [{"role": "user", "content": "different"}]})


def _ledger(repo: _FakeOperationRepo) -> OperationLedger:
    return OperationLedger(repo=repo)  # type: ignore[arg-type]


class TestCanonicalHash:
    def test_key_order_and_nulls_are_normalized(self) -> None:
        h1 = canonical_request_hash({"b": 2, "a": 1, "drop": None})
        h2 = canonical_request_hash({"a": 1, "b": 2})
        assert h1 == h2

    def test_nested_nulls_dropped(self) -> None:
        h1 = canonical_request_hash({"outer": {"keep": 1, "drop": None}})
        h2 = canonical_request_hash({"outer": {"keep": 1}})
        assert h1 == h2

    def test_distinct_payloads_differ(self) -> None:
        assert HASH_A != HASH_B


@pytest.mark.asyncio
class TestReserve:
    async def test_first_reservation_proceeds(self) -> None:
        repo = _FakeOperationRepo()
        outcome = await _ledger(repo).reserve(PROJECT, OP, kind="add", request_hash=HASH_A, ref_id="ar-1")
        assert outcome.decision == "proceed"
        assert outcome.record.status == "processing"
        assert outcome.record.attempts == 1
        assert outcome.record.ref_id == "ar-1"
        assert outcome.record.lease_expires_at is not None

    async def test_identical_resubmit_while_processing_reports_processing(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        assert second.decision == "processing"

    async def test_completed_operation_replays_stored_result(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        result = add_operation_result(OP, "ar-1", memory_ids=["m1"])
        await ledger.complete(PROJECT, OP, status="completed", result=result)

        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        assert second.decision == "replay"
        assert second.result == result

    async def test_divergent_payload_conflicts(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_B)
        assert second.decision == "conflict"

    async def test_conflict_wins_over_replay(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        await ledger.complete(PROJECT, OP, status="completed", result={"ok": True})
        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_B)
        assert second.decision == "conflict"

    async def test_failed_operation_can_be_retried(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        await ledger.fail(PROJECT, OP, error="boom")

        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        assert second.decision == "proceed"
        assert second.record.attempts == 2
        assert second.record.status == "processing"

    async def test_failed_retry_still_conflicts_on_divergent_payload(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        await ledger.fail(PROJECT, OP, error="boom")
        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_B)
        assert second.decision == "conflict"

    async def test_expired_lease_is_reacquired(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        # Age the lease into the past without settling.
        stale = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        await repo.patch(PROJECT, OP, {"lease_expires_at": stale})

        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        assert second.decision == "proceed"
        assert second.record.attempts == 2

    async def test_operations_are_project_scoped(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        other = await ledger.reserve("proj-2", OP, kind="add", request_hash=HASH_A)
        assert other.decision == "proceed"  # different project → independent row


@pytest.mark.asyncio
class TestSettlement:
    async def test_complete_clears_lease_by_default(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        await ledger.complete(PROJECT, OP, status="completed", result={"ok": True})
        row = await ledger.get(PROJECT, OP)
        assert row is not None
        assert row.status == "completed"
        assert row.lease_expires_at is None
        assert row.reservation_token is None

    async def test_complete_can_retain_lease_for_async_acceptance(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        await ledger.complete(PROJECT, OP, status="accepted", clear_lease=False)
        row = await ledger.get(PROJECT, OP)
        assert row is not None
        assert row.status == "accepted"
        assert row.lease_expires_at is not None  # lease held until worker settles
        # A duplicate submission is still fenced while the async op runs.
        second = await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        assert second.decision == "processing"

    async def test_fail_truncates_long_errors(self) -> None:
        repo = _FakeOperationRepo()
        ledger = _ledger(repo)
        await ledger.reserve(PROJECT, OP, kind="add", request_hash=HASH_A)
        await ledger.fail(PROJECT, OP, error="x" * 5000)
        row = await ledger.get(PROJECT, OP)
        assert row is not None
        assert row.status == "failed"
        assert row.error is not None and len(row.error) <= 2000


class TestResultShapes:
    def test_add_result_shape(self) -> None:
        result = add_operation_result("op", "ar-1", memory_ids=["m1"], replayed=True)
        assert result == {
            "operation_id": "op",
            "add_record_id": "ar-1",
            "memory_ids": ["m1"],
            "memories": [],
            "status": "completed",
            "replayed": True,
        }

    def test_feedback_result_shape(self) -> None:
        result = feedback_operation_result("op", "fb-1", actions=[{"action": "update"}])
        assert result["feedback_record_id"] == "fb-1"
        assert result["actions"] == [{"action": "update"}]
        assert result["replayed"] is False

    def test_dreaming_result_shape(self) -> None:
        result = dreaming_operation_result("op", "dr-1", produced_memory_ids=["m2"], status="no_action")
        assert result["dreaming_record_id"] == "dr-1"
        assert result["produced_memory_ids"] == ["m2"]
        assert result["status"] == "no_action"
