"""Tests for the durable outbox worker pool (V3.8 §4.2/§6)."""

from __future__ import annotations

import pytest

from homemaster.agent.messages import ContentBlock, UserMessage
from homemaster.memory.memory_jobs import default_executors
from homemaster.memory.memory_work_store import SQLiteMemoryWorkStore
from homemaster.memory.memory_workers import MemoryWorkerPool
from homemaster.memory.mindmemos_client import (
    AddReceipt,
    DreamingReceipt,
    OperationNotFoundError,
    OutcomeUnknownError,
)
from homemaster.memory.session_trace_store import SessionTraceStore


class FakeClient:
    def __init__(self):
        self.add_calls: list[dict] = []
        self.statuses: dict[str, dict] = {}
        self.fail_next = None
        self._health = True

    async def health(self):
        return self._health

    async def aclose(self):
        return None

    async def add_turn(self, *, operation_id, session_id, turn_id, dialogue, metadata=None):
        self.add_calls.append({"operation_id": operation_id, "dialogue": dialogue})
        if self.fail_next == "unknown":
            self.fail_next = None
            raise OutcomeUnknownError("lost receipt")
        return AddReceipt(
            operation_id=operation_id,
            add_record_id=f"ar-{operation_id}",
            memory_ids=("m1", "m2"),
            status="completed",
            request_id="req-1",
        )

    async def operation_status(self, operation_id):
        if operation_id not in self.statuses:
            raise OperationNotFoundError(operation_id)
        s = self.statuses[operation_id]
        from homemaster.memory.mindmemos_client import OperationStatus

        return OperationStatus(
            operation_id=operation_id,
            kind="add",
            status=s["status"],
            result=s.get("result"),
            error=s.get("error"),
        )

    async def dreaming(self, **kw):
        raise NotImplementedError

    async def feedback_implicit(self, **kw):
        raise NotImplementedError

    async def submit_evolution_batch(self, **kw):
        raise NotImplementedError


class FakeVerifier:
    def __init__(self, existing: set[str] | None = None, records: set[str] | None = None):
        self.existing = existing if existing is not None else {"m1", "m2"}
        self.records = records if records is not None else set()
        self.dynamic_records = True

    async def memory_exists(self, mid):
        return mid in self.existing

    async def add_record_exists(self, rid):
        return True if self.dynamic_records else rid in self.records

    async def memory_ids_exist(self, ids):
        return {i: i in self.existing for i in ids}

    async def close(self):
        return None


@pytest.fixture()
def stack(tmp_path):
    store = SQLiteMemoryWorkStore(tmp_path / "work.db")
    store.open()
    trace = SessionTraceStore(store)
    client = FakeClient()
    verifier = FakeVerifier()
    executors = default_executors(store=store, trace=trace, client=client, verifier=verifier)
    pool = MemoryWorkerPool(
        store=store, client=client, executors=executors,
        audit_path=tmp_path / "audit.jsonl", owner_id="test-worker",
    )
    yield store, trace, client, verifier, pool
    store.close()


def _commit_turn(trace, session_id="s1", turn_index=1, run_id="r1"):
    trace.begin_turn(session_id, turn_index, run_id, message_start_index=0)
    return trace.commit_terminal(
        session_id,
        f"turn-{turn_index}",
        status="completed",
        messages=[UserMessage(content=[ContentBlock(type="text", text="hello")])],
    )


