#!/usr/bin/env bash
# Interactive launcher for the arm_tools backends.
# It does not start CAN, the robot driver, MoveIt, VR, or cameras.

set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/arm_tools"
CONFIG_FILE="$BACKEND_DIR/config.yaml"
PYTHON="python3"

if [ -f "$HOME/Users/Dreams/ictor/RBM_openarm/source_all.sh" ]; then
  # shellcheck disable=SC1091
  set +u
  source "$HOME/Users/Dreams/ictor/RBM_openarm/source_all.sh"
  set -u
else
  echo "警告：找不到 ROS 环境脚本，后端可能无法导入 ROS。" >&2
fi
if [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
  # shellcheck disable=SC1091
  set +u
  source "$HOME/anaconda3/etc/profile.d/conda.sh"
  conda activate openarm >/dev/null 2>&1 || echo "警告：conda 环境 openarm 激活失败。" >&2
  set -u
fi

if [ ! -d "$BACKEND_DIR" ]; then
  echo "找不到后端目录：$BACKEND_DIR" >&2
  exit 1
fi

pause() {
  printf "\n按回车返回菜单..."
  read -r _
}

ask_arm() {
  local value
  while :; do
    read -r -p "手臂 [left/right]: " value
    case "$value" in
      left|right) printf '%s' "$value"; return 0 ;;
      *) echo "请输入 left 或 right。" >&2 ;;
    esac
  done
}

ask_or_default() {
  local prompt="$1" default="$2" value
  read -r -p "$prompt [$default]: " value
  if [ -z "$value" ]; then value="$default"; fi
  printf '%s' "$value"
}

make_json() {
  # 公共参数：arm=left/right；xyz/dxyz 为 world 坐标米；
  # 移动工具没有夹爪参数；timeout_s 为最大等待秒数。
  # 精度、速度和安全上限由 config.yaml 管理，不在菜单中调节。
  # Arguments are passed as argv, so quotes in a prompt cannot corrupt JSON.
  "$PYTHON" - "$@" <<'PY'
import json
import sys

kind = sys.argv[1]
a = sys.argv[2:]
if kind == "gripper":
    out = {"arm": a[0], "state": a[1]}
elif kind == "move_to":
    out = {"arm": a[0], "xyz": [float(x) for x in a[1:4]], "timeout_s": float(a[4])}
elif kind == "move_delta":
    out = {"arm": a[0], "dxyz": [float(x) for x in a[1:4]], "timeout_s": float(a[4])}
elif kind == "rotate_pitch":
    out = {"arm": a[0], "target_pitch": float(a[1]), "timeout_s": float(a[2])}
elif kind == "reset":
    out = {"arm": a[0], "timeout_s": float(a[1])}
elif kind == "scripted_grasp":
    out = {"arm": a[0], "xyz": [float(x) for x in a[1:4]], "approach_z": float(a[4]), "grasp_z_offset": float(a[5]), "timeout_s": float(a[6])}
elif kind == "back_project":
    out = {"camera": a[0], "pixels": json.loads(a[1])}
    if a[2]: out["arm"] = a[2]
elif kind == "world_map":
    out = {"camera": a[0], "resolution": a[1], "z_min": float(a[2]), "z_max": float(a[3]), "min_cluster_size": int(a[4])}
    if a[5]: out["arm"] = a[5]
    if a[6]: out["x_range"] = json.loads(a[6])
    if a[7]: out["y_range"] = json.loads(a[7])
else:
    raise SystemExit("unknown payload kind")
print(json.dumps(out, separators=(",", ":")))
PY
}

run_backend() {
  local script="$1" payload="$2" rc
  printf '\n调用 %s\n' "$script"
  "$PYTHON" "$BACKEND_DIR/$script" "$payload"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "后端返回失败，退出码：$rc" >&2
  fi
  return "$rc"
}

confirm_action() {
  local answer
  echo
  echo "这一步会向 ROS action/topic 发送真实控制命令。"
  read -r -p "确认执行请输入严格的 YES，其他输入都取消: " answer
  [ "$answer" = "YES" ]
}

show_frame_info() {
  "$PYTHON" - "$CONFIG_FILE" <<'PY'
import sys
try:
    import yaml
    with open(sys.argv[1], encoding="utf-8") as f:
        c = yaml.safe_load(f) or {}
    t = c.get("task_frame", {})
    m = c.get("moveit", {})
    vals = [float(t.get(k, 0.0)) for k in ("x", "y", "z")]
    print("动作坐标系：world（task_frame 不参与动作换算）")
    print("  calibrated : %s" % t.get("calibrated", False))
    print("  平移       : [%.4f, %.4f, %.4f] m" % tuple(vals))
    print("  task_frame 仅保留作可选记录，当前不会阻止移动")
    print("  MoveIt action: %s" % m.get("action", "/move_action"))
    print("  move_to/scripted_grasp 输入绝对 world xyz；move_delta 输入 world 增量")
except Exception as exc:
    print("读取 config.yaml 失败：%s" % exc)
PY
}

