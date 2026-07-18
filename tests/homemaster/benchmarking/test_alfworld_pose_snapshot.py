from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from homemaster.benchmarking.alfworld.pose_snapshot import (
    CachePoseInput,
    FrozenOraclePoseStore,
    OraclePose,
    OraclePoseSnapshot,
    OraclePoseSnapshotEntry,
    ScanPolicyInput,
    SceneObjectScanInput,
    SceneScanPlanBuilder,
    load_public_object_vocabulary,
    oracle_pose_matches,
    pose_sha256,
)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _inputs(*, reverse: bool = False) -> ScanPolicyInput:
    objects = [
        SceneObjectScanInput(
            exact_object_id="Drawer|1",
            object_type="Drawer",
            position=(1.0, 0.5, 1.0),
            parent_receptacle_ids=(),
            receptacle_object_ids=("Mug|1",),
            is_picked_up=False,
            closed_ancestor_exact_ids=(),
            pose_freshness_sha256=_sha("drawer"),
        ),
        SceneObjectScanInput(
            exact_object_id="Mug|1",
            object_type="Mug",
            position=(1.1, 0.6, 1.1),
            parent_receptacle_ids=("Drawer|1",),
            receptacle_object_ids=(),
            is_picked_up=False,
            closed_ancestor_exact_ids=("Drawer|1",),
            pose_freshness_sha256=_sha("mug"),
        ),
        SceneObjectScanInput(
            exact_object_id="Cabinet|1",
            object_type="Cabinet",
            position=(2.0, 0.0, 2.0),
            parent_receptacle_ids=(),
            receptacle_object_ids=(),
            is_picked_up=False,
            closed_ancestor_exact_ids=(),
            pose_freshness_sha256=_sha("cabinet"),
        ),
    ]
    cache_entries = [
        CachePoseInput(
            exact_object_id="Drawer|1",
            pose=OraclePose(0.0, 0.9, 0.0, 0.0, 0.0),
            source_record_sha256=_sha("cache-drawer"),
        )
    ]
    reachable = [
        {"x": 1.0, "y": -0.0, "z": 1.5},
        {"x": 2.0, "y": 0.0, "z": 1.5},
        {"x": 1.0, "y": 0.0, "z": 1.5},
    ]
    if reverse:
        objects.reverse()
        cache_entries.reverse()
        reachable.reverse()
    reachable_bytes = json.dumps(reachable, separators=(",", ":")).encode()
    return ScanPolicyInput(
        scene_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        algorithm_version="v18-bounded-scan-1",
        geometry_policy_version="v18-nearest-yaw-horizon-1",
        setup_time_control_version="change-time-scale-bracket-v1",
        setup_slow_time_scale=0.01,
        setup_restore_time_scale=1.0,
        successful_setup_action_offset=4,
        initial_pose=OraclePose(0.0, 0.9, 0.0, 0.0, 0.0),
        public_semantic_vocabulary=("Cabinet", "Drawer", "Mug"),
        objects=tuple(objects),
        cache_entries=tuple(cache_entries),
        reachable_payload=reachable_bytes,
    )


def test_oracle_pose_match_accepts_thor_normalization_but_rejects_real_drift() -> None:
    requested = OraclePose(-5.5, 0.9010564, 1.0, 183.164758, -12.88501)

    assert oracle_pose_matches(
        OraclePose(-5.5, 0.9010564, 1.0, 183.164764, -12.0), requested
    )
    assert oracle_pose_matches(OraclePose(0, 0.9, 0, 0.0, 0), OraclePose(0, 0.9, 0, 360, 0))
    assert not oracle_pose_matches(
        OraclePose(-5.499, 0.9010564, 1.0, 183.164764, -12.0), requested
    )
    assert not oracle_pose_matches(
        OraclePose(-5.5, 0.9010564, 1.0, 183.164764, -11.0), requested
    )


def test_scan_plan_is_canonical_and_merges_initial_pose_provenance() -> None:
    forward = SceneScanPlanBuilder().build(_inputs())
    reverse = SceneScanPlanBuilder().build(_inputs(reverse=True))

    assert forward.canonical_sha256 == reverse.canonical_sha256
    assert forward.scan_policy_sha256 == reverse.scan_policy_sha256
    assert forward.reachable_canonical_sha256 == reverse.reachable_canonical_sha256
    assert forward.reachable_payload_sha256 != reverse.reachable_payload_sha256
    assert [step.index for step in forward.steps] == list(range(len(forward.steps)))
    assert forward.steps[0].send_teleport is False
    assert forward.steps[0].provenances[0].exact_object_id == "Drawer|1"
    assert all(step.send_teleport for step in forward.steps[1:])
    assert len({step.pose for step in forward.steps}) == len(forward.steps)
    assert len(forward.steps) == 2  # initial/cache merge plus one geometry pose for Cabinet


