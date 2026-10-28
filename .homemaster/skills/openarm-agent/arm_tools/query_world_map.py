#!/usr/bin/env python3
"""query_world_map 后端：按高度/区域过滤世界点云并聚类。

用法：python3 query_world_map.py '{"z_min":0.85,"z_max":0.95}'
常用：z 0.85-0.95=台面物；z 0-0.12=地面附近。camera 默认 agentview；
wrist 需显传 arm；navview 拒。x_range/y_range 可选 [min,max]。
体素栅格聚类（cell 2cm），输出按点数排序的团块（最多 50）。
前置：驱动开深度 + 外参已标定，否则直接报错。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C
import perception_common as P

import numpy as np

CELL = 0.02


def main():
    C.check_ros()
    import rclpy
    from sensor_msgs.msg import CameraInfo, Image

    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as e:
        C.die(f"参数不是合法 JSON：{e}")
    z_min = float(args.get("z_min", 0.85))
    z_max = float(args.get("z_max", 0.95))
    x_range = args.get("x_range")
    y_range = args.get("y_range")
    camera = args.get("camera", "agentview")
    arm = args.get("arm")
    min_cluster = int(args.get("min_cluster_size", 10))
    stride = 2 if args.get("resolution", "low") == "high" else 4

    cfg = C.load_config()
    cam = P.resolve_camera(cfg, camera, arm)

    rclpy.init()
    node = rclpy.create_node("arm_tools_worldmap")
    try:
        depth_msg = P.snap_msg(node, Image, cam["depth_topic"], 10.0)
        if depth_msg is None:
            C.die("10s 内无深度图：确认驱动开了 enable_depth",
                  topic=cam["depth_topic"])
        info = P.snap_msg(node, CameraInfo, cam["info_topic"], 10.0)
        if info is None:
            C.die("10s 内无 camera_info", topic=cam["info_topic"])
        depth_m = P.parse_depth(depth_msg)
        H, W = depth_m.shape
        fx, fy, cx, cy = info.k[0], info.k[4], info.k[2], info.k[5]

        rr, cc = np.mgrid[0:H:stride, 0:W:stride]
        d = depth_m[rr, cc]
        ok = np.isfinite(d) & (d > 0.05) & (d < 5.0)
        rr, cc, d = rr[ok], cc[ok], d[ok]
        if d.size == 0:
            C.emit({"success": True, "clusters": [], "points_used": 0})
            return
        pts_cam = np.stack([(cc - cx) * d / fx, (rr - cy) * d / fy, d], axis=1)
        pts = P.to_world(pts_cam, cam["R"], cam["t"])

        keep = (pts[:, 2] >= z_min) & (pts[:, 2] <= z_max)
        if x_range:
            keep &= (pts[:, 0] >= x_range[0]) & (pts[:, 0] <= x_range[1])
        if y_range:
            keep &= (pts[:, 1] >= y_range[0]) & (pts[:, 1] <= y_range[1])
        pts = pts[keep]
        if pts.shape[0] == 0:
            C.emit({"success": True, "clusters": [], "points_used": 0})
            return

        keys = np.floor(pts / CELL).astype(np.int64)
        ukeys, inverse, counts = np.unique(keys, axis=0, return_inverse=True,
                                           return_counts=True)
        clusters = []
        for i, c in enumerate(counts):
            if c < min_cluster:
                continue
            members = pts[inverse == i]
            clusters.append({
                "center": [round(float(v), 3) for v in members.mean(axis=0)],
                "count": int(c),
            })
        clusters.sort(key=lambda c: -c["count"])
        C.emit({"success": True, "camera": cam["key"],
                "clusters": clusters[:50],
                "points_used": int(pts.shape[0])})
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
