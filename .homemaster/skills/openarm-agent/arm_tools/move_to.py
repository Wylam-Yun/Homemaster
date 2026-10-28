#!/usr/bin/env python3
"""Move the requested hand_tcp point through the shared MoveIt client.

Input xyz is in the robot ``world`` frame. MoveIt plans and executes the
motion; this tool does not publish joint commands or run its own IK solver.
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C
from robot_client import MoveItClient


def main():
    C.check_ros()
    import rclpy

    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as exc:
        C.die(f"参数不是合法 JSON：{exc}")

    xyz = args.get("xyz")
    arm = args.get("arm")
    cfg = C.load_config()
    timeout_s = float(args.get(
        "timeout_s", cfg["moveit"].get("default_timeout_s", 15.0)
    ))
    converge = args.get("converge")
    if converge is not None and not isinstance(converge, bool):
        C.die("converge 必须是布尔值", got=converge)
    if not (isinstance(xyz, list) and len(xyz) == 3):
        C.die("xyz 必须是 [x,y,z]（world 系，米）", got=xyz)
    if arm not in ("left", "right"):
        C.die("arm 必须显传 left/right", got=arm)
    if not (0.02 <= timeout_s <= 300.0):
        C.die("timeout_s 必须在 0.02~300", got=timeout_s)
    try:
        xyz_world = np.asarray(xyz, dtype=np.float64)
    except (TypeError, ValueError):
        C.die("xyz 必须是数字", got=xyz)
    if not np.all(np.isfinite(xyz_world)):
        C.die("xyz 必须是有限数字", got=xyz)

    if xyz_world[2] < float(cfg["limits"]["min_z_m"]):
        C.die("目标 z 低于安全高度", z=float(xyz_world[2]))
    if np.linalg.norm(xyz_world) > 10.0:
        C.die("目标距离 world 原点异常，拒绝执行", xyz_world=xyz_world.tolist())

    rclpy.init()
    node = rclpy.create_node("arm_tools_move_to")
    try:
        live = C.LiveState(node, cfg)
        if live.wait_joints(timeout_s=5.0) is None:
            C.die("5s 内无 /joint_states（驱动没起？）")
        tcp = live.get_tcp(arm, timeout_s=5.0)
        if tcp is None:
            C.die("读不到 TCP TF（world->hand_tcp）")
        distance_m = float(np.linalg.norm(
            xyz_world - np.asarray(tcp["xyz"], dtype=np.float64)
        ))
        max_move = float(cfg["limits"]["max_single_move_m"])
        if distance_m > max_move:
            C.die(
                "单次移动超过安全上限，请拆成多次 move_to",
                distance_m=round(distance_m, 4),
                max_single_move_m=max_move,
            )

        moveit_cfg = cfg["moveit"]
        result = MoveItClient(node, live, cfg).move_tcp(
            arm,
            xyz_world,
            tcp["quat"],
            timeout_s=timeout_s,
            position_tolerance=float(moveit_cfg["position_tolerance_m"]),
            orientation_tolerance=float(moveit_cfg["orientation_tolerance_rad"]),
            start_xyz=tcp["xyz"],
            converge_enabled=converge,
        )

        result.update({
            "arm": arm,
            "target_frame": "world",
            "target_link": "hand_tcp",
            "target_xyz_world": [round(float(v), 4) for v in xyz_world],
        })
        C.emit(result)
        if not result.get("success"):
            sys.exit(1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
