"""Contract tests for SQLiteMemoryWorkStore (V3.8 Task 4)."""

from __future__ import annotations

import pytest

from homemaster.memory.memory_work_store import (
    JOB_TURN_ADD,
    MemoryWorkStoreError,
    SQLiteMemoryWorkStore,
    TerminalConflictError,
    TurnNotFoundError,
)


@pytest.fixture()
def store(tmp_path):
    store = SQLiteMemoryWorkStore(tmp_path / "work.db")
    store.open()
    yield store
    store.close()


def _commit(store, session_id="s1", turn_id="turn-1", status="completed", events=None, op=None):
    store.begin_turn(session_id, int(turn_id.split("-")[1]), "run-1", message_start_index=0)
    return store.commit_turn_terminal(
        session_id,
        turn_id,
        status=status,
        events=events or [],
        operation_id=op or f"turnadd:{session_id}:{turn_id}",
        intent_payload={"terminal_reason": status},
    )


class TestTurnLifecycle:
    def test_begin_turn_idempotent_same_run(self, store):
        a = store.begin_turn("s1", 3, "run-1", message_start_index=5)
        b = store.begin_turn("s1", 3, "run-1", message_start_index=99)
        assert a.turn_id == b.turn_id == "turn-3"
        assert b.message_start_index == 5  # original wins

    def test_begin_turn_conflicting_run_rejected(self, store):
        store.begin_turn("s1", 3, "run-1", message_start_index=0)
        with pytest.raises(MemoryWorkStoreError):
            store.begin_turn("s1", 3, "run-2", message_start_index=0)

    def test_single_terminal_commit(self, store):
        result = _commit(
            store,
            events=[{"kind": "message", "payload": {"role": "user", "content": "hi"}}],
        )
        assert result.turn.status == "completed"
        assert result.add_job.kind == JOB_TURN_ADD
        assert result.add_job.operation_key == "turnadd:s1:turn-1"
        assert result.add_job.status == "accepted"
        assert result.turn.add_job_id == result.add_job.job_id

    def test_duplicate_identical_terminal_is_noop(self, store):
        first = _commit(store, status="completed")
        second = _commit(store, status="completed")
        assert first.add_job.job_id == second.add_job.job_id

    def test_conflicting_terminal_rejected(self, store):
        _commit(store, status="completed")
        with pytest.raises(TerminalConflictError):
            _commit(store, status="failed")

    def test_terminal_on_missing_turn_rejected(self, store):
        with pytest.raises(TurnNotFoundError):
            store.commit_turn_terminal(
                "s1", "turn-9", status="completed", events=[], operation_id="x", intent_payload={}
            )

    def test_operation_id_mismatch_rejected(self, store):
        store.begin_turn("s1", 1, "run-1", message_start_index=0)
        with pytest.raises(TerminalConflictError):
            store.commit_turn_terminal(
                "s1", "turn-1", status="completed", events=[],
                operation_id="wrong-op", intent_payload={},
            )

    def test_event_seq_monotonic_per_turn(self, store):
        _commit(
            store,
            events=[
                {"kind": "message", "payload": {"role": "user", "content": "a"}},
                {"kind": "message", "payload": {"role": "assistant", "content": "b"}},
            ],
        )
        events = store.events_for_turn("s1", "turn-1")
        assert [e.event_seq for e in events] == [0, 1]

    def test_interrupted_commit(self, store):
        store.begin_turn("s1", 1, "run-1", message_start_index=0)
        result = store.mark_turn_interrupted(
            "s1", "turn-1", events=[], intent_payload={"terminal_reason": "recovered"}
        )
        assert result.turn.status == "interrupted"
        assert result.add_job.status == "accepted"

    def test_restart_replays(self, tmp_path):
        db = tmp_path / "work.db"
        s1 = SQLiteMemoryWorkStore(db)
        s1.open()
        _commit(s1, events=[{"kind": "message", "payload": {"role": "user", "content": "hi"}}])
        s1.close()
        s2 = SQLiteMemoryWorkStore(db)
        s2.open()
        try:
            turn = s2.turn("s1", "turn-1")
            assert turn is not None and turn.status == "completed"
            assert len(s2.events_for_turn("s1", "turn-1")) == 1
            assert s2.job_by_operation_key("turnadd:s1:turn-1") is not None
        finally:
            s2.close()


