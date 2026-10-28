#!/usr/bin/env python3
"""scripted_grasp 后端：固定流程抓取，全程调 move_to / set_gripper 子进程。

用法：python3 scripted_grasp.py '{"xyz":[0.6,0.0,0.9],"arm":"right"}'
流程：张开 → 悬停(xyz+approach_z) → 下降(xyz+grasp_z_offset) → 闭合 → 抬起。
任一步失败即 abort，输出标明卡在哪一步。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C


def call(script, payload):
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(C.repo_dir(), script), json.dumps(payload)],
            capture_output=True, text=True,
            timeout=float(payload.get("timeout_s", 60.0)),
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"{script} 超时"}
    out = (r.stdout or "").strip().splitlines()
    try:
        return json.loads(out[-1]) if out else {"success": False, "error": r.stderr[-500:]}
    except json.JSONDecodeError:
        return {"success": False, "error": (r.stdout or r.stderr)[-500:]}


def main():
    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as e:
        C.die(f"参数不是合法 JSON：{e}")
    xyz = args.get("xyz")
    arm = args.get("arm")
    approach_z = float(args.get("approach_z", 0.10))
    grasp_z = float(args.get("grasp_z_offset", 0.0))
    timeout_s = float(args.get("timeout_s", 60.0))
    if not (isinstance(xyz, list) and len(xyz) == 3):
        C.die("xyz 必须是 world 系 [x,y,z]", got=xyz)
    if arm not in ("left", "right"):
        C.die("arm 必须显传 left/right", got=arm)
    if not (1.0 <= timeout_s <= 600.0):
        C.die("timeout_s 必须在 1~600", got=timeout_s)

    def shifted(dz):
        return [xyz[0], xyz[1], xyz[2] + dz]

    phases = []
    seq = [
        ("open", "set_gripper.py",
         {"arm": arm, "state": "open", "timeout_s": timeout_s}),
        ("hover", "move_to.py",
         {"xyz": shifted(approach_z), "arm": arm,
          "timeout_s": timeout_s, "converge": False}),
        ("descend", "move_to.py",
         {"xyz": shifted(grasp_z), "arm": arm,
          "timeout_s": timeout_s, "converge": False}),
        ("close", "set_gripper.py",
         {"arm": arm, "state": "close", "timeout_s": timeout_s}),
        ("lift", "move_to.py",
         {"xyz": shifted(approach_z), "arm": arm,
          "timeout_s": timeout_s, "converge": False}),
    ]
    for name, script, payload in seq:
        res = call(script, payload)
        phases.append({"phase": name, "result": res})
        if not res.get("success"):
            C.emit({"success": False, "failed_phase": name, "phases": phases})
            sys.exit(1)
    C.emit({"success": True, "phases": phases})


if __name__ == "__main__":
    main()