class TestAddWorker:
    @pytest.mark.asyncio
    async def test_terminal_turn_adds_once(self, stack):
        store, trace, client, verifier, pool = stack
        commit = _commit_turn(trace)
        progressed = await pool.run_once()
        assert progressed
        await pool.stop()
        job = store.job(commit.add_job_id)
        assert job.status == "completed"
        assert job.result["add_record_id"] == "ar-turnadd:s1:turn-1"
        assert job.result["raw_verified"] is True
        assert client.add_calls[0]["operation_id"] == "turnadd:s1:turn-1"

    @pytest.mark.asyncio
    async def test_second_worker_claim_same_job_no_duplicate(self, stack):
        store, trace, client, verifier, pool = stack
        _commit_turn(trace)
        await pool.run_once()
        await pool.stop()
        # A second pool on the same store must NOT re-execute the finished job.
        pool2 = MemoryWorkerPool(
            store=store, client=client,
            executors=default_executors(store=store, trace=trace, client=client, verifier=verifier),
            owner_id="worker-2",
        )
        progressed = await pool2.run_once()
        assert not progressed
        assert len(client.add_calls) == 1

    @pytest.mark.asyncio
    async def test_outcome_unknown_reconciles_before_retry(self, stack):
        store, trace, client, verifier, pool = stack
        client.fail_next = "unknown"
        commit = _commit_turn(trace)
        await pool.run_once()  # add fired, receipt lost → unknown
        job = store.job(commit.add_job_id)
        assert job.status == "outcome_unknown"
        # Server later reports the operation actually completed.
        client.statuses["turnadd:s1:turn-1"] = {
            "status": "completed",
            "result": {"add_record_id": "ar-1", "memory_ids": ["m1"]},
        }
        await pool.run_once()
        job = store.job(commit.add_job_id)
        assert job.status == "completed"
        assert job.result["reconciled"] is True
        assert len(client.add_calls) == 1  # never resubmitted

    @pytest.mark.asyncio
    async def test_missing_readback_retries(self, stack):
        store, trace, client, verifier, pool = stack
        verifier.existing = {"m1"}  # m2 missing from raw storage
        commit = _commit_turn(trace)
        await pool.run_once()
        job = store.job(commit.add_job_id)
        assert job.status == "accepted"  # retry queued
        assert "m2" in (job.last_error or "")
        verifier.existing = {"m1", "m2"}
        # Pull the retry's not_before into the past instead of sleeping 15s.
        store._db.execute("UPDATE outbox_jobs SET not_before = 0")
        await pool.run_once()
        assert store.job(commit.add_job_id).status == "completed"

    @pytest.mark.asyncio
    async def test_crash_recovery_reclaims_and_completes(self, tmp_path):
        db = tmp_path / "work.db"
        store = SQLiteMemoryWorkStore(db)
        store.open()
        trace = SessionTraceStore(store)
        _commit_turn(trace)
        # Simulate a worker that claimed then died (lease left dangling at 0s).
        claimed = store.claim_jobs("dead-worker", lease_seconds=0.0)
        assert claimed
        client = FakeClient()
        verifier = FakeVerifier()
        pool = MemoryWorkerPool(
            store=store, client=client,
            executors=default_executors(store=store, trace=trace, client=client, verifier=verifier),
            owner_id="new-worker",
        )
        await pool.start()
        job = store.job(claimed[0].job_id)
        # audit requeued the expired lease; run_once executes it
        assert job.status in {"accepted", "processing"}
        await pool.run_once()
        assert store.job(claimed[0].job_id).status == "completed"
        await pool.stop()
        store.close()

    @pytest.mark.asyncio
    async def test_interrupted_turn_recovers_intent(self, stack):
        store, trace, client, verifier, pool = stack
        trace.begin_turn("s1", 5, "r5", message_start_index=0)
        # crash: no terminal commit — the turn stays 'active'
        audit = store.audit_and_recover(owner="w")
        assert audit.active_turns_marked_interrupted == ("s1/turn-5",)
        job = store.job_by_operation_key("turnadd:s1:turn-5")
        assert job is not None
        await pool.run_once()
        job = store.job_by_operation_key("turnadd:s1:turn-5")
        assert job.status == "completed"
        # empty dialogue → placeholder message, still one add call
        assert client.add_calls[0]["dialogue"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_audit_jsonl_written(self, stack, tmp_path):
        store, trace, client, verifier, pool = stack
        _commit_turn(trace)
        await pool.run_once()
        audit = tmp_path / "audit.jsonl"
        lines = audit.read_text().strip().splitlines()
        events = [__import__("json").loads(line)["event"] for line in lines]
        assert "job.started" in events and "job.settled" in events
        settled = [
            __import__("json").loads(line)
            for line in lines
            if __import__("json").loads(line)["event"] == "job.settled"
        ][0]
        assert settled["payload"]["outcome_status"] == "completed"
        assert settled["payload"]["add_record_id"] == "ar-turnadd:s1:turn-1"


class _DreamClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.dreaming_calls: list[dict] = []

    async def dreaming(self, *, operation_id, seed_add_record_ids, session_id=None):
        self.dreaming_calls.append(
            {
                "operation_id": operation_id,
                "seed_add_record_ids": list(seed_add_record_ids),
                "session_id": session_id,
            }
        )
        return DreamingReceipt(
            operation_id=operation_id,
            dreaming_record_id="dr-1",
            produced_memory_ids=("dm-1",),
            actions=(),
            status="completed",
            request_id=None,
        )


class TestDreamingWorker:
    @pytest.mark.asyncio
    async def test_no_seeds_is_no_action_not_error(self, tmp_path):
        store = SQLiteMemoryWorkStore(tmp_path / "w.db")
        store.open()
        archive = store.create_archive(
            "s1", turn_range_start=0, turn_range_end=1, max_event_seq=0,
            add_operation_ids=[], intent_payload={},
        )
        client = FakeClient()
        verifier = FakeVerifier()
        trace = SessionTraceStore(store)
        pool = MemoryWorkerPool(
            store=store, client=client,
            executors=default_executors(store=store, trace=trace, client=client, verifier=verifier),
            owner_id="w",
        )
        await pool.run_once()
        job = store.job(archive.dreaming_job_id)
        assert job.status == "no_action"
        assert job.result["reason"] == "no_seeds"
        store.close()

    def _stack(self, tmp_path):
        store = SQLiteMemoryWorkStore(tmp_path / "w.db")
        store.open()
        client = _DreamClient()
        verifier = FakeVerifier()
        trace = SessionTraceStore(store)
        pool = MemoryWorkerPool(
            store=store, client=client,
            executors=default_executors(store=store, trace=trace, client=client, verifier=verifier),
            owner_id="w",
        )
        return store, client, verifier, pool

    @pytest.mark.asyncio
    async def test_seed_still_processing_defers_then_completes(self, tmp_path):
        """A seed Add still extracting server-side is a wait, not a failure:
        the dreaming job must not burn its attempt budget while it waits."""
        store, client, verifier, pool = self._stack(tmp_path)
        archive = store.create_archive(
            "s1", turn_range_start=1, turn_range_end=1, max_event_seq=41,
            add_operation_ids=["turnadd:s1:turn-1"], intent_payload={},
        )
        job_id = archive.dreaming_job_id
        client.statuses["turnadd:s1:turn-1"] = {"status": "processing"}
        await pool.run_once()
        job = store.job(job_id)
        assert job.status == "accepted"
        assert job.attempts == 0
        assert "still processing" in (job.last_error or "")
        assert client.dreaming_calls == []
        # The seed finishes upstream; the deferred job then converges.
        client.statuses["turnadd:s1:turn-1"] = {
            "status": "completed",
            "result": {"add_record_id": "ar-1"},
        }
        verifier.existing.add("dm-1")
        store._db.execute("UPDATE outbox_jobs SET not_before = 0")
        await pool.run_once()
        job = store.job(job_id)
        assert job.status == "completed"
        assert client.dreaming_calls == [
            {
                "operation_id": "dreaming:archive:s1:41",
                "seed_add_record_ids": ["ar-1"],
                "session_id": "s1",
            }
        ]
        assert job.result["dreaming_record_id"] == "dr-1"
        assert job.result["raw_verified"] is True
        store.close()

    @pytest.mark.asyncio
    async def test_dependency_wait_budget_exceeded_fails(self, tmp_path):
        """A dependency stuck past the wait budget must surface as failed,
        not hang the queue forever."""
        store, client, verifier, pool = self._stack(tmp_path)
        archive = store.create_archive(
            "s1", turn_range_start=1, turn_range_end=1, max_event_seq=41,
            add_operation_ids=["turnadd:s1:turn-1"], intent_payload={},
        )
        store._db.execute(
            "UPDATE outbox_jobs SET created_at = created_at - 8000"
        )
        client.statuses["turnadd:s1:turn-1"] = {"status": "processing"}
        await pool.run_once()
        job = store.job(archive.dreaming_job_id)
        assert job.status == "failed"
        assert "wait budget" in (job.last_error or "")
        store.close()

    @pytest.mark.asyncio
    async def test_turn_add_reconcile_still_processing_defers(self, stack):
        """Reconciling an outcome_unknown add whose server op is still
        running defers the job instead of burning retry attempts."""
        store, trace, client, verifier, pool = stack
        client.fail_next = "unknown"
        commit = _commit_turn(trace)
        await pool.run_once()
        job = store.job(commit.add_job_id)
        assert job.status == "outcome_unknown"
        client.statuses["turnadd:s1:turn-1"] = {"status": "processing"}
        await pool.run_once()
        job = store.job(commit.add_job_id)
        # deferred keeps outcome_unknown so the retry reconciles again
        # instead of blindly resubmitting the operation.
        assert job.status == "outcome_unknown"
        assert job.attempts == 0
        assert "still processing" in (job.last_error or "")
        client.statuses["turnadd:s1:turn-1"] = {
            "status": "completed",
            "result": {"add_record_id": "ar-1", "memory_ids": ["m1"]},
        }
        store._db.execute("UPDATE outbox_jobs SET not_before = 0")
        await pool.run_once()
        job = store.job(commit.add_job_id)
        assert job.status == "completed"
        assert job.result["reconciled"] is True
        assert len(client.add_calls) == 1  # never resubmitted


class _SkillEvoClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.trace_calls: list[dict] = []
        self.batch_calls: list[dict] = []

    async def submit_skill_trace(self, *, cloud_skill_id, trace):
        from homemaster.memory.mindmemos_client import SkillTraceReceipt

        self.trace_calls.append({"cloud_skill_id": cloud_skill_id, **trace})
        return SkillTraceReceipt(
            invocation_id=trace["invocation_id"],
            cloud_skill_id=cloud_skill_id,
            unconsumed_count=1,
        )

    async def submit_evolution_batch(self, *, cloud_skill_id, batch_id, trace_ids):
        from homemaster.memory.mindmemos_client import EvolutionBatchReceipt

        self.batch_calls.append(
            {"cloud_skill_id": cloud_skill_id, "batch_id": batch_id, "trace_ids": list(trace_ids)}
        )
        return EvolutionBatchReceipt(
            batch_id=batch_id, draft_version_id="draft-1", status="completed", reason=None
        )


class TestSkillEvolutionWorker:
    """The executor must seal each trace upstream before submitting the batch —
    the server 409s a batch that references unknown trace ids."""

    @pytest.mark.asyncio
    async def test_evolution_submits_traces_then_batch(self, tmp_path):
        from homemaster.memory.memory_jobs import SkillEvolutionExecutor
        from homemaster.memory.memory_work_store import SkillInvocationStep

        store = SQLiteMemoryWorkStore(tmp_path / "w.db")
        store.open()
        for i in range(8):
            inv_id = f"inv-{i}"
            store.open_skill_invocation(
                inv_id, session_id="s1", turn_id=f"turn-{i}",
                skill_id="sk", cloud_skill_id="cloud-1",
                skill_version_id="v1", skill_content_hash="h1",
            )
            store.add_invocation_step(
                inv_id,
                SkillInvocationStep(
                    invocation_id=inv_id, step_seq=0, tool_call_id=f"c{i}",
                    tool_name="mindmemos_search", arguments={"q": "x"},
                    tool_result_tail="1 hit", tool_result_truncated=False, status="ok",
                ),
            )
            store.seal_skill_invocation(
                inv_id, task_input=f"task {i}", agent_completion=f"done {i}",
                outcome="success", score=0.8,
            )
        client = _SkillEvoClient()
        executor = SkillEvolutionExecutor(store=store, client=client)
        job = store.enqueue_job(
            "skill_evolution", "skillevolve:b1",
            {"batch_id": "b1", "cloud_skill_id": "cloud-1",
             "trace_ids": [f"inv-{i}" for i in range(8)]},
            session_id=None,
        )
        outcome = await executor.execute(job)
        assert outcome.status == "completed"
        assert outcome.result["draft_version_id"] == "draft-1"
        # All 8 sealed traces submitted upstream BEFORE the batch call.
        assert len(client.trace_calls) == 8
        assert client.trace_calls[0]["skill_version_id"] == "v1"
        assert client.trace_calls[0]["skill_content_hash"] == "h1"
        assert client.trace_calls[0]["tool_steps"][0]["tool_name"] == "mindmemos_search"
        assert client.trace_calls[0]["outcome"] == "success"
        assert client.trace_calls[0]["score"] == 0.8
        assert client.batch_calls == [
            {"cloud_skill_id": "cloud-1", "batch_id": "b1",
             "trace_ids": [f"inv-{i}" for i in range(8)]}
        ]
        store.close()

    @pytest.mark.asyncio
    async def test_evolution_rejects_unsealed_trace(self, tmp_path):
        from homemaster.memory.memory_jobs import SkillEvolutionExecutor

        store = SQLiteMemoryWorkStore(tmp_path / "w.db")
        store.open()
        store.open_skill_invocation(
            "inv-open", session_id="s1", turn_id="turn-0",
            skill_id="sk", cloud_skill_id="cloud-1",
            skill_version_id="v1", skill_content_hash="h1",
        )
        client = _SkillEvoClient()
        executor = SkillEvolutionExecutor(store=store, client=client)
        job = store.enqueue_job(
            "skill_evolution", "skillevolve:b2",
            {"batch_id": "b2", "cloud_skill_id": "cloud-1",
             "trace_ids": ["inv-open"] + [f"missing-{i}" for i in range(7)]},
            session_id=None,
        )
        outcome = await executor.execute(job)
        assert outcome.status == "failed"
        assert client.trace_calls == []
        assert client.batch_calls == []
        store.close()
