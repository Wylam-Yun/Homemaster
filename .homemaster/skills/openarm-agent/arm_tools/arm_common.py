"""arm_tools 共享 helpers：ROS 初始化、joint/TF/图像快照。

本文件只读不控：不创建任何 publisher / action client。
运行前提：source RBM_openarm/source_all.sh + conda activate openarm。
"""
import json
import os
import sys
import time

import numpy as np

try:
    import rclpy
    from rclpy.node import Node
    from sensor_msgs.msg import CompressedImage, JointState
    from tf2_ros import Buffer, TransformException, TransformListener
    _ROS_OK = True
except ImportError:
    rclpy = None
    _ROS_OK = False

# 与 openarm_vr_ik_teleop/urdf_utils.py 一致（硬编码，避免 view/gripper 依赖 workspace 包）
LEFT_JOINT_NAMES = [f"openarm_left_joint{i}" for i in range(1, 8)]
RIGHT_JOINT_NAMES = [f"openarm_right_joint{i}" for i in range(1, 8)]


def repo_dir():
    return os.path.dirname(os.path.abspath(__file__))


def load_config():
    import yaml
    path = os.path.join(repo_dir(), "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def emit(obj):
    print(json.dumps(obj), flush=True)


def die(msg, **extra):
    out = {"success": False, "error": msg}
    out.update(extra)
    emit(out)
    sys.exit(1)


def check_ros():
    if not _ROS_OK:
        die("ROS 环境未加载：请先 source source_all.sh + conda activate openarm")


class LiveState:
    """缓存 joint_states + TF。只订阅不发布。"""

    def __init__(self, node, cfg):
        self.node = node
        self.cfg = cfg
        self.joint_msg = None
        self.tf = Buffer()
        self.tf_listener = TransformListener(self.tf, node)
        self.sub = node.create_subscription(
            JointState, cfg["topics"]["joint_states"], self._cb, 10
        )

    def _cb(self, msg):
        self.joint_msg = msg

    def _spin_wait(self, cond, timeout_s, poll=0.02):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            rclpy.spin_once(self.node, timeout_sec=0.0)
            v = cond()
            if v is not None:
                return v
            time.sleep(poll)
        return None

    def wait_joints(self, timeout_s=5.0):
        return self._spin_wait(lambda: self.joint_msg, timeout_s)

    def arm_q(self, side):
        """从缓存的 joint_states 取单臂 7 关节角（弧度），缺失返回 None。"""
        if self.joint_msg is None:
            return None
        names = LEFT_JOINT_NAMES if side == "left" else RIGHT_JOINT_NAMES
        idx = {n: i for i, n in enumerate(self.joint_msg.name)}
        try:
            return np.array(
                [float(self.joint_msg.position[idx[n]]) for n in names],
                dtype=np.float64,
            )
        except KeyError:
            return None

    def lookup_tf(self, target, source, timeout_s=5.0):
        def _try():
            try:
                return self.tf.lookup_transform(target, source, rclpy.time.Time())
            except TransformException:
                return None

        return self._spin_wait(_try, timeout_s)

    def get_tcp(self, side, timeout_s=5.0):
        """取 world 下末端位姿，返回 {xyz, quat[w,x,y,z]}，失败返回 None。"""
        frames = self.cfg["frames"]
        tcp = frames["tcp_left"] if side == "left" else frames["tcp_right"]
        t = self.lookup_tf(frames["world"], tcp, timeout_s)
        if t is None:
            return None
        p, q = t.transform.translation, t.transform.rotation
        return {
            "xyz": [p.x, p.y, p.z],
            "quat": [q.w, q.x, q.y, q.z],
        }

    def snap_image(self, topic, timeout_s=6.0):
        """取一帧 CompressedImage，超时返回 None。"""
        box = {}
        sub = self.node.create_subscription(
            CompressedImage, topic, lambda m: box.setdefault("m", m), 10
        )
        try:
            return self._spin_wait(lambda: box.get("m"), timeout_s)
        finally:
            self.node.destroy_subscription(sub)
