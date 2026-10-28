# Safety and Recovery

## Before motion

- Confirm the intended arm, target frame (`world`), and target coordinates.
- Read `view_env_state` and check that the driver, joint state, TF, and MoveIt
  action are available.
- Keep the robot workspace clear and ensure no other controller is commanding
  the same arm.

## During motion

- Send one action at a time and wait for its returned result.
- Accept completion only when the external action return code is successful and
  the final TCP or joint state is within the reported tolerance.
- Keep gripper commands separate from `move_to`, `move_delta`, and
  `rotate_pitch`. This makes a failed arm motion unable to silently change the
  gripper.

## Recovery

- On a failed motion, preserve the failure result, inspect live state, and use
  `reset_arm` on the affected arm when a planned recovery is appropriate.
- `reset_arm` targets the configured `moveit.safe_states.hands_up` joints and
  does not open or close either gripper.
- `release` opens a gripper; it is not an arm reset.
- The software tools do not provide an emergency-stop command. For immediate
  danger, press the robot's physical emergency-stop control.
