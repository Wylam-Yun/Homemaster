#!/usr/bin/env python3
"""Move one or both arms to the configured hands_up safety posture."""
import json
import os
import sys

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
    arm = args.get("arm")
    timeout_s = float(args.get("timeout_s", 30.0))
    if arm not in ("left", "right", "both"):
        C.die("arm 必须是 left/right/both", got=arm)
    if not (1.0 <= timeout_s <= 300.0):
        C.die("timeout_s 必须在 1~300", got=timeout_s)

    cfg = C.load_config()
    safe = cfg["moveit"]["safe_states"]["hands_up"]
    sides = ("left", "right") if arm == "both" else (arm,)

    rclpy.init()
    node = rclpy.create_node("arm_tools_reset")
    try:
        live = C.LiveState(node, cfg)
        client = MoveItClient(node, live, cfg)
        results = []
        for side in sides:
            result = client.move_joints(
                side,
                safe[side],
                timeout_s=timeout_s,
            )
            result.update({"arm": side, "target_state": "hands_up"})
            results.append(result)
            if not result.get("success"):
                C.emit({
                    "success": False,
                    "target_state": "hands_up",
                    "failed_arm": side,
                    "results": results,
                })
                sys.exit(1)
        C.emit({
            "success": True,
            "target_state": "hands_up",
            "results": results,
        })
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
