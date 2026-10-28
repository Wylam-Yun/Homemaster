"""Read controller feedback, solve one IK target, execute through MoveIt only."""
import json
import time
from pathlib import Path
import numpy as np
import arm_common as C
from joint_tracking import fixed_target_tracking


def log(client,event,**data):
    dest=Path(C.repo_dir())/'snapshots'/'motion.jsonl'
    dest.parent.mkdir(exist_ok=True)
    with dest.open('a') as f:f.write(json.dumps({'stamp':time.time(),'event':event,**data})+'\n')


def stable_state(client,side):
    from control_msgs.msg import JointTrajectoryControllerState
    names=C.LEFT_JOINT_NAMES if side=='left' else C.RIGHT_JOINT_NAMES
    rows=[]
    def cb(msg):
        try:
            idx=[list(msg.joint_names).index(n) for n in names]
            actual=np.array(msg.actual.positions)[idx]
            desired=np.array(msg.desired.positions)[idx]
            stamp=msg.header.stamp.sec+msg.header.stamp.nanosec*1e-9
            now=client.node.get_clock().now().nanoseconds*1e-9
            if not 0<=now-stamp<.5:return
            if np.all(np.isfinite(actual)) and np.all(np.isfinite(desired)):
                rows.append((time.monotonic(),actual,desired))
        except (ValueError,IndexError):pass
    topic=client.cfg['topics'][f'controller_state_{side}']
    sub=client.node.create_subscription(JointTrajectoryControllerState,topic,cb,10)
    try:
        end=time.monotonic()+4
        while time.monotonic()<end:
            C.rclpy.spin_once(client.node,timeout_sec=.02)
            recent=[r for r in rows if rows[-1][0]-r[0]<.65] if rows else []
            if len(recent)<10 or recent[-1][0]-recent[0][0]<.5:continue
            actual=np.stack([r[1] for r in recent]);desired=np.stack([r[2] for r in recent])
            if np.max(np.ptp(actual,axis=0))>.003 or np.max(np.ptp(desired,axis=0))>.003:continue
            q=client.live.arm_q(side)
            if q is None or np.max(abs(q-actual[-1]))>.003:continue
            return actual[-1],desired[-1]
        raise RuntimeError('controller feedback missing, stale, moving, or inconsistent')
    finally:client.node.destroy_subscription(sub)


def service(client,typ,name,request):
    srv=client.node.create_client(typ,name)
    try:
        if not srv.wait_for_service(timeout_sec=3):raise RuntimeError(f'{name} unavailable')
        start=time.monotonic();future=srv.call_async(request)
        C.rclpy.spin_until_future_complete(client.node,future,timeout_sec=5)
        if not future.done() or future.result() is None:raise RuntimeError(f'{name} timeout')
        result=future.result();log(client,'service_result',service=name,code=result.error_code.val,duration_s=time.monotonic()-start)
        if result.error_code.val!=1:raise RuntimeError(f'{name} code={result.error_code.val}')
        return result
    finally:client.node.destroy_client(srv)


def track_joints(client,side,target,timeout,velocity,converge,accept=None):
    if side not in ('left','right'):return {'success':False,'error':'invalid arm'}
    try:
        actual,desired=stable_state(client,side)
        target=np.asarray(target,dtype=float)
        if target.shape!=(7,) or not np.all(np.isfinite(target)):raise RuntimeError('invalid joint target')
        cfg=client.cfg['moveit']['joint_tracking']
        if np.max(abs(target-actual))>cfg['max_target_delta_rad']:raise RuntimeError('joint target delta exceeds limit')
        bias=desired-actual
        log(client,'tracking_start',arm=side,target=target.tolist(),actual=actual.tolist(),desired=desired.tolist(),bias=bias.tolist())
        def execute(command):
            from trajectory_executor import execute as execute_trajectory
            try:
                return execute_trajectory(client,side,target,command-target,timeout,velocity)
            except (RuntimeError,ValueError) as exc:
                log(client,'execution_rejected',arm=side,error=str(exc))
                return {'success':False,'error':str(exc),'moveit_error_code':None}
        return fixed_target_tracking(target,bias,execute,
            iterations=cfg['max_iters'] if converge else 1,
            tolerance=client.cfg['moveit']['joint_tolerance_rad'],max_bias=cfg['max_bias_rad'],
            max_step=cfg['max_correction_step_rad'],correction_gain=cfg['correction_gain'],accept=accept)
    except (RuntimeError,ValueError) as exc:
        log(client,'tracking_rejected',arm=side,error=str(exc))
        return {'success':False,'error':str(exc)}


