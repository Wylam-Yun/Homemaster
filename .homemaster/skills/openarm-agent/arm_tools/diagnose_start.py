"""Read-only: compare MoveIt planned first point with active holding command."""
import json
from pathlib import Path
import numpy as np
import rclpy
from moveit_msgs.srv import GetMotionPlan
from moveit_msgs.msg import JointConstraint
import arm_common as C
from robot_client import MoveItClient
from tracking_ros import stable_state,log
rclpy.init();node=rclpy.create_node('skill_start_diagnostic')
try:
 cfg=C.load_config();live=C.LiveState(node,cfg);live.wait_joints();client=MoveItClient(node,live,cfg)
 actual,desired=stable_state(client,'right')
 req=GetMotionPlan.Request();r=req.motion_plan_request;r.group_name='right_arm'
 r.start_state.joint_state=live.joint_msg;r.allowed_planning_time=3.;r.num_planning_attempts=1
 r.max_velocity_scaling_factor=.1;r.max_acceleration_scaling_factor=.1
 constraints=client.Constraints()
 for n,q in zip(C.RIGHT_JOINT_NAMES,desired):
  j=JointConstraint();j.joint_name=n;j.position=float(q);j.tolerance_above=.001;j.tolerance_below=.001;j.weight=1.;constraints.joint_constraints.append(j)
 r.goal_constraints=[constraints]
 srv=node.create_client(GetMotionPlan,'/plan_kinematic_path')
 if not srv.wait_for_service(timeout_sec=3):raise RuntimeError('planner unavailable')
 f=srv.call_async(req);rclpy.spin_until_future_complete(node,f,timeout_sec=6)
 response=f.result().motion_plan_response
 if response.error_code.val!=1:raise RuntimeError(str(response.error_code.val))
 trajectory=response.trajectory.joint_trajectory
 idx=[list(trajectory.joint_names).index(n) for n in C.RIGHT_JOINT_NAMES]
 first=np.array(trajectory.points[0].positions)[idx]
 out={'success':True,'plan_only':True,'moveit_error_code':response.error_code.val,'actual':actual.tolist(),'holding_command':desired.tolist(),'first_planned_command':first.tolist(),'start_command_jump_rad':(first-desired).tolist(),'number_points':len(trajectory.points)}
 log(client,'start_discontinuity',**out);C.emit(out)
finally:node.destroy_node();rclpy.shutdown()
