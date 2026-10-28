#!/usr/bin/env python3
"""move_delta 后端：当前位置 + dxyz，再调共享 move_to。

用法：python3 move_delta.py '{"dxyz":[0.02,0,0],"arm":"right"}'
dxyz 是 world 系增量（米）。本脚本只读一次 TCP 算出目标，转调 move_to.py，
目标只计算一次；实际规划、执行和最终 TF 校验全部走 move_to。
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C

import numpy as np


def main():
    C.check_ros()
    import rclpy

    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as e:
        C.die(f"参数不是合法 JSON：{e}")
    dxyz = args.get("dxyz")
    arm = args.get("arm")
    if not (isinstance(dxyz, list) and len(dxyz) == 3):
        C.die("dxyz 必须是 [dx,dy,dz]（world 系，米）", got=dxyz)
    if arm not in ("left", "right"):
        C.die("arm 必须显传 left/right", got=arm)
    try:
        dxyz = np.asarray(dxyz, dtype=np.float64)
    except (TypeError, ValueError):
        C.die("dxyz 必须是数字", got=dxyz)
    if not np.all(np.isfinite(dxyz)):
        C.die("dxyz 必须是有限数字", got=dxyz.tolist())
    if np.linalg.norm(dxyz) > 0.50:
        C.die("单次 dxyz 超过 0.50m，拒绝执行", norm_m=float(np.linalg.norm(dxyz)))

    cfg = C.load_config()
    timeout_s = float(args.get(
        "timeout_s", cfg["moveit"].get("default_timeout_s", 15.0)
    ))

    rclpy.init()
    node = rclpy.create_node("arm_tools_movedelta")
    try:
        live = C.LiveState(node, cfg)
        tcp = live.get_tcp(arm, timeout_s=5.0)
        if tcp is None:
            C.die("读不到 TCP TF（world->hand_tcp）")
        target_world = np.asarray(tcp["xyz"], dtype=np.float64) + dxyz
    finally:
        node.destroy_node()
        rclpy.shutdown()

    payload = {
        "xyz": target_world.tolist(),
        "arm": arm,
        "timeout_s": timeout_s,
        "converge": False,
    }
    move_to = os.path.join(C.repo_dir(), "move_to.py")
    r = subprocess.run(
        [sys.executable, move_to, json.dumps(payload)],
        capture_output=True, text=True,
    )
    sys.stdout.write(r.stdout if r.stdout else "")
    if r.returncode != 0 and not r.stdout.strip():
        C.die("move_to 子进程失败", stderr=r.stderr[-500:])
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
