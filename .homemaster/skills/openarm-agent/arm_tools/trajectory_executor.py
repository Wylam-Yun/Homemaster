"""MoveIt plans physical motion; an existing controller executes continuous commands."""
import copy
import time
import xml.etree.ElementTree as ET
import numpy as np
import arm_common as C
from trajectory_compensation import compensate_positions


def call_service(client,typ,name,request):
    srv=client.node.create_client(typ,name)
    try:
        if not srv.wait_for_service(timeout_sec=3):raise RuntimeError(name+' unavailable')
        future=srv.call_async(request)
        C.rclpy.spin_until_future_complete(client.node,future,timeout_sec=8)
        if not future.done() or future.result() is None:raise RuntimeError(name+' timeout')
        return future.result()
    finally:client.node.destroy_client(srv)


def joint_limits(client,names):
    from rcl_interfaces.srv import GetParameters
    req=GetParameters.Request();req.names=['robot_description']
    result=call_service(client,GetParameters,client.cfg['moveit']['model_parameters_service'],req)
    root=ET.fromstring(result.values[0].string_value)
    bounds={}
    for joint in root.findall('joint'):
        if joint.attrib['name'] in names:
            lim=joint.find('limit')
            if lim is None:raise RuntimeError('joint limit missing')
            bounds[joint.attrib['name']]=(float(lim.attrib['lower']),float(lim.attrib['upper']))
    return np.array([bounds[n] for n in names])


def plan(client,side,target,actual,velocity):
    from moveit_msgs.srv import GetMotionPlan
    from moveit_msgs.msg import JointConstraint
    from tracking_ros import log
    names=C.LEFT_JOINT_NAMES if side=='left' else C.RIGHT_JOINT_NAMES
    req=GetMotionPlan.Request();r=req.motion_plan_request
    r.group_name=client.cfg['moveit'][f'group_{side}'];r.start_state.joint_state=copy.deepcopy(client.live.joint_msg)
    r.allowed_planning_time=3.;r.num_planning_attempts=1
    r.max_velocity_scaling_factor=float(velocity);r.max_acceleration_scaling_factor=float(velocity)
    constraints=client.Constraints()
    for name,q in zip(names,target):
        j=JointConstraint();j.joint_name=name;j.position=float(q)
        j.tolerance_above=.0002;j.tolerance_below=.0002;j.weight=1.
        constraints.joint_constraints.append(j)
    r.goal_constraints=[constraints]
    res=call_service(client,GetMotionPlan,client.cfg['moveit']['planning_service'],req).motion_plan_response
    log(client,'trajectory_plan',arm=side,code=res.error_code.val,target=target.tolist())
    if res.error_code.val!=1:raise RuntimeError(f'plan failed code={res.error_code.val}')
    tr=res.trajectory.joint_trajectory
    idx=[list(tr.joint_names).index(n) for n in names]
    for p in tr.points:
        p.positions=[p.positions[i] for i in idx]
        if p.velocities:p.velocities=[p.velocities[i] for i in idx]
        if p.accelerations:p.accelerations=[p.accelerations[i] for i in idx]
    tr.joint_names=names
    if len(tr.points)<2:raise RuntimeError('planner returned fewer than two points')
    positions=np.array([p.positions for p in tr.points])
    if np.max(abs(positions[0]-actual))>.003:raise RuntimeError('planned start differs from actual state')
    if np.max(abs(positions[-1]-target))>.001:raise RuntimeError('planned endpoint differs from fixed target')
    limit=client.cfg['moveit']['joint_tracking']['max_target_delta_rad']
    if np.max(abs(positions-actual))>limit:raise RuntimeError('planned path exceeds joint excursion bound')
    return tr,positions