class TestOutbox:
    def test_enqueue_idempotent_operation_key(self, store):
        a = store.enqueue_job("turn_add", "op-1", {"x": 1})
        b = store.enqueue_job("turn_add", "op-1", {"x": 2})
        assert a.job_id == b.job_id
        assert b.payload == {"x": 1}  # first payload wins

    def test_claim_and_complete(self, store):
        store.enqueue_job("turn_add", "op-1", {})
        claimed = store.claim_jobs("w1", limit=5)
        assert len(claimed) == 1
        assert claimed[0].lease_owner == "w1" and claimed[0].status == "processing"
        done = store.complete_job(claimed[0].job_id, owner="w1", result={"ok": True})
        assert done.status == "completed" and done.result == {"ok": True}
        assert store.claim_jobs("w1", limit=5) == []

    def test_claim_respects_not_before(self, store):
        job = store.enqueue_job("turn_add", "op-1", {})
        store.claim_jobs("w1")
        store.fail_job(job.job_id, owner="w1", error="boom", retry_delay_seconds=3600)
        assert store.claim_jobs("w2") == []

    def test_fail_eventually_terminal(self, store):
        job = store.enqueue_job("turn_add", "op-1", {}, max_attempts=2)
        store.claim_jobs("w1")
        store.fail_job(job.job_id, owner="w1", error="e1", retry_delay_seconds=0)
        claimed = store.claim_jobs("w1")
        store.fail_job(claimed[0].job_id, owner="w1", error="e2")
        assert store.job(job.job_id).status == "failed"

    def test_wrong_owner_cannot_finish(self, store):
        job = store.enqueue_job("turn_add", "op-1", {})
        store.claim_jobs("w1")
        with pytest.raises(MemoryWorkStoreError):
            store.complete_job(job.job_id, owner="w2")

    def test_lease_expiry_reclaims(self, store):
        job = store.enqueue_job("turn_add", "op-1", {})
        store.claim_jobs("w1", lease_seconds=0.0)
        import time

        time.sleep(0.01)
        claimed = store.claim_jobs("w2")
        assert [j.job_id for j in claimed] == [job.job_id]

    def test_outcome_unknown_claimable(self, store):
        job = store.enqueue_job("turn_add", "op-1", {})
        store.claim_jobs("w1")
        store.mark_job_unknown(job.job_id, owner="w1", error="lost receipt")
        claimed = store.claim_jobs("w2")
        assert [j.job_id for j in claimed] == [job.job_id]
        assert claimed[0].status == "outcome_unknown"

    def test_requeue_expired(self, store):
        job = store.enqueue_job("turn_add", "op-1", {})
        store.claim_jobs("w1", lease_seconds=0.0)
        import time

        time.sleep(0.01)
        ids = store.requeue_expired()
        assert ids == [job.job_id]
        assert store.job(job.job_id).status == "accepted"


class TestArchives:
    def test_same_boundary_same_archive(self, store):
        a = store.create_archive(
            "s1", turn_range_start=0, turn_range_end=9, max_event_seq=41,
            add_operation_ids=["op1"], intent_payload={"k": 1},
        )
        b = store.create_archive(
            "s1", turn_range_start=0, turn_range_end=9, max_event_seq=41,
            add_operation_ids=["op1"], intent_payload={"k": 1},
        )
        assert a.archive_id == b.archive_id == "archive:s1:41"
        assert a.dreaming_job_id is not None
        assert store.job_by_operation_key("dreaming:archive:s1:41") is not None

    def test_new_boundary_new_archive(self, store):
        a = store.create_archive(
            "s1", turn_range_start=0, turn_range_end=9, max_event_seq=10,
            add_operation_ids=["op1"], intent_payload={},
        )
        b = store.create_archive(
            "s1", turn_range_start=10, turn_range_end=11, max_event_seq=20,
            add_operation_ids=["op2"], intent_payload={},
        )
        assert a.archive_id != b.archive_id


