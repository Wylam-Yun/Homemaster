# arm_tools（RBM OpenArm，真机）

Python 后端直调：`arm_tools/` 放 Python 后端脚本，直接 `python3` 传参调用。

## Agent Skill

skill 根目录的 [`SKILL.md`](../SKILL.md) 是给 Agent 使用的控制入口，覆盖移动、夹爪、复位和感知工具。详细参数见 [`references/tools.md`](../references/tools.md)，安全与恢复规则见 [`references/safety.md`](../references/safety.md)。
调用方直接传参调脚本；逻辑全在 Python；输出统一单行 JSON。

执行类似任务前，按 [`experience/index.md`](../experience/index.md) 选择相关经验。
例如草莓入盘先读 [2026-10-02 成功记录](../experience/2026-10-02-openarm-strawberry-grasp.md)，
参考其中实际抓取和松爪姿态，再用当前相机和 TCP 校正目标；历史点位不能直接重放。
接近、重试及受限转移的注意事项见 [`references/manipulation.md`](../references/manipulation.md)。

## 交互式入口

不想手写 JSON 时，在 `openarm-agent` 目录运行：

```bash
bash ./arm_tools.sh
```

它只加载 ROS/conda 环境并显示菜单，不会自动启动 CAN、驱动、MoveIt、VR
或相机。菜单会逐项询问参数；`view_env_state`、`back_project_batch`、
`query_world_map` 是只读操作。夹爪和手臂动作在真正调用前必须输入严格的
`YES`，其他输入都会取消。

菜单中的坐标输入如下：

| 工具 | 主要输入 | 坐标系 |
| --- | --- | --- |
| `set_gripper` / `release` | `left/right`，开合 | 无位移 |
| `move_to` / `scripted_grasp` | `xyz=[x,y,z]` | `world`，米 |
| `move_delta` | `dxyz=[dx,dy,dz]` | `world` 增量，米 |
| `rotate_pitch` | `target_pitch` | 弧度，绝对 pitch |
| `back_project_batch` | `pixels=[[row,col],...]` | 图像像素，输出 world 米 |
| `query_world_map` | `z_min/z_max`、可选 xy 范围 | `world`，米 |

动作工具现在直接使用机器人 `world` 坐标，不要求测量桌面原点，也不依赖
`task_frame.calibrated`。`task_frame` 字段仍保留在配置中，但只作记录，不参与
坐标换算。建议先用小幅度 `move_delta` 试探，确认 TCP 位置后再把 world 坐标
传给 `move_to`。

## 目录

```
openarm-agent/
  SKILL.md           # skill 入口：能力边界、操作顺序、恢复规则
  arm_tools.sh       # 交互式菜单入口
  references/        # 工具参数表 + 安全规则
  experience/        # 任务经验索引、实测点位、失败复盘与会话证据
  arm_tools/
    config.yaml        # 话题/帧名/task_frame/相机外参/限位
    arm_common.py      # 共享：配置加载、LiveState（joint/TF/图像快照，只读）
    perception_common.py  # 感知共享：相机解析、外参、深度反投
    set_gripper.py     # 后端：GripperCommand Action（drive_gripper 可 import）
    view_env_state.py  # 后端：快照 + 存图到 snapshots/
    robot_client.py    # 共享：固定 IK 目标、整轨补偿执行 + 最终 TCP 校验
    move_to.py         # 后端：world xyz → MoveIt hand_tcp 目标
    move_delta.py      # 后端：算一次目标（world 增量）→ 调 move_to
    rotate_pitch.py    # 后端：生成目标姿态 → MoveIt
    scripted_grasp.py  # 后端：串行编排 move_to + set_gripper，阶段超时
    reset_arm.py       # 后端：关节空间回 hands_up 并校验关节误差
    back_project_batch.py  # 后端：3x3 中值深度 + 内参反投 + 外参到 world
    query_world_map.py     # 后端：降采样投影 + z/xy 过滤 + 2cm 栅格聚类
    snapshots/         # view 存图（运行时生成）
```

## 运行前提（每次调用前先加载环境）

```bash
source ~/Users/Dreams/ictor/RBM_openarm/source_all.sh
source ~/anaconda3/etc/profile.d/conda.sh
conda activate openarm
```

