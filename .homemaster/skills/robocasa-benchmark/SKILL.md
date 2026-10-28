---
name: robocasa-benchmark
description: >
  跑 RoboCasa 评测集（benchmark-robocasa / robocasa_* 工具）时的操作技能。
  当任务要求运行、复现、调试或分析 RoboCasa Target50 评测，或以 robocasa profile
  完成仿真 cell（task+seed）时使用。此 skill 会随运行经验持续自进化，
  先读 experience/index.md 再行动。
---

# RoboCasa Benchmark

HomeMaster 以 `profile="robocasa"` 在 RoboCasa 仿真里跑评测 cell。
本 skill 是初始骨架；已验证的经验沉淀在 `experience/`，每次运行后按
文末「自进化规则」更新，而不是把假设写死在本文。

## 运行入口

- CLI：`benchmark-robocasa`（`src/homemaster/cli/benchmark_robocasa.py`），
  参数含 `rpent_root`、`task_manifest`、`trace_root`、`split`、`memory_mode`、
  `tasks`/`seeds` 等。
- 架构：HomeMaster（纯 CPU）→ NDJSON → `workers/robocasa_worker/`
  （RPent `.venv-robocasa`，持有 env_server + vla_server 生命周期）。
- 成功判定以环境为权威：`env.check_success` RPC 结果，不用工具返回标志
  或模型自述替代。

## 可用工具（robocasa_* 前缀，14 个）

- 感知/状态：`robocasa_observe`、`robocasa_world_map`、`robocasa_back_project`
- 技能/移动：`robocasa_skill`、`robocasa_navigate`、`robocasa_move_base`
- 臂与夹爪：`robocasa_arm`、`robocasa_move_to`、`robocasa_move_delta`、
  `robocasa_rotate_pitch`、`robocasa_set_gripper`、`robocasa_release`、
  `robocasa_scripted_grasp`
- 验收：`robocasa_verify`

各工具的确切参数以 `src/homemaster/robocasa/tools.py` 为准，不在本文重复。

## 最小工作流

1. 读 [experience/index.md](experience/index.md)，选读与当前 cell/任务类型
   相关的记录；历史结论是参考，不是当前场景的事实。
2. 运行前核对环境锁定信息（RPent commit、task manifest 及其 hash）。
   不一致就停下报告，不静默换数据集。
3. 执行时每次决策基于新鲜 `robocasa_observe`/状态返回；区分
   「工具执行失败」「状态未达目标」「任务成功」三层，不要互相混用。
4. 换 cell（task+seed）需要重启 env_server，不要在旧 cell 上继续执行
   新 cell 的指令。
5. 结束前用 `robocasa_verify` 与环境权威判定核对结果，记录到 trace。

## 自进化规则

- 每次有证据的运行结束后：把「已验证的结论、失败症状与恢复手段」写入
  `experience/` 一条记录，并在 `experience/index.md` 增加可检索条目；
  在 `CHANGELOG.md` 顶部记一行改动。
- 只沉淀有 trace/日志支撑的结论；未验证的假设必须显式标注为假设。
- 修改 SKILL.md 本身只用于稳定、跨任务复用的规则；单次条件（点位、
  阈值、seed 特例）放 `experience/`。
- 经验条目要保留「目标与实际反馈」的区别；历史点位与参数不得直接当
  当前测量值重放。
