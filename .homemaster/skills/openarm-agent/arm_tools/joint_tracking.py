"""Fixed joint targets with bounded load-error compensation; no ROS writes here."""
import numpy as np


def fixed_target_tracking(target, bias, execute, *, iterations=3, tolerance=.006,
                          max_bias=.30, max_step=.08, correction_gain=.5, log=None, accept=None):
    target=np.asarray(target,dtype=float).copy()
    bias=np.asarray(bias,dtype=float).copy()
    attempts=[]; previous=None; result={}
    def finish(reason,success=False):
        return {**result,'success':success,'error':None if success else result.get('error') or reason,
                'iterations':len(attempts),'attempts':attempts,
                'target_joints':target.tolist(),'convergence_stop_reason':reason}
    if target.shape!=(7,) or bias.shape!=(7,) or not np.all(np.isfinite(target)) or not np.all(np.isfinite(bias)):
        return finish('invalid_target_or_bias')
    for i in range(iterations):
        if np.any(abs(bias)>max_bias):return finish('joint_compensation_limit')
        command=target+bias
        result=execute(command.copy())
        record={**result,'attempt':i+1,'commanded_joints':command.tolist(),'bias_rad':bias.tolist()}
        attempts.append(record)
        if log:log(record)
        if result.get('moveit_error_code')!=1:return finish('moveit_failed')
        if 'controller_error_code' in result and (result['controller_error_code']!=0 or result.get('action_status')!=4):return finish('controller_failed')
        actual=np.asarray(result.get('final_joints',[]),dtype=float)
        if actual.shape!=(7,) or not np.all(np.isfinite(actual)):return finish('final_joints_unavailable')
        residual=target-actual; error=float(np.max(abs(residual)))
        result['joint_error_max_rad']=error
        record.update(residual_rad=residual.tolist(),joint_error_max_rad=error)
        if (accept(result) if accept is not None else error<=tolerance):return finish('target_reached',True)
        if previous is not None and error>=previous-.0005:return finish('residual_not_decreasing')
        previous=error
        bias+=np.clip(correction_gain*residual,-max_step,max_step)
    return finish('max_iters')
