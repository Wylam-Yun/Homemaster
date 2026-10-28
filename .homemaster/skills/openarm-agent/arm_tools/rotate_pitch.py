#!/usr/bin/env python3
"""Rotate hand_tcp pitch through the shared MoveIt client."""
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C
from robot_client import MoveItClient, matrix_from_quat, quat_from_matrix


def pitch_of_quat(q):
    R = matrix_from_quat(q)
    return float(math.atan2(-R[2, 0], math.hypot(R[0, 0], R[1, 0])))


def rpy_from_matrix(R):
    """Extract intrinsic ZYX roll/pitch/yaw matching pitch_of_quat()."""
    cp = math.hypot(float(R[0, 0]), float(R[1, 0]))
    pitch = math.atan2(-float(R[2, 0]), cp)
    if cp > 1e-8:
        yaw = math.atan2(float(R[1, 0]), float(R[0, 0]))
        roll = math.atan2(float(R[2, 1]), float(R[2, 2]))
    else:
        # The requested pitch range stays away from this singularity. Keep a
        # deterministic fallback for a noisy TF sample at the boundary.
        yaw = 0.0
        roll = math.atan2(-float(R[0, 1]), float(R[1, 1]))
    return roll, pitch, yaw


def matrix_from_rpy(roll, pitch, yaw):
    """Build an intrinsic ZYX rotation from roll, pitch, and yaw."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


def main():
    C.check_ros()
    import rclpy

    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as exc:
        C.die(f"参数不是合法 JSON：{exc}")
    target = float(args.get("target_pitch", 0.6))
    arm = args.get("arm")
    cfg = C.load_config()
    timeout_s = float(args.get(
        "timeout_s", cfg["moveit"].get("default_timeout_s", 15.0)
    ))
    if arm not in ("left", "right"):
        C.die("arm 必须显传 left/right", got=arm)
    if not (-1.5 <= target <= 1.5):
        C.die("target_pitch 超限 ±1.5rad", got=target)
    if not (0.02 <= timeout_s <= 300.0):
        C.die("timeout_s 必须在 0.02~300", got=timeout_s)

    rclpy.init()
    node = rclpy.create_node("arm_tools_rotate_pitch")
    try:
        live = C.LiveState(node, cfg)
        if live.wait_joints(timeout_s=5.0) is None:
            C.die("5s 内无 /joint_states（驱动没起？）")
        tcp = live.get_tcp(arm, timeout_s=5.0)
        if tcp is None:
            C.die("读不到 TCP TF（world->hand_tcp）")

        R0 = matrix_from_quat(tcp["quat"])
        roll, _, yaw = rpy_from_matrix(R0)
        target_quat = quat_from_matrix(matrix_from_rpy(roll, target, yaw))

        result = MoveItClient(node, live, cfg).move_tcp(
            arm,
            np.asarray(tcp["xyz"], dtype=np.float64),
            target_quat,
            timeout_s=timeout_s,
            position_tolerance=float(cfg["moveit"]["rotation_position_tolerance_m"]),
            orientation_tolerance=float(cfg["moveit"]["rotation_orientation_tolerance_rad"]),
            start_xyz=tcp["xyz"],
        )
        final_q = result.get("final_quat_wxyz")
        final_pitch = pitch_of_quat(final_q) if final_q is not None else None
        result.update({
            "arm": arm,
            "target_pitch": round(target, 4),
            "final_pitch": round(final_pitch, 4) if final_pitch is not None else None,
            "pitch_error_rad": (
                round(abs(final_pitch - target), 4)
                if final_pitch is not None else None
            ),
        })
        C.emit(result)
        if not result.get("success"):
            sys.exit(1)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
