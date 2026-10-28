#!/usr/bin/env python3
"""Shared motion interface: MoveIt plans; continuous compensated actions execute.

TCP goals are solved once by MoveIt's IK/FK services. Every physical command
uses the shared bounded tracking and trajectory executor; grippers stay separate.
"""
import math
import time

import numpy as np

import arm_common as C


def moveit_error_detail(code):
    """Return an agent-facing explanation for MoveIt error codes."""
    details = {
        99999: (
            "MoveIt 规划失败：在当前姿态、关节限位和碰撞约束下，"
            "规划器找不到有效的关节解"
        ),
        -1: (
            "MoveIt 规划失败：在当前姿态、关节限位和碰撞约束下，"
            "规划器找不到有效的关节解"
        ),
        -2: "MoveIt 规划失败：生成的运动计划无效",
        -12: "MoveIt 规划失败：目标位姿处于碰撞状态",
        -14: "MoveIt 规划失败：目标不满足路径约束",
        -31: "MoveIt 规划失败：目标位姿没有可用的 IK 关节解",
        -4: "MoveIt 执行失败：控制器没有完成轨迹",
        -6: "MoveIt 执行超时",
        -7: "MoveIt 执行被取消或抢占",
    }
    return details.get(
        int(code),
        f"MoveIt 返回错误码 {int(code)}，未能完成规划或执行",
    )


class MoveItClient:
    def __init__(self, node, live, cfg):
        from moveit_msgs.msg import Constraints
        self.node = node
        self.live = live
        self.cfg = cfg
        self.Constraints = Constraints

    @staticmethod
    def _quat_normalize(q):
        q = np.asarray(q, dtype=np.float64)
        n = float(np.linalg.norm(q))
        if not np.isfinite(n) or n < 1e-9:
            raise ValueError("四元数无效")
        return q / n

    @staticmethod
    def _orientation_error(q_a, q_b):
        a = MoveItClient._quat_normalize(q_a)
        b = MoveItClient._quat_normalize(q_b)
        dot = min(1.0, max(-1.0, abs(float(np.dot(a, b)))))
        return 2.0 * math.acos(dot)

    def move_tcp(self, side, xyz_world, quat_wxyz, timeout_s=15.0,
                 position_tolerance=0.012, orientation_tolerance=0.06,
                 velocity_scaling=0.15, start_xyz=None, converge_enabled=None):
        from tracking_ros import track_tcp
        return track_tcp(self, side, xyz_world, quat_wxyz, timeout_s,
                         position_tolerance, orientation_tolerance,
                         velocity_scaling, start_xyz, converge_enabled)

    def move_joints(self, side, target_joints, timeout_s=30.0,
                    velocity_scaling=0.15):
        from tracking_ros import track_joints
        return track_joints(self, side, target_joints, timeout_s,
                            velocity_scaling, True)


def quat_from_matrix(R):
    """Return a normalized quaternion in [w, x, y, z] order."""
    R = np.asarray(R, dtype=np.float64)
    trace = float(np.trace(R))
    if trace > 0.0:
        s = 0.5 / math.sqrt(trace + 1.0)
        w = 0.25 / s
        x = (R[2, 1] - R[1, 2]) * s
        y = (R[0, 2] - R[2, 0]) * s
        z = (R[1, 0] - R[0, 1]) * s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2])
        w = (R[2, 1] - R[1, 2]) / s
        x = 0.25 * s
        y = (R[0, 1] + R[1, 0]) / s
        z = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = 2.0 * math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2])
        w = (R[0, 2] - R[2, 0]) / s
        x = (R[0, 1] + R[1, 0]) / s
        y = 0.25 * s
        z = (R[1, 2] + R[2, 1]) / s
    else:
        s = 2.0 * math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1])
        w = (R[1, 0] - R[0, 1]) / s
        x = (R[0, 2] - R[2, 0]) / s
        y = (R[1, 2] + R[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z], dtype=np.float64)
    return q / np.linalg.norm(q)


def matrix_from_quat(q):
    w, x, y, z = MoveItClient._quat_normalize(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])
