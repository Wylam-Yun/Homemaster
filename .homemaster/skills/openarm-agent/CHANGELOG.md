# Changelog

## 2026-10-02 - 保持运动起步支撑命令连续

- 将旧 TCP 残差补偿替换为固定 IK 关节目标、新鲜控制器反馈、整轨有界偏差补偿和 FollowJointTrajectory 执行；保持首点与原支撑命令连续，减少起步卸力。
- 增加逐关节限幅、返回码与新鲜终态检查、JSONL 记录及 13 项离线回归；move_delta 传播子进程退出码。
- 真机已完成草莓抓取入盘；跟踪残差与限位拒绝仍存在，超时/abort 连续保持分支尚未专门完成真机验收，不宣称控制问题全部修复。

## 2026-10-02 - 沉淀抓取判据并收拢任务经验

- 在 SKILL.md 加入腕部视觉对齐、接触与试抬确认、动作结果分层判断和松爪退开后验收规则，避免空夹误判及将失败标志直接等同于未运动。
- 补充接近小步、固定绝对目标重试、受限转移与位姿记录说明；同步纠偏概述，并改为观察后决定恢复，不因外层失败自动复位。
- 将 workspace_weilin/experience 整体移入 openarm-agent/experience，保留六个原有记录/证据文件；增加任务索引、入口链接并修正相对路径，供类似任务按需参考，历史点位不作为直接重放轨迹。

## 2026-10-01 - Add bounded TCP convergence and motion planning guidance

- Added accumulated position-residual compensation after successful MoveIt
  motions, with fixed original targets, a capped total compensation, safety
  bounds, divergence detection, per-attempt telemetry, and short retry timeouts.
- Set motion defaults to 15 seconds, kept correction retries at 8 seconds, and
  disabled correction in `move_delta` and contact-prone `scripted_grasp` phases.
- Documented phase-first planning and the rule to avoid artificial micro-steps
  for known free-space targets.

## 2026-10-01 - 收拢为标准 skill 文件夹

- 把散落根目录的 `SKILL.md`、`CHANGELOG.md`、`arm_tools.sh`、`arm_tools/`、`references/` 收拢进 `openarm-agent/`，skill 自包含。
- 删除 `.opencode/tools/*.ts`（共 10 个）：Python 后端可直调，不再需要 TS 注册层；同步清理残留 `.opencode/` 目录。
- 更新 `arm_tools/README.md` 的目录树与运行说明，修正 `.gitignore` 的 `__pycache__`/`snapshots` 路径。

## 2026-09-30 - Add OpenArm agent skill

- Added a repository-level `SKILL.md` with explicit motion, gripper, reset, perception, and recovery boundaries.
- Added progressive references for tool schemas and safety verification.

## 2026-09-30 - Separate gripper actions and add safety reset

- Removed gripper side effects from `move_to`, `move_delta`, and `rotate_pitch`.
- Added `reset_arm` to move one or both arms to the configured `hands_up` safety posture and verify final joint error.
- Kept `set_gripper`, `release`, and `scripted_grasp` as the explicit gripper and composite-action tools.

## 2026-09-30 - Simplified agent-facing parameters

- Removed unused movement parameters (`step_clip`, `max_steps`, `n`) and exposed precision settings from the agent-facing tools.
- Moved position and orientation tolerances into `arm_tools/config.yaml` while retaining final TCP error verification.
- Replaced numeric gripper commands and interpolation controls with `state=open/close`; `release` remains the explicit open shortcut.
- Changed gripper execution to one action goal and documented all interactive shell parameters, reducing avoidable command latency.

## 2026-09-30 - Initial arm_tools baseline

- Added the standalone agent control tools and interactive shell for OpenArm.
- Reused the existing ROS 2, MoveIt, joint trajectory controller, and gripper action paths.
- Added world-frame motion tools, TCP verification, pitch control, gripper control, perception helpers, and safety checks.
- Added explicit MoveIt planning and execution error explanations for agent callers.
