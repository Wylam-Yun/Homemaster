#!/usr/bin/env python3
"""back_project_batch 后端：像素批量反投影到世界坐标。

用法：python3 back_project_batch.py '{"pixels":[[120,160],[130,165]],"camera":"agentview"}'
pixels: [[row,col],...] 最多 50 个；camera: agentview / wrist(需 arm) / navview(拒)；
step: 只支持最新（传数字直接拒）；resolution: high/low（记录用，当前用驱动发布的流）。
每点取 3x3 中值深度抗噪；输出逐点坐标（无效为 null）+ 有效点中值。
前置：驱动开深度（realsense enable_depth:=true）+ 外参已标定，否则直接报错。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C
import perception_common as P

import numpy as np


def main():
    C.check_ros()
    import rclpy
    from sensor_msgs.msg import CameraInfo, Image

    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as e:
        C.die(f"参数不是合法 JSON：{e}")
    pixels = args.get("pixels")
    camera = args.get("camera", "agentview")
    arm = args.get("arm")
    step = args.get("step")
    if step is not None:
        C.die("step 回放未实现：当前只支持最新帧", got=step)
    if not (isinstance(pixels, list) and 1 <= len(pixels) <= 50):
        C.die("pixels 必须是 1~50 个 [row,col]", got=pixels)

    cfg = C.load_config()
    cam = P.resolve_camera(cfg, camera, arm)

    rclpy.init()
    node = rclpy.create_node("arm_tools_backproject")
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

        pts = []
        for rc in pixels:
            r, cc = int(rc[0]), int(rc[1])
            if not (0 <= r < H and 0 <= cc < W):
                pts.append(None)
                continue
            patch = depth_m[max(0, r - 1):r + 2, max(0, cc - 1):cc + 2].ravel()
            valid = patch[np.isfinite(patch)]
            if valid.size == 0:
                pts.append(None)
                continue
            d = float(np.median(valid))
            fx, fy, cx, cy = info.k[0], info.k[4], info.k[2], info.k[5]
            xyz_cam = np.array([(cc - cx) * d / fx, (r - cy) * d / fy, d])
            w = P.to_world(xyz_cam.reshape(1, 3), cam["R"], cam["t"])[0]
            pts.append([round(float(v), 4) for v in w])

        valid_pts = np.array([p for p in pts if p], dtype=np.float64)
        median = ([round(float(v), 4) for v in np.median(valid_pts, axis=0)]
                  if valid_pts.size else None)
        C.emit({"success": True, "camera": cam["key"],
                "resolution": args.get("resolution", "low"),
                "points": pts, "median": median,
                "valid": int(valid_pts.shape[0]), "total": len(pts)})
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
