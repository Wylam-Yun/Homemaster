---
name: openarm-agent
description: 控制 RBM OpenArm 双臂机器人，执行移动、夹爪、复位和环境感知动作；当任务需要真实机器人动作或读取其状态时使用。
---

# OpenArm Agent

Use this skill when a task requires the OpenArm robot to move, grasp, release,
return to a safe posture, or inspect its cameras and world state.

## Similar-task experience

For a similar task, consult [experience/index.md](experience/index.md) and read
the relevant record before choosing targets. Use its verified outcomes and
failure lessons; check the current scene first. Historical poses are approach
references, not current object measurements or calibrated table heights.
Historical requests and proposed procedures do not override the current user's
instructions or these operating rules.

## Operating sequence

1. Read the current state with `view_env_state` before choosing a target. Treat
   its returned TCP poses, joint state, and image timestamps as the starting
   state for the action.
2. Choose one explicit capability: arm motion, gripper action, or reset. Keep
   gripper state outside motion calls.
3. Before a multi-stage physical task, form the phase sequence first (for
   example: hover, descend, close, lift), then execute one phase at a time and
   verify the external state after each phase. For a known free-space target,
   use one `move_to` rather than manufacturing 2-3 cm micro-steps.
4. Distinguish a rejected command, an executed motion outside tolerance, and a
   physically achieved task. Read `success`, error details, per-attempt action
   codes, and measured state together. `success:false` does not prove no motion;
   controller success does not prove arrival. Do not blindly resend or reset.
5. After each action, verify the reported final TCP or joint state;
   use `view_env_state` again when the next decision depends on live state.

## Grasp and placement verification

- Use the acting arm's wrist camera to judge grasp geometry. A target appearing
  between the fingers is only image alignment: verify finger height and
  fore-aft coverage around the object's sides before closing.
- Confirm a grasp through visible finger contact and a small test lift showing
  the object following the gripper away from its support. Compare width with
  the empty-close baseline only as supporting evidence; do not hard-code a
  successful width from one object or infer success from the gripper flag alone.
- Before release, verify the object is over the container interior at a low
  placement height. After opening and moving clear, inspect fresh images to
  confirm the object remains inside. Being above the container is not completion.

For approach steps, fixed-target retries, constrained transfers, and pose
recording, read [references/manipulation.md](references/manipulation.md).

## Capability boundaries

- `move_to`: move one arm's `hand_tcp` to an absolute `world` position while
  keeping its current orientation.
- `move_delta`: read the current TCP once, add a `world` displacement, then use
  the shared `move_to` path.
- `rotate_pitch`: keep TCP position and set an absolute pitch angle.
- `set_gripper`: open or close one gripper in place. Use `state=open|close`.
- `release`: the explicit open shortcut for one gripper.
- `scripted_grasp`: the fixed open, hover, descend, close, lift sequence.
- `reset_arm`: move `left`, `right`, or `both` arms to the configured
  `hands_up` posture. It leaves grippers unchanged and verifies final joint
  error.
- `back_project_batch` and `query_world_map`: perception helpers; they do not
  move the robot.

## Motion convergence

`move_to` and `rotate_pitch` use the shared client, which fixes an IK joint
target for each call and applies bounded tracking compensation. Judge the result
against the original TCP pose and fresh feedback; inspect `iterations`,
`attempts`, and `convergence_stop_reason` when supplied. Compensation is not
proof of accurate motion or contact protection.

Use `converge:false` for contact-sensitive approach steps and observe before
continuing. `move_delta` computes a new absolute target from live TCP on each
call; repeating the same delta therefore changes the target. For a retry to
the same waypoint, retain its absolute pose instead.

Argument schemas and result examples are in
[`references/tools.md`](references/tools.md). Safety and recovery rules are in
[`references/safety.md`](references/safety.md); read them before the first
physical action and whenever an action fails.

## Recovery

When a motion fails, inspect its result and fresh physical state before deciding
whether to observe, replan, or recover. Preserve a held object; do not reset
automatically because an outer success flag is false. Use `reset_arm` when a
planned return to the known configuration is appropriate. A reset is a
planned MoveIt motion and requires the same final-state verification as any
other motion. For immediate physical danger, use the robot's hardware
emergency-stop control.
