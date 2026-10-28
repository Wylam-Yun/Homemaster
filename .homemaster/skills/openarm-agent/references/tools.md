# Tool Reference

All coordinates are metres. `arm` is required for arm-specific actions.

| Tool | Required arguments | Notes |
| --- | --- | --- |
| `view_env_state` | none | Read-only TCP, joints, and camera snapshots. |
| `move_to` | `arm`, `xyz: [x,y,z]` | Absolute `world` TCP target; orientation stays unchanged. Optional `converge=false` disables tracking correction for this call. |
| `move_delta` | `arm`, `dxyz: [dx,dy,dz]` | Relative `world` displacement from one captured TCP pose. |
| `rotate_pitch` | `arm`, `target_pitch` | Absolute pitch in radians; TCP position stays fixed. |
| `set_gripper` | `arm`, `state: open\|close` | Gripper-only action; no arm motion. |
| `release` | `arm` | Equivalent to `set_gripper(state=open)`. |
| `scripted_grasp` | `arm`, `xyz`, `approach_z`, `grasp_z_offset` | Fixed multi-stage grasp sequence. |
| `reset_arm` | `arm: left\|right\|both` | Move to configured `hands_up`; gripper is unchanged. |
| `back_project_batch` | camera and pixel batch | Perception only; returns world points. |
| `query_world_map` | camera and map filters | Perception only; returns filtered clusters. |

`timeout_s` is optional on action tools and controls the primary action wait.
The default for motion tools is 15 seconds. Each tracking attempt uses the
provided timeout; a call with multiple attempts can take longer. Separate
phases do not share one timeout. `converge=false` is available on `move_to`; `move_delta` and
`scripted_grasp` contact-prone phases set it internally.
Precision, velocity scaling, MoveIt planning time, and safety limits live in
`arm_tools/config.yaml`; agents do not tune them per call.

Successful motion results include a final state and an error measurement:
`move_to` reports `final_xyz`, `position_error_m`, orientation error, and
convergence attempt telemetry;
`reset_arm` reports `final_joints` and `joint_error_max_rad` for each arm.