def test_scan_policy_rejects_non_finite_reachable_and_broken_containment() -> None:
    base = _inputs()
    with pytest.raises(ValueError, match="finite"):
        SceneScanPlanBuilder().build(
            replace(base, reachable_payload=b'[{"x":NaN,"y":0,"z":1}]')
        )

    broken = replace(
        base.objects[0],
        receptacle_object_ids=(),
    )
    with pytest.raises(ValueError, match="reciprocal"):
        SceneScanPlanBuilder().build(replace(base, objects=(broken, *base.objects[1:])))


def test_pose_hash_includes_y_and_vocabulary_is_pinned() -> None:
    assert pose_sha256(OraclePose(1, 2, 3, 4, 5)) != pose_sha256(
        OraclePose(1, 2.5, 3, 4, 5)
    )
    vocabulary = load_public_object_vocabulary()
    assert len(vocabulary.object_types) == 25
    assert vocabulary.canonical_sha256 == (
        "ee9b62391469e5efbc3883a3e2ebab4998010e1206313bcb9d19157bc52580da"
    )


def _snapshot() -> OraclePoseSnapshot:
    entries = (
        OraclePoseSnapshotEntry(
            exact_object_id="Cabinet|1",
            status="coverage_miss",
            addressable=True,
            addressability_reason="public_semantic",
            pose=None,
            pose_sha256=None,
            pose_freshness_sha256=_sha("cabinet"),
            source_kind="none",
            evidence_ref=None,
        ),
        OraclePoseSnapshotEntry(
            exact_object_id="Mug|1",
            status="unobserved",
            addressable=False,
            addressability_reason="closed_ancestor",
            pose=None,
            pose_sha256=None,
            pose_freshness_sha256=_sha("mug"),
            source_kind="none",
            evidence_ref=None,
        ),
        OraclePoseSnapshotEntry(
            exact_object_id="Drawer|1",
            status="ok",
            addressable=True,
            addressability_reason="public_semantic",
            pose=OraclePose(0, 0.9, 0, 0, 0),
            pose_sha256=pose_sha256(OraclePose(0, 0.9, 0, 0, 0)),
            pose_freshness_sha256=_sha("drawer"),
            source_kind="cache",
            evidence_ref="poses/drawer.json",
        ),
    )
    return OraclePoseSnapshot.create(
        scene_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        algorithm_version="v18-bounded-scan-1",
        scan_policy_sha256=_sha("policy"),
        reachable_payload_sha256=_sha("raw"),
        reachable_canonical_sha256=_sha("reachable"),
        scan_plan_sha256=_sha("plan"),
        initial_event_ref="events/reset.json",
        restored_event_ref="events/normal-time-restore.json",
        initial_world_sha256=_sha("world"),
        restored_world_sha256=_sha("world"),
        entries=entries,
    )


def test_snapshot_rejects_invalid_published_row_and_store_distinguishes_overlays() -> None:
    snapshot = _snapshot()
    store = FrozenOraclePoseStore()
    store.publish(snapshot)

    assert store.get_pose(
        scene_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        exact_anchor_id="Drawer|1",
    ).status == "ok"
    assert store.get_pose(
        scene_generation=1,
        scene_reset_fingerprint=_sha("scene"),
        exact_anchor_id="missing",
    ).status == "absent"
    assert store.get_pose(
        scene_generation=2,
        scene_reset_fingerprint=_sha("scene"),
        exact_anchor_id="Drawer|1",
    ).status == "stale"

    for status in ("relocated", "malformed", "stale", "error"):
        store.set_overlay("Drawer|1", status=status, evidence_ref=f"overlay/{status}.json")
        assert store.get_pose(
            scene_generation=1,
            scene_reset_fingerprint=_sha("scene"),
            exact_anchor_id="Drawer|1",
        ).status == status

    with pytest.raises(ValueError):
        replace(snapshot.entries[0], status="ok")
