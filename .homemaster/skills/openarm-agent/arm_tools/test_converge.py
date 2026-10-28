"""Compatibility entry point for the current convergence/continuity regression suite."""
import unittest
from test_joint_tracking import TrackingTests
from test_trajectory_continuity import ContinuityTests
if __name__ == '__main__':
    unittest.main()