def execute(client,side,target,end_bias,timeout,velocity):
    from control_msgs.action import FollowJointTrajectory
    from control_msgs.msg import JointTolerance,JointTrajectoryControllerState
    from rclpy.action import ActionClient
    from tracking_ros import stable_state,log
    actual,desired=stable_state(client,side)
    tr,physical=plan(client,side,target,actual,velocity)
    bounds=joint_limits(client,tr.joint_names)
    qnow,unow=stable_state(client,side)
    if np.max(abs(qnow-actual))>.003 or np.max(abs(unow-desired))>.003:
        raise RuntimeError('robot changed while planning')
    b0=desired-actual;cfg=client.cfg['moveit']['joint_tracking']
    times=np.array([p.time_from_start.sec+p.time_from_start.nanosec*1e-9 for p in tr.points])
    times-=times[0]
    if times[-1]<=0:raise RuntimeError('invalid plan duration')
    # Slow the existing path without altering its geometry; preserve zero end velocities.
    scale=max(1.,cfg['min_duration_s']/times[-1]);times*=scale
    command=compensate_positions(physical,times,b0,end_bias,cfg['max_bias_rad'])
    if np.any(command<bounds[:,0]) or np.any(command>bounds[:,1]):raise RuntimeError('compensated command outside model joint limits')
    if np.max(abs(command[0]-desired))>1e-9:raise RuntimeError('holding command discontinuity')
    duration=times[-1]
    for p,t,u in zip(tr.points,times,command):
        p.positions=list(map(float,u));s=float(t/duration)
        hp=(30*s*s-60*s**3+30*s**4)/duration
        hpp=(60*s-180*s*s+120*s**3)/(duration*duration)
        if p.velocities:p.velocities=(np.array(p.velocities)/scale+hp*(end_bias-b0)).tolist()
        if p.accelerations:p.accelerations=(np.array(p.accelerations)/(scale*scale)+hpp*(end_bias-b0)).tolist()
        p.effort=[]
        ns=round(float(t)*1e9);p.time_from_start.sec=ns//10**9;p.time_from_start.nanosec=ns%10**9
    tr.header.stamp.sec=0;tr.header.stamp.nanosec=0
    goal=FollowJointTrajectory.Goal();goal.trajectory=tr
    for n,b in zip(tr.joint_names,np.maximum(abs(b0),abs(end_bias))):
        tol=JointTolerance();tol.name=n;tol.position=float(cfg['max_bias_rad']+cfg['tracking_allowance_rad'])
        goal.path_tolerance.append(tol);goal.goal_tolerance.append(copy.deepcopy(tol))
    goal.goal_time_tolerance.sec=2
    initial_tcp=client.live.get_tcp(side)
    if initial_tcp is None:raise RuntimeError('initial TCP unavailable')
    client._last_action_samples=[];samples=client._last_action_samples
    def cb(msg):
        idx=[list(msg.joint_names).index(n) for n in tr.joint_names]
        samples.append({'stamp':msg.header.stamp.sec+msg.header.stamp.nanosec*1e-9,
                        'actual':np.array(msg.actual.positions)[idx].tolist(),
                        'desired':np.array(msg.desired.positions)[idx].tolist()})
    sub=client.node.create_subscription(JointTrajectoryControllerState,client.cfg['topics'][f'controller_state_{side}'],cb,30)
    action=ActionClient(client.node,FollowJointTrajectory,client.cfg['moveit'][f'trajectory_action_{side}'])
    try:
        if not action.wait_for_server(timeout_sec=3):raise RuntimeError('trajectory controller unavailable')
        log(client,'continuous_trajectory',arm=side,physical_positions=physical.tolist(),command_positions=command.tolist(),times=times.tolist(),bias_start=b0.tolist(),bias_end=end_bias.tolist())
        sent=action.send_goal_async(goal);C.rclpy.spin_until_future_complete(client.node,sent,timeout_sec=3)
        if not sent.done() or sent.result() is None or not sent.result().accepted:raise RuntimeError('trajectory rejected or unavailable')
        handle=sent.result();future=handle.get_result_async();start=time.monotonic();minimum_z=initial_tcp['xyz'][2]
        last_log=0;stop_reason=None
        while not future.done() and time.monotonic()-start<max(timeout,duration+3):
            C.rclpy.spin_once(client.node,timeout_sec=.01)
            # Latest TF is independently derived from measured joints.
            tf=client.live.lookup_tf(client.cfg['frames']['world'],client.cfg['frames'][f'tcp_{side}'],.05)
            if tf:
                z=tf.transform.translation.z;minimum_z=min(minimum_z,z)
                # During the first 0.5 seconds every move must preserve support.
                if time.monotonic()-start<.5 and z<initial_tcp['xyz'][2]-cfg['max_start_drop_m']:
                    stop_reason='unexpected_start_drop';break
            for row in samples[last_log:]:log(client,'controller_sample',arm=side,**row)
            last_log=len(samples)
        if not future.done():
            hold_result=hold_continuous(client,action,tr.joint_names,command,times,start,cfg)
            log(client,'trajectory_hold',arm=side,reason=stop_reason or 'timeout',result=hold_result)
            return {'success':False,'moveit_error_code':1,'controller_error_code':None,'error':stop_reason or 'controller timeout','hold':hold_result}
        outcome=future.result();code=int(outcome.result.error_code)
        if code!=0 or outcome.status!=4:
            hold_result=hold_continuous(client,action,tr.joint_names,command,times,start,cfg)
            log(client,'trajectory_hold',arm=side,reason='controller_abort',result=hold_result)
        for row in samples[last_log:]:log(client,'controller_sample',arm=side,**row)
        q,d=stable_state(client,side)
        result={'success':code==0 and outcome.status==4,'moveit_error_code':1,'controller_error_code':code,
                'action_status':outcome.status,'error':None if code==0 and outcome.status==4 else outcome.result.error_string,
                'final_joints':q.tolist(),'controller_desired':d.tolist(),
                'initial_tcp_z':initial_tcp['xyz'][2],'minimum_tcp_z':minimum_z,
                'start_command_jump_rad':float(np.max(abs(command[0]-desired))),
                'controller_sample_count':len(samples)}
        log(client,'trajectory_result',arm=side,**result)
        return result
    finally:
        client.node.destroy_subscription(sub);action.destroy()