show_startup() {
  clear 2>/dev/null || true
  echo "============================================================"
  echo " arm_tools 交互入口（不自动启动任何机器人进程）"
  echo "============================================================"
  show_frame_info
  echo
  echo "运行前提：先由你手动启动 CAN、OpenArm 驱动和 MoveIt。"
  echo "动作工具使用 /move_action；同一只手臂不要同时运行 VR 遥操作。"
  echo "感知工具还需要深度流和 camera_extrinsics 标定。"
  echo
}

show_parameter_help() {
  cat <<'EOF'
参数说明：
  arm                 left/right，选择手臂。
  move_to 的 xyz      world 坐标系绝对位置，单位米。
  move_delta 的 dxyz  world 坐标系相对当前 TCP 的位移，单位米。
  target_pitch        绝对 pitch 角，单位弧度，范围 -1.5~1.5。
  reset 的 arm        left/right/both，回到 hands_up 安全姿态，不改变夹爪。
  approach_z          抓取点上方悬停高度，单位米。
  grasp_z_offset      抓取点 z 方向偏移，单位米。
  timeout_s           动作或抓取阶段的最大等待时间，单位秒。
  pixels              图像像素 [row,col] 数组；camera=wrist 时还要选择 arm。
  z_min/z_max         world 高度过滤范围，单位米。
  x_range/y_range     可选 world 平面范围 [min,max]，单位米。

位置/姿态容差、规划时间、速度和单次移动上限由 config.yaml 管理。
EOF
}

choose_camera() {
  local camera arm
  while :; do
    read -r -p "相机 [agentview/wrist]: " camera
    case "$camera" in
      agentview) printf '%s|' "$camera"; return 0 ;;
      wrist)
        arm="$(ask_arm)"
        printf '%s|%s' "$camera" "$arm"
        return 0
        ;;
      *) echo "请输入 agentview 或 wrist。" >&2 ;;
    esac
  done
}

do_set_gripper() {
  local arm state payload
  arm="$(ask_arm)"
  state="$(ask_or_default '夹爪状态（open=张开，close=闭合）' 'open')"
  case "$state" in open|close) ;; *) echo '状态必须是 open 或 close'; return ;; esac
  payload="$(make_json gripper "$arm" "$state")" || { echo '参数格式错误'; return; }
  if confirm_action; then run_backend set_gripper.py "$payload"; fi
}

do_release() {
  local arm payload
  arm="$(ask_arm)"
  payload="$(make_json gripper "$arm" 'open')" || { echo '参数格式错误'; return; }
  echo "目标：打开 $arm 夹爪，不移动手臂。"
  if confirm_action; then run_backend set_gripper.py "$payload"; fi
}

do_move_to() {
  local arm x y z timeout payload
  arm="$(ask_arm)"
  x="$(ask_or_default 'world x（米）' '0.60')"; y="$(ask_or_default 'world y（米）' '0.00')"; z="$(ask_or_default 'world z（米）' '0.90')"
  timeout="$(ask_or_default '动作最大等待时间（秒）' '15')"
  payload="$(make_json move_to "$arm" "$x" "$y" "$z" "$timeout")" || { echo '坐标和超时必须是数字'; return; }
  echo "目标：world [$x, $y, $z] m，arm=$arm。"
  if confirm_action; then run_backend move_to.py "$payload"; fi
}

do_move_delta() {
  local arm dx dy dz timeout payload
  arm="$(ask_arm)"
  dx="$(ask_or_default 'world dx（米）' '0.02')"; dy="$(ask_or_default 'world dy（米）' '0.00')"; dz="$(ask_or_default 'world dz（米）' '0.00')"
  timeout="$(ask_or_default '动作最大等待时间（秒）' '15')"
  payload="$(make_json move_delta "$arm" "$dx" "$dy" "$dz" "$timeout")" || { echo '位移和超时必须是数字'; return; }
  echo "目标：从当前 TCP 在 world 系移动 [$dx, $dy, $dz] m。"
  if confirm_action; then run_backend move_delta.py "$payload"; fi
}