def solve_target(client,side,xyz,quat):
    from moveit_msgs.srv import GetPositionIK,GetPositionFK
    names=C.LEFT_JOINT_NAMES if side=='left' else C.RIGHT_JOINT_NAMES
    req=GetPositionIK.Request();ik=req.ik_request
    ik.group_name=client.cfg['moveit'][f'group_{side}']
    ik.robot_state.joint_state=client.live.joint_msg
    ik.avoid_collisions=True;ik.ik_link_name=client.cfg['frames'][f'tcp_{side}']
    ik.pose_stamped.header.frame_id=client.cfg['frames']['world']
    p=ik.pose_stamped.pose
    p.position.x,p.position.y,p.position.z=map(float,xyz)
    p.orientation.w,p.orientation.x,p.orientation.y,p.orientation.z=map(float,quat)
    ik.timeout.sec=2
    log(client,'ik_request',arm=side,xyz=list(map(float,xyz)),quat=list(map(float,quat)))
    response=service(client,GetPositionIK,client.cfg['moveit']['ik_service'],req)
    js=response.solution.joint_state;mapping=dict(zip(js.name,js.position))
    target=np.array([mapping[n] for n in names])
    fk=GetPositionFK.Request();fk.header.frame_id=client.cfg['frames']['world']
    fk.fk_link_names=[ik.ik_link_name];fk.robot_state=response.solution
    answer=service(client,GetPositionFK,client.cfg['moveit']['fk_service'],fk)
    pose=answer.pose_stamped[0].pose
    pos=np.array([pose.position.x,pose.position.y,pose.position.z]);q=[pose.orientation.w,pose.orientation.x,pose.orientation.y,pose.orientation.z]
    if np.linalg.norm(pos-xyz)>.002 or client._orientation_error(q,quat)>.01:
        raise RuntimeError('IK solution failed independent MoveIt FK target check')
    return target


def track_tcp(client,side,xyz,quat,timeout,ptol,otol,velocity,start,converge):
    try:
        if side not in ('left','right'):raise RuntimeError('invalid arm')
        xyz=np.asarray(xyz,dtype=float);quat=client._quat_normalize(quat)
        if xyz.shape!=(3,) or not np.all(np.isfinite(xyz)):raise RuntimeError('invalid xyz')
        current=client.live.get_tcp(side)
        if current is None:raise RuntimeError('TCP unavailable')
        if xyz[2]<client.cfg['limits']['min_z_m'] or np.linalg.norm(xyz-np.array(current['xyz']))>client.cfg['limits']['max_single_move_m']:
            raise RuntimeError('TCP safety limit')
        target=solve_target(client,side,xyz,quat)
        retry=client.cfg['moveit']['converge_enabled'] if converge is None else converge
        def accept_pose(action_result):
            tcp=client.live.get_tcp(side)
            if tcp is None:return False
            pe=float(np.linalg.norm(np.asarray(tcp['xyz'])-xyz))
            oe=client._orientation_error(tcp['quat'],quat)
            action_result.update(position_error_m=pe,orientation_error_rad=oe)
            return pe<=ptol and oe<=otol
        result=track_joints(client,side,target,timeout,velocity,retry,accept_pose)
        # Spin fresh messages after settled joint samples; never accept an old TF.
        stamp=client.node.get_clock().now().nanoseconds
        end=time.monotonic()+2;tf=None
        while time.monotonic()<end:
            C.rclpy.spin_once(client.node,timeout_sec=.02)
            tf=client.live.lookup_tf(client.cfg['frames']['world'],client.cfg['frames'][f'tcp_{side}'],.1)
            if tf and tf.header.stamp.sec*10**9+tf.header.stamp.nanosec>=stamp:break
        if tf is None or tf.header.stamp.sec*10**9+tf.header.stamp.nanosec<stamp:raise RuntimeError('fresh final TF unavailable')
        p=tf.transform.translation;q=tf.transform.rotation
        actual=np.array([p.x,p.y,p.z]);actual_q=[q.w,q.x,q.y,q.z]
        pe=float(np.linalg.norm(actual-xyz));oe=client._orientation_error(actual_q,quat)
        result.update(final_xyz=actual.tolist(),final_quat_wxyz=actual_q,position_error_m=pe,orientation_error_rad=oe,convergence_enabled=bool(retry))
        result['success']=bool(result.get('success') and result.get('moveit_error_code')==1 and pe<=ptol and oe<=otol)
        if not result['success'] and not result.get('error'):result['error']='final TCP outside original pose tolerance'
        log(client,'tcp_result',arm=side,target_xyz=xyz.tolist(),result=result)
        return result
    except (RuntimeError,ValueError,KeyError) as exc:
        log(client,'tcp_rejected',arm=side,error=str(exc))
        return {'success':False,'error':str(exc)}