真机侧还需：CAN 已起（菜单 2）、OpenArm 驱动已起（菜单 3，默认
`joint_trajectory_controller`）、MoveIt 已起（菜单 4）、相机脚本已起（菜单 9，
否则图像记 null 不报错）。动作 tool 通过 MoveIt 的 IK/FK 与 `/plan_kinematic_path` 规划，再经现有
`FollowJointTrajectory` action 执行整轨补偿命令，
不再直接发布手臂关节 topic；使用 agent 动作时不要同时运行 VR 遥操作。
感知两个 tool 额外需要：驱动开深度（`realsense.sh` 里 `enable_depth:=false`
改 `true`，或单起 `rs_launch.py` 传参）+ 下方外参标定，否则直接报错不跑。

## 使用前配置：相机外参（感知工具需要）

1. `camera_extrinsics`（back_project/query_world_map 用）：标每只相机光心在
   world 下的位置 + 姿态。粗标法：view 读出 TCP，把已知 world 坐标的标定点
   （如桌角贴 ArUco）放在镜头前，用 back_project 反投结果与真值对齐，
   调 xyz/quat 直到 3 个以上点误差 <2cm，再置 `calibrated: true`。
   精标以后用手眼标定重做，接口不变。

## tool 说明

### set_gripper / release
- `set_gripper` 输入：`arm(left/right)`、`state(open/close)`；夹爪原地动作，不移动手臂。
- `release` 输入：`arm`；它是 `set_gripper(state=open)` 的语义快捷入口。
- 输出：`{success, arm, state, width}`（0.0 闭 / 0.044 开）。
- 链路：`left|right_gripper_controller/gripper_cmd`（GripperCommand Action，
  `position` + `max_effort=0.0`，与 ik_teleop_node 同值）；内部只发一个目标。

### view_env_state
- 输入：无。永远读最新（step 索引等录 HDF5 后再加）。
- 输出：`{success, stamp, q_left[7], q_right[7], tcp_left, tcp_right,
  images{head,wrist_left,wrist_right}, missing[]}`
- 链路：`/joint_states` 订阅 + TF2 `world→openarm_*_hand_tcp` +
  三路 compressed 图像各取一帧存 `snapshots/`。纯只读。

### move_to
- 输入：`xyz(world 系绝对位置)`，`arm`，`timeout_s=15`，可选 `converge=false`
- 输出：`{success, final_xyz, position_error_m, orientation_error_rad, iterations, attempts, ...}`
- 链路：
  1. 检查 world 目标和安全高度。
  2. 读取当前 `hand_tcp` 姿态，保持姿态并将 TCP 目标交给 MoveIt。
  3. MoveIt 使用现有 `left_arm/right_arm` 规划组和
     `joint_trajectory_controller` 执行。
  4. 执行后重新读取真实 `hand_tcp` TF；位置和姿态误差都在容差内才成功。
  5. 执行后残差超限时，围绕固定 IK 关节目标执行有界跟踪纠偏；规划/控制器失败或误差不降时停止。

### move_delta
- 输入：`dxyz(world 系增量)`，`arm`，`timeout_s=15`
- 输出：move_to 的原样 JSON。
- 链路：读一次当前 TCP（world）并锁定目标 → 调共享 move_to。

### rotate_pitch
- 输入：`target_pitch(绝对弧度，限±1.5，默认0.6)`，`arm`，`timeout_s=15`
- 输出：`{success, arm, final_pitch, pitch_error_rad, position_error_m, ...}`
- 链路：TF 读当前位姿提 pitch → 生成保持 TCP 位置的新姿态 → MoveIt
  规划执行 → 重新读取 TCP 验证位置和姿态；夹爪动作需单独调用 `set_gripper`。

### scripted_grasp
- 输入：`xyz(world 系抓取点)`，`arm`，`approach_z=0.10`，
  `grasp_z_offset=0.0`，`timeout_s=60`（每个阶段的最大等待时间）
- 输出：`{success, phases[]}` 或 `{success:false, failed_phase, phases}`
- 链路：串行调用共享 `set_gripper`/`move_to`：张开→悬停→下降→闭→抬，
  任一步失败即停并标明卡点；每个阶段有超时。

