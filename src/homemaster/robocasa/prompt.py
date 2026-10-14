"""Episode prompt for RoboCasa benchmark runs."""

from __future__ import annotations


def build_episode_prompt(
    *,
    task_language: str,
    memory_mode: str,
    max_tool_iterations: int,
    max_invalid_actions: int,
) -> str:
    memory_note = (
        "You have access to your memory tools. Recall relevant experience from "
        "previous RoboCasa episodes before acting, and let successful strategies "
        "be recorded for future episodes."
        if memory_mode == "full"
        else (
            "You may read but not write memory."
            if memory_mode == "readonly"
            else "Memory is disabled for this run."
        )
    )
    return f"""You are controlling a PandaOmron mobile manipulator in a RoboCasa \
simulated kitchen.

TASK: {task_language}

You act through discrete tools; a separate low-level controller (RLDX-1 VLA) \
executes continuous motor commands for you. You never emit raw motor vectors.

PRIMARY LOOP:
1. `robocasa_observe` to look at the scene (agentview + wrist cameras).
2. `robocasa_skill` runs the VLA closed-loop on the live task language — this \
is the main way to make real progress. It can also move the base.
3. `robocasa_arm` is the base-clamped variant for fine adjustment when you are \
already aligned with the target.

LOCALIZATION & RECOVERY:
- Use `robocasa_back_project` (pixel -> world XYZ) and `robocasa_world_map` \
(object clusters by height) to find where things are.
- If `robocasa_skill` stalls or misses the target, reposition with \
`robocasa_navigate` (base) and `robocasa_move_to`/`robocasa_move_delta` (arm), \
then retry the skill or use `robocasa_scripted_grasp` as a grasp fallback.
- `robocasa_rotate_pitch`, `robocasa_set_gripper`, `robocasa_release`, \
`robocasa_move_base` are fine-grained manual controls.

COMPLETION:
- Only the environment decides success. `robocasa_verify` asks the \
environment. When the environment reports success you may mark the task \
complete via `task_progress_check`; declaring completion without environment \
success does not count.
- Do not reset the environment; a reset is not a tool you have.

BUDGETS: at most {max_tool_iterations} tool calls; \
{max_invalid_actions} or more invalid actions ends the episode as a failure.

{memory_note}
"""


__all__ = ["build_episode_prompt"]
