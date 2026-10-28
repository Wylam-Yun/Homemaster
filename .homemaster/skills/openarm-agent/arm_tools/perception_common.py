"""感知共用：相机解析、外参、深度反投。纯计算 + 只读订阅，不发控制。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C

import numpy as np


def resolve_camera(cfg, camera, arm=None):
    """camera: agentview(胸前主) / wrist(腕，需显传 arm) / navview(不存在，直接拒)。"""
    t = cfg["topics"]
    ext = cfg.get("camera_extrinsics", {})
    if camera == "agentview":
        key, dtop, itop = "agentview", t["cam_head_depth"], t["cam_head_info"]
    elif camera == "wrist":
        if arm not in ("left", "right"):
            C.die("camera=wrist 时 arm 必须显传 left/right", got=arm)
        key = f"wrist_{arm}"
        dtop = t["cam_wrist_l_depth" if arm == "left" else "cam_wrist_r_depth"]
        itop = t["cam_wrist_l_info" if arm == "left" else "cam_wrist_r_info"]
    elif camera == "navview":
        C.die("无导航相机：当前只有胸前主 + 双腕，navview 不可用")
    else:
        C.die("camera 只能是 agentview/wrist/navview", got=camera)
    e = ext.get(key, {})
    if not e.get("calibrated", False):
        C.die(f"相机外参未标定：请标定 config.yaml camera_extrinsics.{key}", camera=camera)
    return {"key": key, "depth_topic": dtop, "info_topic": itop,
            "t": np.array(e["xyz"], dtype=np.float64),
            "R": quat_to_mat(np.array(e["quat"], dtype=np.float64))}


def quat_to_mat(q):
    w, x, y, z = (float(v) for v in q)
    n = (w * w + x * x + y * y + z * z) ** 0.5 or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def snap_msg(node, msgtype, topic, timeout_s=10.0):
    box = {}
    sub = node.create_subscription(msgtype, topic, lambda m: box.setdefault("m", m), 10)
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            C.rclpy.spin_once(node, timeout_sec=0.0)
            if "m" in box:
                return box["m"]
            time.sleep(0.02)
        return None
    finally:
        node.destroy_subscription(sub)


def parse_depth(msg):
    """Image → 米单位 HxW float 数组，无效为 NaN。支持 16UC1(mm)/32FC1(m)。"""
    h, w = msg.height, msg.width
    if msg.encoding == "16UC1":
        a = np.frombuffer(msg.data, dtype=np.uint16).reshape(h, w).astype(np.float64)
        a[a == 0] = np.nan
        return a / 1000.0
    if msg.encoding == "32FC1":
        a = np.frombuffer(msg.data, dtype=np.float32).reshape(h, w).astype(np.float64)
        a[~(a > 0)] = np.nan
        return a
    C.die("不支持的深度编码", encoding=msg.encoding)


def deproject(depth_m, info, rows, cols):
    """像素 → 光心系 xyz。rows/cols 为等长数组，返回 Nx3（无效行为 NaN）。"""
    fx, fy, cx, cy = info.k[0], info.k[4], info.k[2], info.k[5]
    d = depth_m[rows, cols]
    x = (cols.astype(np.float64) - cx) * d / fx
    y = (rows.astype(np.float64) - cy) * d / fy
    return np.stack([x, y, d], axis=1)


def to_world(pts_cam, R, t):
    return pts_cam @ R.T + t
