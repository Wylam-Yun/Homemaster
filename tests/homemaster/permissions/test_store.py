"""Store tests against real SQLite: atomicity, recovery and concurrency.

No mocks of the database itself; every assertion about durability reads
either the Store API or a fresh raw sqlite3 connection.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FakeClock, decide, make_combo_request, make_request, read_raw

from homemaster.permissions.models import (
    ApprovalConflict,
    ApprovalExpired,
    ApprovalSubmission,
    ExecutionObservation,
    ItemDecision,
    PermissionStorageUnavailable,
)
from homemaster.permissions.store import (
    PermissionStore,
    default_store_path,
    resolve_store_path,
)


def _choices(request, choice: str) -> dict[str, str]:
    return {item.item_id: choice for item in request.requirements}


def _key(env: str, kind: str, rid: str, action: str):
    from homemaster.permissions.models import ResourceKey

    return ResourceKey(
        environment_id=env,
        resource_kind=kind,  # type: ignore[arg-type]
        resource_id=rid,
        action=action,
    )


def test_allow_once_leaves_no_grant(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "once")
    store.create_request(request)
    resolution = decide(store, request, _choices(request, "allow_once"), "sub-once")
    assert resolution.request_status == "ready"
    assert resolution.persisted_grant_ids == ()
    assert store.matching_grants([item.key for item in request.requirements]) == {}
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_grants")[0] == (0,)


def test_allow_always_creates_one_grant(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "always")
    store.create_request(request)
    resolution = decide(store, request, _choices(request, "allow_always"), "sub-always")
    assert resolution.request_status == "ready"
    assert len(resolution.persisted_grant_ids) == 1
    rows = read_raw(
        store.path,
        "SELECT environment_id, resource_kind, resource_id, action, revoked_at"
        " FROM permission_grants",
    )
    assert rows == [("home", "object", "cup-a", "pick_up", None)]


def test_reject_creates_no_grant(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "reject")
    store.create_request(request)
    resolution = decide(store, request, _choices(request, "reject"), "sub-reject")
    assert resolution.request_status == "blocked"
    assert resolution.persisted_grant_ids == ()
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_grants")[0] == (0,)


def test_partial_reject_blocks_but_keeps_grant(
    store: PermissionStore, clock: FakeClock
) -> None:
    request = make_combo_request(clock, "combo")
    store.create_request(request)
    enter, pick = (item.item_id for item in request.requirements)
    resolution = decide(
        store,
        request,
        {enter: "allow_always", pick: "reject"},
        "sub-combo",
    )
    assert resolution.request_status == "blocked"
    assert len(resolution.persisted_grant_ids) == 1
    stored = store.get_request(request.approval_id)
    assert stored.status == "blocked"
    assert {item.item_id: item.decision for item in stored.items} == {
        enter: "allow_always",
        pick: "reject",
    }
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_grants")[0] == (1,)


def test_same_submission_is_idempotent(
    store: PermissionStore, clock: FakeClock
) -> None:
    request = make_request(clock, "idem")
    store.create_request(request)
    first = decide(store, request, _choices(request, "allow_always"), "sub-idem")
    second = decide(store, request, _choices(request, "allow_always"), "sub-idem")
    assert first == second
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_grants")[0] == (1,)
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_submissions")[0] == (1,)


def test_same_submission_id_with_other_content_conflicts(
    store: PermissionStore, clock: FakeClock
) -> None:
    request = make_request(clock, "conflict")
    store.create_request(request)
    decide(store, request, _choices(request, "allow_always"), "sub-dup")
    with pytest.raises(ApprovalConflict):
        decide(store, request, _choices(request, "reject"), "sub-dup")


def test_revision_mismatch_conflicts(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "rev")
    store.create_request(request)
    with pytest.raises(ApprovalConflict):
        store.submit(
            request.approval_id,
            ApprovalSubmission(
                submission_id="sub-rev",
                request_revision=request.revision + 1,
                decisions=tuple(
                    ItemDecision(item_id=item.item_id, choice="allow_once")
                    for item in request.requirements
                ),
            ),
            "tester",
        )


def test_expired_request_cannot_submit(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "expired")
    store.create_request(request)
    clock.advance(600)
    with pytest.raises(ApprovalExpired):
        decide(store, request, _choices(request, "allow_once"), "sub-expired")
    assert store.get_request(request.approval_id).status == "expired"


def test_no_partial_commit_on_mid_submit_failure(
    store: PermissionStore, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = make_combo_request(clock, "atomic")
    store.create_request(request)
    enter, pick = (item.item_id for item in request.requirements)
    calls = {"updates": 0}
    original = PermissionStore._execute

    def flaky(self: PermissionStore, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        if sql.startswith("UPDATE permission_request_items"):
            calls["updates"] += 1
            if calls["updates"] == 2:
                raise sqlite3.OperationalError("injected fault")
        return original(self, sql, params)

    monkeypatch.setattr(PermissionStore, "_execute", flaky)
    with pytest.raises(PermissionStorageUnavailable):
        decide(store, request, {enter: "allow_always", pick: "reject"}, "sub-atomic")
    rows = read_raw(
        store.path,
        "SELECT status FROM permission_requests WHERE request_id = ?",
        (request.request_id,),
    )
    assert rows == [("prepared",)]
    assert read_raw(
        store.path,
        "SELECT COUNT(*) FROM permission_request_items"
        " WHERE request_id = ? AND decision IS NOT NULL",
        (request.request_id,),
    )[0] == (0,)
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_grants")[0] == (0,)
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_submissions")[0] == (0,)


def test_interleaved_connections(
    store_path: Path, clock: FakeClock
) -> None:
    first_clock = FakeClock()
    second_clock = FakeClock()
    first = PermissionStore.open(store_path, clock=first_clock)
    second = PermissionStore.open(store_path, clock=second_clock)
    try:
        req_a = make_request(first_clock, "ia")
        req_b = make_request(second_clock, "ib")
        first.create_request(req_a)
        second.create_request(req_b)
        res_a = decide(first, req_a, _choices(req_a, "allow_always"), "sub-ia")
        res_b = decide(second, req_b, _choices(req_b, "reject"), "sub-ib")
        assert res_a.request_status == "ready"
        assert res_b.request_status == "blocked"
        grant_id = res_a.persisted_grant_ids[0]
        revoked = first.revoke(grant_id, "rev-ia", 1, "tester")
        assert revoked.revoked_at is not None
        assert revoked.revision == 2
        same = first.revoke(grant_id, "rev-ia", 1, "tester")
        assert same.revoked_at == revoked.revoked_at
        with pytest.raises(ApprovalConflict):
            second.revoke(grant_id, "rev-ib", 1, "tester")
    finally:
        first.close()
        second.close()
    assert read_raw(store_path, "SELECT COUNT(*) FROM permission_grants")[0] == (1,)
    assert read_raw(store_path, "SELECT COUNT(*) FROM permission_submissions")[0] == (2,)
    statuses = {
        row[0]
        for row in read_raw(store_path, "SELECT status FROM permission_requests")
    }
    assert statuses == {"ready", "blocked"}


def test_claim_and_finish_lifecycle(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "life")
    store.create_request(request)
    decide(store, request, _choices(request, "allow_once"), "sub-life")
    step = request.steps[0]
    binding = store.claim_step(request.request_id, step.step_id, request.revision)
    assert binding == step.binding_ref
    with pytest.raises(ApprovalConflict):
        store.claim_step(request.request_id, step.step_id, request.revision)
    status = store.finish_step(
        request.request_id,
        step.step_id,
        ExecutionObservation(
            outcome="succeeded",
            backend_code="ok",
            binding_ref=step.binding_ref,
            observed_resource_id="cup-a",
            current_area_id="bedroom",
            evidence_ref="ev-001",
        ),
    )
    assert status == "succeeded"
    with pytest.raises(ApprovalConflict):
        store.claim_step(request.request_id, step.step_id, request.revision)


def test_unknown_outcome_blocks_reclaim(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "unk")
    store.create_request(request)
    decide(store, request, _choices(request, "allow_once"), "sub-unk")
    step = request.steps[0]
    store.claim_step(request.request_id, step.step_id, request.revision)
    status = store.finish_step(
        request.request_id,
        step.step_id,
        ExecutionObservation(
            outcome="unknown",
            backend_code="timeout",
            binding_ref=step.binding_ref,
            observed_resource_id=None,
            current_area_id=None,
            evidence_ref="ev-002",
        ),
    )
    assert status == "outcome_unknown"
    with pytest.raises(ApprovalConflict):
        store.claim_step(request.request_id, step.step_id, request.revision)


def test_retry_within_budget_after_no_effect(
    store: PermissionStore, clock: FakeClock
) -> None:
    request = make_request(clock, "retry")
    store.create_request(request)
    decide(store, request, _choices(request, "allow_once"), "sub-retry")
    step = request.steps[0]
    for _ in range(2):
        store.claim_step(request.request_id, step.step_id, request.revision)
        status = store.finish_step(
            request.request_id,
            step.step_id,
            ExecutionObservation(
                outcome="no_effect_failure",
                backend_code="busy",
                binding_ref=step.binding_ref,
                observed_resource_id=None,
                current_area_id=None,
                evidence_ref="ev-003",
            ),
        )
        assert status == "running"
    stored = store.get_request(request.approval_id)
    assert stored.steps[0].attempt_count == 2


def test_matching_is_exact_only(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "exact")
    store.create_request(request)
    decide(store, request, _choices(request, "allow_always"), "sub-exact")
    hit = _key("home", "object", "cup-a", "pick_up")
    assert list(store.matching_grants([hit]).values())[0].active
    for miss in (
        _key("home", "object", "cup-b", "pick_up"),
        _key("home", "object", "cup-a", "clean"),
        _key("other-home", "object", "cup-a", "pick_up"),
    ):
        assert store.matching_grants([miss]) == {}


def test_snapshot_has_no_native_handles(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "snap")
    store.create_request(request)
    rows = read_raw(
        store.path,
        "SELECT immutable_plan_json FROM permission_requests WHERE request_id = ?",
        (request.request_id,),
    )
    plan = json.loads(rows[0][0])
    blob = json.dumps(plan)
    for banned in ("native", "scene", "episode", "handle", "coordinate"):
        assert banned not in blob.casefold()


def test_mirror_failure_does_not_reverse_commit(
    store: PermissionStore,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(self: PermissionStore, events: list) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(PermissionStore, "_emit", broken)
    request = make_request(clock, "mirror")
    store.create_request(request)
    resolution = decide(store, request, _choices(request, "allow_always"), "sub-mirror")
    assert resolution.request_status == "ready"
    assert read_raw(store.path, "SELECT COUNT(*) FROM permission_grants")[0] == (1,)


def test_recover_interrupts_pending(store: PermissionStore, clock: FakeClock) -> None:
    waiting = make_request(clock, "rec-wait")
    running_req = make_request(clock, "rec-run")
    store.create_request(waiting)
    store.create_request(running_req)
    decide(store, running_req, _choices(running_req, "allow_once"), "sub-rec-run")
    step = running_req.steps[0]
    store.claim_step(running_req.request_id, step.step_id, running_req.revision)
    summary = store.recover()
    assert summary == {"interrupted_requests": 2, "unknown_steps": 1}
    assert store.get_request(waiting.approval_id).status == "interrupted"
    stored = store.get_request(running_req.approval_id)
    assert stored.status == "interrupted"
    assert stored.steps[0].status == "unknown"


def test_cancel_is_idempotent(store: PermissionStore, clock: FakeClock) -> None:
    request = make_request(clock, "cancel")
    store.create_request(request)
    assert store.cancel(request.request_id, "user closed the card") == "cancelled"
    assert store.cancel(request.request_id, "again") == "cancelled"
    with pytest.raises(ApprovalConflict):
        decide(store, request, _choices(request, "allow_once"), "sub-cancel")


def test_store_path_helpers(tmp_path: Path) -> None:
    derived = default_store_path("~/.homemaster/sessions", "tenant-a")
    assert derived.name == "permissions.sqlite3"
    assert derived.parent.name == "tenant-a"
    assert default_store_path("~/.homemaster/sessions", "../evil").parent.name != "evil"
    assert ".." not in default_store_path("~/.homemaster/sessions", "../evil").parts
    explicit = resolve_store_path(
        "/tmp/custom.sqlite3",
        session_dir="~/.homemaster/sessions",
        tenant_id="tenant-a",
    )
    assert explicit == Path("/tmp/custom.sqlite3")


_CHILD_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[2])
sys.path.insert(0, sys.argv[3])
from conftest import FakeClock, decide, make_request
from homemaster.permissions.store import PermissionStore
store = PermissionStore.open(sys.argv[1], clock=FakeClock())
request = make_request(store._clock, "extproc")
store.create_request(request)
item_id = request.requirements[0].item_id
resolution = decide(store, request, {item_id: "allow_always"}, "sub-extproc")
print(resolution.request_status, len(resolution.persisted_grant_ids))
store.close()
"""


def test_external_process_readback(tmp_path: Path) -> None:
    import os

    worktree = Path(__file__).resolve().parents[3]
    db = tmp_path / "external.sqlite3"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _CHILD_SCRIPT,
            str(db),
            str(worktree / "src"),
            str(Path(__file__).resolve().parent),
        ],
        cwd=worktree,
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ready 1"
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM permission_grants").fetchone()[0] == 1
        assert conn.execute(
            "SELECT environment_id, resource_kind, resource_id, action, revoked_at"
            " FROM permission_grants"
        ).fetchall() == [("home", "object", "cup-a", "pick_up", None)]
        assert conn.execute(
            "SELECT COUNT(*) FROM permission_submissions"
        ).fetchone()[0] == 1
    finally:
        conn.close()