### reset_arm
- 输入：`arm=left/right/both`，`timeout_s=30`
- 动作：把指定手臂移动到 MoveIt `hands_up` 安全姿态；不改变夹爪。
- 输出：每只手臂的 MoveIt 返回码、最终关节角和最大关节误差。
- 复位不是急停；危险情况必须使用实体急停按钮。

### 动作工具共用的控制链
固定目标与整轨补偿的数据流见 [`docs/implementation-plan.md`](../docs/implementation-plan.md)。
`tracking_ros.py` 校验反馈和终态，`joint_tracking.py` 约束纠偏，
`trajectory_compensation.py` 保持首点连续，`trajectory_executor.py` 规划并执行轨迹。
离线验证：在项目 Python 环境运行 `python arm_tools/test_converge.py`（从 skill 根目录）。
目前仍有跟踪残差和限位拒绝；超时/abort 连续保持分支尚未专门完成真机验收。

`move_to` 是唯一基础移动操作。`move_delta` 只计算目标，
`rotate_pitch` 只生成姿态，`scripted_grasp` 只编排阶段；它们都通过
`arm_tools/robot_client.py` 调用 MoveIt，不各自实现 IK 或发布关节命令。

### back_project_batch
- 输入：`pixels([[row,col]...]≤50)`，`camera=agentview`（wrist 需显传 arm，
  navview 拒）
- 输出：`{success, camera, points[](无效为null), median, valid, total}`
- 链路：取深度 + camera_info → 每点 3x3 中值深度 → 内参反投 → 外参到 world。
  标准用法：目标上取 3–8 个点读中值。

### query_world_map
- 输入：`z_min=0.85, z_max=0.95`，`x_range/y_range` 可选，`camera=agentview`，
  `resolution=low`（降采样步长 4，high 为 2），`min_cluster_size=10`
- 输出：`{success, camera, clusters[{center, count}]（按点数排序，最多50）,
  points_used}`
- 链路：整帧降采样投影到 world → z/xy 过滤 → 2cm 栅格聚类 → 滤小团。

## 真机验收（你做）

1. `view_env_state`：读数与肉眼/卷尺对上；三图有（depth 关着无深度是正常的）。
2. `set_gripper`：空爪开合平滑；捏海绵时确认闭合动作不会压坏物体。
3. `move_to`：先 2cm 小步 → 10cm → 30cm 拆段；残差>tol 判失败就停手查 TF。
4. `move_delta`：连发 5 次 2cm 增量，看漂不漂（重点看目标换算对不对）。
5. `rotate_pitch`：先 `target_pitch=0.2` 小角度，看位置漂不漂（<1cm 才继续加角度）。
6. `scripted_grasp`：先用空中假目标（手不放东西）走全流程，确认五步衔接；再放真东西抓。
7. `back_project_batch`：标定点上取 5 个点，中值与真值差 <2cm 才算外参可用。
   （需先开深度 + 标外参，否则工具直接报错是正常的）
8. `query_world_map`：`z 0.85-0.95` 框桌上物，看团块中心和肉眼对不对得上。

## 排错

- `ROS 环境未加载`：source 链没走通，检查 `source_all.sh` 路径。
- `无 /joint_states`：OpenArm 驱动（菜单 3）没起。
- `读不到 TCP TF`：驱动起了但 TF 树不全，`ros2 run tf2_ros tf2_echo world
  openarm_right_hand_tcp` 看。
- Action 5s 超时：gripper controller 没 spawn（驱动用的 controller 不对，
  需 `forward_position_controller` 那一路）。
- `move_to`/`move_delta` 先用小步测试；它们不要求 task_frame 标定。
- 长距离 move_to 超时：先拆小段调用。
- MoveIt 返回 `99999` 或 `-1` 时，JSON 中的 `error` 会明确说明：
  当前姿态、关节限位和碰撞约束下找不到有效的关节解。不要只看“失败”，
  先缩小目标位移，并检查 `moveit_error_code` 和最终 TCP 状态。
## 在别处使用本 skill

把整个 `openarm-agent/` 目录拷到目标位置即可：Agent 读 `SKILL.md` 入口，
按 `references/` 里的参数表直接 `python3 arm_tools/<tool>.py '<json>'` 调用；
交互式使用运行 `bash arm_tools.sh`。
