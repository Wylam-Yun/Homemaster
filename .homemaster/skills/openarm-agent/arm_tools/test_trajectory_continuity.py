"""Regression: replacing a holding setpoint with measured q unloads the arm."""
import unittest
import numpy as np
from trajectory_compensation import compensate_positions

class ContinuityTests(unittest.TestCase):
    def test_first_command_preserves_measured_holding_setpoint(self):
        q0=np.array([-.1836805,-.0307088,.0387198,1.5547036,-.0444419,.0108721,-.2679866])
        u0=np.array([-.0319198,-.0074302,.0073424,1.8068164,-.0034965,.0081071,-.0963451])
        path=np.stack([q0,q0+.01,q0+.02]);original=path.copy()
        out=compensate_positions(path,[0,1,2],u0-q0,u0-q0)
        np.testing.assert_allclose(out[0],u0,atol=1e-10)
        np.testing.assert_allclose(out[-1],u0+.02,atol=1e-10)
        np.testing.assert_array_equal(path,original)
    def test_changed_bias_has_no_start_jump(self):
        path=np.zeros((5,7));b0=np.ones(7)*.18;b1=np.ones(7)*.22
        out=compensate_positions(path,[0,.5,1,1.5,2],b0,b1)
        np.testing.assert_allclose(out[0],b0);np.testing.assert_allclose(out[-1],b1)
        self.assertTrue(np.all(out>=b0));self.assertTrue(np.all(out<=b1))
    def test_each_joint_bias_is_bounded(self):
        with self.assertRaises(ValueError):compensate_positions(np.zeros((2,7)),[0,1],np.zeros(7),np.array([0]*6+[.31]))
    def test_invalid_time_and_nonfinite_rejected(self):
        for times in ([1,0],[0,0],[0,float('nan')]):
            with self.assertRaises(ValueError):compensate_positions(np.zeros((2,7)),times,np.zeros(7),np.zeros(7))

if __name__=='__main__':unittest.main()
