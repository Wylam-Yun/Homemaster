"""Read-only validation of live feedback and an upward IK target."""
import json
import rclpy
import arm_common as C
from robot_client import MoveItClient
from tracking_ros import stable_state,solve_target
rclpy.init();node=rclpy.create_node('skill_tracking_check')
try:
 cfg=C.load_config();live=C.LiveState(node,cfg);live.wait_joints();client=MoveItClient(node,live,cfg)
 actual,desired=stable_state(client,'right');tcp=live.get_tcp('right');xyz=list(tcp['xyz']);xyz[2]+=.02
 target=solve_target(client,'right',xyz,tcp['quat'])
 C.emit({'success':True,'actual':actual.tolist(),'bias':(desired-actual).tolist(),'up20_target_xyz':xyz,'ik_target':target.tolist(),'ik_delta':(target-actual).tolist()})
finally:node.destroy_node();rclpy.shutdown()