do_rotate_pitch() {
  local arm pitch timeout payload
  arm="$(ask_arm)"; pitch="$(ask_or_default '目标 pitch（弧度，-1.5~1.5）' '0.30')"
  timeout="$(ask_or_default '动作最大等待时间（秒）' '15')"
  payload="$(make_json rotate_pitch "$arm" "$pitch" "$timeout")" || { echo 'pitch 和超时必须是数字'; return; }
  echo "目标：arm=$arm，保持 TCP 位置，调整绝对 pitch=$pitch rad。"
  if confirm_action; then run_backend rotate_pitch.py "$payload"; fi
}

do_scripted_grasp() {
  local arm x y z approach offset timeout payload
  arm="$(ask_arm)"; x="$(ask_or_default '抓取点 world x（米）' '0.60')"; y="$(ask_or_default '抓取点 world y（米）' '0.00')"; z="$(ask_or_default '抓取点 world z（米）' '0.90')"
  approach="$(ask_or_default '悬停高度（相对抓取点，米）' '0.10')"; offset="$(ask_or_default '抓取点 z 偏移（米）' '0.00')"; timeout="$(ask_or_default '每个阶段最大等待时间（秒）' '60')"
  payload="$(make_json scripted_grasp "$arm" "$x" "$y" "$z" "$approach" "$offset" "$timeout")" || { echo '参数格式错误'; return; }
  echo "流程：打开夹爪 -> world 目标上方 -> 下降 -> 闭合 -> 抬起。"
  if confirm_action; then run_backend scripted_grasp.py "$payload"; fi
}

do_reset() {
  local arm timeout payload
  while :; do
    read -r -p "复位手臂 [left/right/both]: " arm
    case "$arm" in left|right|both) break ;; *) echo '请输入 left、right 或 both。' >&2 ;; esac
  done
  timeout="$(ask_or_default '复位最大等待时间（秒）' '30')"
  payload="$(make_json reset "$arm" "$timeout")" || { echo '超时必须是数字'; return; }
  echo "目标：$arm 回到 hands_up 安全姿态，夹爪不变。"
  if confirm_action; then run_backend reset_arm.py "$payload"; fi
}

do_back_project() {
  local selected camera arm pixels payload
  selected="$(choose_camera)"; camera="${selected%%|*}"; arm="${selected#*|}"
  pixels="$(ask_or_default 'pixels JSON（例如 [[240,320],[242,322]]）' '[[240,320]]')"
  payload="$(make_json back_project "$camera" "$pixels" "$arm")" || { echo 'pixels 必须是合法 JSON 数组'; return; }
  run_backend back_project_batch.py "$payload"
}

do_world_map() {
  local selected camera arm resolution zmin zmax mincluster xr yr payload
  selected="$(choose_camera)"; camera="${selected%%|*}"; arm="${selected#*|}"
  resolution="$(ask_or_default '分辨率 high/low' 'low')"; zmin="$(ask_or_default 'z_min（world 米）' '0.85')"; zmax="$(ask_or_default 'z_max（world 米）' '0.95')"
  mincluster="$(ask_or_default '最小聚类点数' '10')"; xr="$(ask_or_default 'x_range JSON，留空不限制' '')"; yr="$(ask_or_default 'y_range JSON，留空不限制' '')"
  payload="$(make_json world_map "$camera" "$resolution" "$zmin" "$zmax" "$mincluster" "$arm" "$xr" "$yr")" || { echo '范围必须是合法 JSON，例如 [0.3,0.8]'; return; }
  run_backend query_world_map.py "$payload"
}

show_startup
while :; do
  echo
  echo "1) view_env_state   2) set_gripper   3) release"
  echo "4) move_to          5) move_delta    6) rotate_pitch"
  echo "7) scripted_grasp   8) reset_arm"
  echo "9) back_project    10) query_world_map"
  echo "h) 参数说明  i) 坐标说明  q) 退出"
  if ! read -r -p "选择工具: " choice; then
    echo
    break
  fi
  case "$choice" in
    1) run_backend view_env_state.py '{}' ; pause ;;
    2) do_set_gripper ; pause ;;
    3) do_release ; pause ;;
    4) do_move_to ; pause ;;
    5) do_move_delta ; pause ;;
    6) do_rotate_pitch ; pause ;;
    7) do_scripted_grasp ; pause ;;
    8) do_reset ; pause ;;
    9) do_back_project ; pause ;;
    10) do_world_map ; pause ;;
    h|H) show_parameter_help ; pause ;;
    i|I) show_frame_info ; pause ;;
    q|Q) echo '退出。'; break ;;
    *) echo '请输入菜单编号、i 或 q。' ;;
  esac
done