def hold_continuous(client,action,names,command,times,start,cfg):
    """Preempt with the last planned support command, never an actual-angle reset."""
    from control_msgs.action import FollowJointTrajectory
    from control_msgs.msg import JointTolerance
    from trajectory_msgs.msg import JointTrajectoryPoint
    elapsed=min(max(0.,time.monotonic()-start),float(times[-1]))
    u=[float(np.interp(elapsed,times,command[:,i])) for i in range(7)]
    goal=FollowJointTrajectory.Goal();goal.trajectory.joint_names=names
    for ns in (0,500000000):
        p=JointTrajectoryPoint();p.positions=u;p.velocities=[0.]*7;p.accelerations=[0.]*7
        p.time_from_start.nanosec=ns;goal.trajectory.points.append(p)
    for name in names:
        t=JointTolerance();t.name=name;t.position=float(cfg['max_bias_rad']+cfg['tracking_allowance_rad'])
        goal.path_tolerance.append(t);goal.goal_tolerance.append(copy.deepcopy(t))
    sent=action.send_goal_async(goal);C.rclpy.spin_until_future_complete(client.node,sent,timeout_sec=1)
    if not sent.done() or sent.result() is None or not sent.result().accepted:
        return {'success':False,'error':'support hold rejected'}
    done=sent.result().get_result_async();C.rclpy.spin_until_future_complete(client.node,done,timeout_sec=2)
    if not done.done():return {'success':False,'error':'support hold timeout'}
    answer=done.result()
    return {'success':answer.status==4 and answer.result.error_code==0,'controller_error_code':answer.result.error_code,'positions':u}
