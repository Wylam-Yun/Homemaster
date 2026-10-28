import unittest
import numpy as np
from joint_tracking import fixed_target_tracking

class TrackingTests(unittest.TestCase):
    def test_bias_compensates_all_joints_against_fixed_goal(self):
        target=np.zeros(7); bias=np.array([.18,0,0,.17,0,0,.18]); calls=[]
        def execute(command):
            calls.append(command.copy())
            return {'moveit_error_code':1,'final_joints':(command-bias).tolist()}
        r=fixed_target_tracking(target,bias,execute,iterations=3)
        self.assertTrue(r['success']);np.testing.assert_allclose(calls[0],bias)
        np.testing.assert_allclose(target,np.zeros(7))
    def test_controller_failure_never_becomes_success(self):
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),lambda q:{'moveit_error_code':-4,'final_joints':q.tolist()})
        self.assertFalse(r['success']);self.assertEqual(r['convergence_stop_reason'],'moveit_failed')
    def test_contact_does_not_keep_adding_force(self):
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),lambda q:{'moveit_error_code':1,'final_joints':[.04]*7})
        self.assertFalse(r['success']);self.assertEqual(r['iterations'],2)
        self.assertEqual(r['convergence_stop_reason'],'residual_not_decreasing')
    def test_every_joint_must_pass(self):
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),lambda q:{'moveit_error_code':1,'final_joints':[0]*6+[.04]},iterations=1)
        self.assertFalse(r['success'])
    def test_over_limit_bias_rejected_before_motion(self):
        def forbidden(q):raise AssertionError('must not move')
        r=fixed_target_tracking(np.zeros(7),np.array([.31]+[0]*6),forbidden)
        self.assertFalse(r['success']);self.assertEqual(r['iterations'],0)
    def test_single_shot_never_retries(self):
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),lambda q:{'moveit_error_code':1,'final_joints':[.04]*7},iterations=1)
        self.assertEqual(r['iterations'],1)
    def test_correction_is_bounded(self):
        calls=[]
        def execute(q):
            calls.append(q.copy());return {'moveit_error_code':1,'final_joints':(q-.12).tolist()}
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),execute)
        self.assertFalse(r['success']);self.assertLessEqual(np.max(abs(calls[1]-calls[0])),.08+1e-9)

    def test_controller_abort_cannot_be_accepted_by_pose(self):
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),lambda q:{'moveit_error_code':1,'controller_error_code':-4,'action_status':6,'final_joints':q.tolist()},accept=lambda r:True)
        self.assertFalse(r['success']);self.assertEqual(r['convergence_stop_reason'],'controller_failed')
    def test_tcp_goal_accepts_a_verified_equivalent_joint_solution(self):
        r=fixed_target_tracking(np.zeros(7),np.zeros(7),lambda q:{'moveit_error_code':1,'controller_error_code':0,'action_status':4,'final_joints':[.02]*7},accept=lambda r:True)
        self.assertTrue(r['success']);self.assertEqual(r['iterations'],1)

if __name__=='__main__':unittest.main()
