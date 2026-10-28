#!/usr/bin/env python3
"""set_gripper 后端：原地开合夹爪，手臂不动。

用法：python3 set_gripper.py '{"arm":"right","state":"open"}'
只打 GripperCommand Action，不发布任何手臂话题。
`release` 由 TS 层固定 state=open 调用本脚本。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arm_common as C



def current_width(node, live, side, cfg):
    """从 joint_states 按名找夹爪开口；找不到返回 None（调用方用对端兜底）。"""
    msg = live.joint_msg or live.wait_joints(timeout_s=3.0)
    if msg is None:
        return None
    for n, p in zip(msg.name, msg.position):
        ln = n.lower()
        if ("grip" in ln or "finger" in ln) and side in ln:
            return float(p)
    return None


def drive_gripper(node, live, side, target_m, cfg):
    """下发一个 GripperCommand 目标，返回 (ok, reached_width)。"""
    from control_msgs.action import GripperCommand
    from rclpy.action import ActionClient

    topic = (
        cfg["topics"]["gripper_left"]
        if side == "left"
        else cfg["topics"]["gripper_right"]
    )
    client = ActionClient(node, GripperCommand, topic)
    if not client.wait_for_server(timeout_sec=5.0):
        return False, None

    start = current_width(node, live, side, cfg)
    if start is None:  # 读不到就从另一端插值，结果一致，只是多走几步
        g = cfg["gripper"]
        start = g["open_m"] if target_m < g["open_m"] / 2 else g["closed_m"]
    goal = GripperCommand.Goal()
    goal.command.position = float(target_m)
    goal.command.max_effort = float(cfg["gripper"]["max_effort"])
    fut = client.send_goal_async(goal)
    rclpy = C.rclpy
    rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
    if not fut.done():
        return False, start
    handle = fut.result()
    if handle is None or not handle.accepted:
        return False, start
    res_fut = handle.get_result_async()
    rclpy.spin_until_future_complete(node, res_fut, timeout_sec=10.0)
    if not res_fut.done():
        cancel_fut = handle.cancel_goal_async()
        rclpy.spin_until_future_complete(node, cancel_fut, timeout_sec=2.0)
        return False, start
    res = res_fut.result()
    if res is None:
        return False, start
    width = float(res.result.position)
    return bool(res.result.reached_goal), width


def main():
    C.check_ros()
    import rclpy

    try:
        args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    except json.JSONDecodeError as e:
        C.die(f"参数不是合法 JSON：{e}")
    arm = args.get("arm")
    state = args.get("state")
    if arm not in ("left", "right"):
        C.die("arm 必须显传 left/right", got=arm)
    if state not in ("open", "close"):
        C.die("state 只能是 open/close", got=state)

    cfg = C.load_config()
    target = cfg["gripper"]["closed_m"] if state == "close" else cfg["gripper"]["open_m"]
    rclpy.init()
    node = rclpy.create_node("arm_tools_gripper")
    try:
        live = C.LiveState(node, cfg)
        ok, width = drive_gripper(node, live, arm, target, cfg)
        C.emit({"success": ok, "arm": arm, "state": state, "width": width})
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