class TestSkillInvocations:
    def test_open_seal_steps(self, store):
        store.open_skill_invocation(
            "inv-1", session_id="s1", turn_id="turn-1", skill_id="sk",
            cloud_skill_id="cloud-sk", skill_version_id="v1", skill_content_hash="h1",
        )
        store.add_invocation_step(
            "inv-1",
            __import__(
                "homemaster.memory.memory_work_store", fromlist=["SkillInvocationStep"]
            ).SkillInvocationStep(
                invocation_id="inv-1",
                step_seq=0,
                tool_call_id="c1",
                tool_name="t",
                arguments={"a": 1},
                tool_result_tail="tail",
                tool_result_truncated=False,
                status="success",
            ),
        )
        inv = store.seal_skill_invocation(
            "inv-1", task_input="do x", agent_completion="done", outcome="success", score=0.8
        )
        assert inv.status == "sealed" and inv.score == 0.8
        assert len(store.invocation_steps("inv-1")) == 1

    @pytest.mark.parametrize(
        "outcome,score,ok",
        [("success", 0.5, True), ("success", 1.0, True), ("success", 0.49, False),
         ("failure", 0.0, True), ("failure", 0.5, True), ("failure", 0.51, False)],
    )
    def test_score_outcome_bounds(self, store, outcome, score, ok):
        store.open_skill_invocation(
            f"inv-{score}-{outcome}", session_id="s", turn_id="t", skill_id="sk",
            cloud_skill_id="c", skill_version_id="v", skill_content_hash="h",
        )
        if ok:
            store.seal_skill_invocation(
                f"inv-{score}-{outcome}", task_input="i", agent_completion="c",
                outcome=outcome, score=score,
            )
        else:
            with pytest.raises(ValueError):
                store.seal_skill_invocation(
                    f"inv-{score}-{outcome}", task_input="i", agent_completion="c",
                    outcome=outcome, score=score,
                )

    def test_claim_exactly_once(self, store):
        for i in range(9):
            store.open_skill_invocation(
                f"inv-{i}", session_id="s", turn_id=f"t{i}", skill_id="sk",
                cloud_skill_id="c1", skill_version_id="v", skill_content_hash="h",
            )
            store.seal_skill_invocation(
                f"inv-{i}", task_input="i", agent_completion="c", outcome="success", score=0.9,
            )
        first = store.claim_skill_traces("c1", batch_id="b1", count=8)
        assert len(first) == 8
        second = store.claim_skill_traces("c1", batch_id="b2", count=8)
        assert len(second) == 1  # only the remainder is claimable
        assert set(first).isdisjoint(second)
        assert store.unconsumed_trace_count("c1") == 0

    def test_pending_evolution_skills(self, store):
        for i in range(8):
            store.open_skill_invocation(
                f"inv-{i}", session_id="s", turn_id=f"t{i}", skill_id="sk",
                cloud_skill_id="hot", skill_version_id="v", skill_content_hash="h",
            )
            store.seal_skill_invocation(
                f"inv-{i}", task_input="i", agent_completion="c", outcome="failure", score=0.2,
            )
        store.open_skill_invocation(
            "inv-cold", session_id="s", turn_id="tc", skill_id="sk",
            cloud_skill_id="cold", skill_version_id="v", skill_content_hash="h",
        )
        store.seal_skill_invocation(
            "inv-cold", task_input="i", agent_completion="c", outcome="success", score=0.9,
        )
        assert store.pending_evolution_skills(8) == ["hot"]


class TestAudit:
    def test_terminal_without_intent_rebuilt(self, store):
        # Simulate a crash between turn terminal and intent insert by manually
        # clearing add_job_id after commit.
        _commit(store)
        store._db.execute("UPDATE turns SET add_job_id = NULL")
        audit = store.audit_and_recover(owner="w1")
        assert ("s1/turn-1",) == audit.terminal_turns_without_intent
        job = store.job_by_operation_key("turnadd:s1:turn-1")
        assert job is not None

    def test_active_turns_interrupted(self, store):
        store.begin_turn("s1", 1, "run-1", message_start_index=0)
        audit = store.audit_and_recover(owner="w1")
        assert audit.active_turns_marked_interrupted == ("s1/turn-1",)
        assert store.turn("s1", "turn-1").status == "interrupted"
        assert store.job_by_operation_key("turnadd:s1:turn-1") is not None

    def test_active_turns_excluded(self, store):
        store.begin_turn("s1", 1, "run-1", message_start_index=0)
        store.begin_turn("s2", 1, "run-2", message_start_index=0)
        audit = store.audit_and_recover(owner="w1", exclude_turns=(("s1", "turn-1"),))
        assert audit.active_turns_marked_interrupted == ("s2/turn-1",)
        assert store.turn("s1", "turn-1").status == "active"


class TestWatermarks:
    def test_watermark_upsert(self, store):
        store.put_feedback_watermark("scope", 100.0, job_id="j1", status="processing")
        mark = store.feedback_watermark("scope", 100.0)
        assert mark["job_id"] == "j1" and mark["status"] == "processing"
        store.put_feedback_watermark("scope", 100.0, job_id="j1", status="completed")
        assert store.feedback_watermark("scope", 100.0)["status"] == "completed"
        assert store.feedback_watermark("scope", 999.0) is None
