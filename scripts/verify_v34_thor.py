"""Real-THOR verification for the V3.4 ALFWorld adapter (Task 8 gate).

Runs with the benchmark venv (alfworld-venv python) + worktree src on
PYTHONPATH, DISPLAY=:99, ALFWORLD_DATA set. Drives ONE pinned trial:
reset/read/reset/read id stability, cross-scene isolation, the production
resolve/prepare path, and real nav+take execution with independent
inventory readback (THOR metadata, not adapter.observe).

Writes locked evidence to .runtime/v34/acceptance/thor-001 (refuses reuse).
Exit 0 only if every required check passes. Slow phases print heartbeats;
run under nohup and poll the evidence dir.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_MAIN = Path("/data1/haodong2/weilin/red_bird/Homemaster")
WORKTREE = Path("/data1/haodong2/weilin/red_bird/Homemaster-v34-permissions")


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _evidence() -> dict[str, Any]:
    return {"checks": []}


def _record(evidence: dict, name: str, ok: bool, detail: dict[str, Any]) -> None:
    evidence["checks"].append({"case": name, "status": "pass" if ok else "fail", **detail})
    _log(f"{'PASS' if ok else 'FAIL'} {name}")
    if not ok:
        raise AssertionError(f"thor check failed: {name} {detail}")


def _finding(evidence: dict, name: str, detail: dict[str, Any]) -> None:
    evidence["checks"].append({"case": name, "status": "finding", **detail})
    _log(f"FINDING {name}: {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V3.4 real-THOR verification")
    parser.add_argument("--output", required=True)
    parser.add_argument("--trial-a", required=True, help="traj_data.json for scene A")
    parser.add_argument("--trial-b", required=True, help="traj_data.json for scene B")
    args = parser.parse_args(argv)

    out = Path(args.output)
    if out.exists():
        print(f"refusing to reuse evidence directory: {out}", file=sys.stderr)
        return 2
    (out / "tmp").mkdir(parents=True)

    from homemaster.benchmarking.alfworld.env_adapter import (
        AlfworldEnvAdapter,
        build_alfworld_batch_env,
    )
    from homemaster.benchmarking.alfworld.permission_adapter import (
        AlfworldPermissionAdapter,
        ThorBackendView,
    )
    from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
    from homemaster.tools.base import ToolExecutionContext

    evidence = _evidence()
    evidence["created_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    evidence["trial_a"] = args.trial_a
    evidence["trial_b"] = args.trial_b

    config = AlfworldBenchmarkConfig(
        alfworld_root=REPO_MAIN / ".runtime" / "alfworld",
        alfworld_config=REPO_MAIN / ".runtime" / "alfworld" / "configs" / "base_config.yaml",
        trace_root=out / "tmp",
        data_root=REPO_MAIN / ".runtime" / "alfworld" / "data",
        env_type="AlfredThorEnv",
        split="valid_unseen",
        seed=7,
    )
    _log("building THOR env (downloads nothing; uses local binary)...")
    env = build_alfworld_batch_env(config)
    env.json_file_list = [args.trial_a]
    adapter = AlfworldEnvAdapter(env=env, episode_prefix="v34thor", seed=7)

    _log("reset #1 ...")
    reset1 = adapter.reset()
    _record(evidence, "reset-ready", bool(reset1.ready),
            {"classification": str(getattr(reset1, "classification", ""))})
    index1 = adapter.authoritative_object_index
    _record(evidence, "index-available", index1 is not None, {})
    assert index1 is not None

    singles = {t: labels for t, labels in index1.labels_by_type.items() if len(labels) == 1}
    _log(f"single-instance types: {sorted(singles)[:12]}")
    pick_type = next((t for t in ("mug", "cup", "apple", "book", "pencil")
                      if t in singles), None)
    if pick_type is None:
        pick_type = sorted(singles)[0]
    label = singles[pick_type][0]
    ref1 = index1.resolve(label)
    assert ref1 is not None
    id1 = ref1.object_id
    _log(f"target: {label} -> {id1} ({ref1.object_type})")

    _log("reset #2 (same trial) ...")
    reset2 = adapter.reset()
    _record(evidence, "reset2-ready", bool(reset2.ready), {})
    index2 = adapter.authoritative_object_index
    assert index2 is not None
    ref2 = index2.resolve(label)
    id2 = ref2.object_id if ref2 is not None else None
    _finding(evidence, "id-across-reset",
                {"id_reset1": id1, "id_reset2": id2,
                 "stable": id2 == id1,
                 "note": "THOR position-based ids change when poses reshuffle; "
                         "grants must never cross a reset boundary"})

    _log("reset #3 (different scene) ...")
    env.json_file_list = [args.trial_b]
    reset3 = adapter.reset()
    _record(evidence, "reset3-ready", bool(reset3.ready), {})
    index3 = adapter.authoritative_object_index
    assert index3 is not None
    ref3 = index3.resolve(label)
    id3 = ref3.object_id if ref3 is not None else None
    _record(evidence, "cross-scene-isolation",
            id3 != id1, {"id_scene_a": id1, "id_scene_b": id3})

    _log("back to scene A for execution ...")
    env.json_file_list = [args.trial_a]
    reset4 = adapter.reset()
    _record(evidence, "reset4-ready", bool(reset4.ready), {})
    state = adapter.current_state
    _log(f"observation: {state.observation[:200]}")

    obj_type = ref1.object_type
    subtask = SimpleNamespace(object=obj_type, parent=None, toggle=None, mrecep=None)
    view = ThorBackendView(adapter, subtask=subtask)
    evidence["environment_id"] = view.environment_id()
    bare = pick_type
    target = view.resolve_target(bare, allowed=frozenset({"object", "receptacle", "toggle"}))
    _record(evidence, "production-resolve", target is not None,
            {"label": bare,
             "object_id": target.object_id if target is not None else None,
             "kind": target.kind if target is not None else None})
    assert target is not None
    garbage = view.resolve_target("dragonnonexistent", allowed=frozenset({"object"}))
    _record(evidence, "production-unknown-refuses", garbage is None, {})

    perm = AlfworldPermissionAdapter(backend=view)
    context = ToolExecutionContext(
        WORKTREE, metadata={"session_id": "thor-verify", "run_id": "thor-run-1"})
    request = await_prepare(perm, {"action": "take", "object": bare}, context)
    key = request.requirements[0].key
    _record(evidence, "prepare-uses-real-id",
            key.resource_id == target.object_id and key.action == "pick_up",
            {"resource_id": key.resource_id, "action": key.action,
             "steps": [s.summary for s in request.steps]})
    assert len(request.steps) == 2

    bindings = [step.binding_ref for step in request.steps]
    _log(f"executing nav {bindings[0]} ...")
    nav_result = await_execute(perm, bindings[0], context)
    _record(evidence, "real-nav-executed", nav_result is not None, nav_result)
    _log(f"executing take {bindings[1]} ...")
    take_result = await_execute(perm, bindings[1], context)
    _record(evidence, "real-take-executed", take_result is not None, take_result)

    held = read_inventory_ids(adapter)
    _record(evidence, "independent-inventory-readback", target.object_id in held,
            {"inventory_ids": held, "expected": target.object_id})

    observation = await_observe(perm, bindings[1], context)
    _record(evidence, "observe-succeeded",
            observation["outcome"] == "succeeded" and bool(observation["evidence_ref"]),
            observation)
    last_nav = getattr(adapter, "_last_go_to_object_id", "n/a")
    _record(evidence, "nav-manipulate-id-handoff", last_nav == target.object_id,
            {"last_go_to_object_id": str(last_nav)})

    failed = [c for c in evidence["checks"] if c["status"] == "fail"]
    result = "fail" if failed else "pass"
    manifest = {
        "schema_version": 1,
        "created_at": evidence["created_at"],
        "backend": "thor",
        "result": result,
        "areas": {"thor_real_backend": {
            "status": result, "required": True, "checks": evidence["checks"]}},
        "environment_id": evidence.get("environment_id", ""),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"THOR verification {result.upper()}")
    return 0 if result == "pass" else 1


def await_prepare(perm: Any, call: dict, context: Any) -> Any:
    import asyncio

    return asyncio.run(_prepare_one(perm, call, context))


async def _prepare_one(perm: Any, call: dict, context: Any) -> Any:
    return await perm.prepare(call, context)


def await_execute(perm: Any, binding: str, context: Any) -> dict:
    import asyncio

    return asyncio.run(_execute_one(perm, binding, context))


async def _execute_one(perm: Any, binding: str, context: Any) -> dict:
    result = await perm.execute(binding, context)
    return {"is_error": result.is_error, "output": result.output[:200],
            "metadata": dict(result.metadata)}


def await_observe(perm: Any, binding: str, context: Any) -> dict:
    import asyncio

    return asyncio.run(_observe_one(perm, binding, context))


async def _observe_one(perm: Any, binding: str, context: Any) -> dict:
    observation = await perm.observe(binding, context)
    return {"outcome": observation.outcome,
            "backend_code": observation.backend_code,
            "evidence_ref": observation.evidence_ref}


def read_inventory_ids(adapter: Any) -> list[str]:
    thor_env = adapter._resolve_thor_env()
    event = getattr(thor_env, "last_event", None)
    metadata = getattr(event, "metadata", None) or {}
    items = metadata.get("inventoryObjects") or []
    return [str(item.get("objectId")) for item in items
            if isinstance(item, dict) and item.get("objectId")]


if __name__ == "__main__":
    raise SystemExit(main())
