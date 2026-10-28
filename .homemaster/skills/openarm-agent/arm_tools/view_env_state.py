#!/usr/bin/env python3
"""view_env_state 后端：只读快照，不动机器人。

用法：python3 view_env_state.py '{}'
输出单行 JSON：{success, stamp, q_left[7], q_right[7], tcp_left, tcp_right,
images:{head,wrist_left,wrist_right}（文件路径，缺失为 null）, missing:[...]}
图像存 arm_tools/snapshots/<stamp>_{head,wrist_l,wrist_r}.jpg。
"""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C


def human_summary(snap):
    """人可读摘要，走 stderr，不污染 stdout 的 JSON。"""
    L = []
    L.append(f"========== view_env_state @ {snap['stamp']} ==========")
    L.append("左臂关节(deg): " + " ".join(f"{v * 57.2958:7.2f}" for v in snap["q_left"]))
    L.append("右臂关节(deg): " + " ".join(f"{v * 57.2958:7.2f}" for v in snap["q_right"]))
    for side in ("left", "right"):
        tcp = snap[f"tcp_{side}"]
        if tcp is None:
            L.append(f"{side} TCP: 无 TF")
        else:
            x, y, z = tcp["xyz"]
            L.append(f"{side} TCP xyz(m): {x:+.3f} {y:+.3f} {z:+.3f}")
    L.append("图像:")
    for k, p in snap["images"].items():
        L.append(f"  {k}: {p if p else '缺失'}")
    if snap["missing"]:
        L.append("缺失项: " + ", ".join(snap["missing"]))
    else:
        L.append("缺失项: 无")
    return "\n".join(L)


def main():
    C.check_ros()
    import rclpy

    rclpy.init()
    node = rclpy.create_node("arm_tools_view")
    try:
        cfg = C.load_config()
        live = C.LiveState(node, cfg)
        missing = []

        jmsg = live.wait_joints(timeout_s=5.0)
        if jmsg is None:
            C.die("5s 内无 /joint_states（驱动没起？）")
        ql = live.arm_q("left")
        qr = live.arm_q("right")
        if ql is None or qr is None:
            C.die("joint_states 缺少手臂关节名", names=list(jmsg.name))

        tcp_l = live.get_tcp("left")
        tcp_r = live.get_tcp("right")
        if tcp_l is None:
            missing.append("tcp_left")
        if tcp_r is None:
            missing.append("tcp_right")

        snapdir = os.path.join(C.repo_dir(), "snapshots")
        os.makedirs(snapdir, exist_ok=True)
        stamp = f"{time.time():.3f}".replace(".", "_")
        images = {}
        for key, topic, fname in (
            ("head", cfg["topics"]["cam_head"], f"{stamp}_head.jpg"),
            ("wrist_left", cfg["topics"]["cam_wrist_left"], f"{stamp}_wrist_l.jpg"),
            ("wrist_right", cfg["topics"]["cam_wrist_right"], f"{stamp}_wrist_r.jpg"),
        ):
            msg = live.snap_image(topic, timeout_s=6.0)
            if msg is None:
                images[key] = None
                missing.append(key)
            else:
                p = os.path.join(snapdir, fname)
                with open(p, "wb") as f:
                    f.write(bytes(msg.data))
                images[key] = p

        snap = {
            "success": True,
            "stamp": stamp,
            "q_left": [round(float(v), 4) for v in ql],
            "q_right": [round(float(v), 4) for v in qr],
            "tcp_left": tcp_l,
            "tcp_right": tcp_r,
            "images": images,
            "missing": missing,
        }
        C.emit(snap)
        print(human_summary(snap), file=sys.stderr, flush=True)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
