# V3.4 backend-readiness — Task 0 调查记录

> 性质：调查记录，不写业务代码、不操作真实机器人。
> Worktree：`/data1/haodong2/weilin/red_bird/Homemaster-v34-permissions`（分支 `v34-permissions`，基线 `2f6b4581`）。
> 主树：`/data1/haodong2/weilin/red_bird/Homemaster`（`main`，`2f6b4581`，`git status --short` 空）。
> 日期：2026-09-10。计划真理源：`plan/V3.4/permission-system-design.md` + `permission-system-implementation-plan.md`。
> 已读：`CLAUDE.md`（live-run隔离/外部终态/信号/deadline等纪律）、`docs/pitfalls.md`（09-09 visual reset门、09-08 memory runtime/Neo4j CLI/runner隔离等）。

## 0. 调查命令（worktree内执行）与原始结果

| 命令 | 退出码 | 结果摘要 |
|---|---|---|
| `git status --short` | 0 | 空（干净） |
| `git rev-parse HEAD` | 0 | `2f6b458111ef31629adc07b8e1cbc97254c1d072`（计划基线写`e4c7940f`，已漂移2个commit：`7a7f9138`计划文档+`f3ed7072` ALFWorld adapter/workspace fixes合并） |
| `.venv/bin/python --version` | 0 | `Python 3.11.13`（`uv sync --frozen --extra dev`新建worktree venv通过） |
| `.venv/bin/python -c 'import sys,sqlite3,pytest; ...'` | 0 | `sys.executable=.../Homemaster-v34-permissions/.venv/bin/python`，`sqlite3.sqlite_version=3.50.4` |
| `rg -n 'ToolExecutor\(|PermissionChecker\(' src/homemaster/application` | 0 | `factory.py:88`唯一组合根；`runtime.py:264`默认构造、`runtime.py:708` browser run重建、`runtime.py:440` ApplicationToolExecutor——三处须在Task4逐个透传同一checker/store/handler |
| `rg -n 'go_to_target|manipulate_with_thor|force_toggle' src/homemaster/benchmarking/alfworld` | 0 | `tools.py:156/174/187/198`、`env_adapter.py:1129/1743/2363`确认宏动作内部导航+操作并存 |
| `rg -n 'robot_' src/homemaster/domain/tools.py` | 0 | `robot_navigate/manipulate/verify`三工具均为`simulated_skill` stub |
| `node --version`（`.runtime/v34/node/bin`优先） | 0 | `v22.22.1`，`npm 10.9.4`；满足`web/package-lock.json`中vite 6.4.3要求的`^20.19.0 \|\| ^22.12.0 \|\| >=24.0.0` |
| `npm --prefix web ci` | 0 | `added 252 packages, audited 253`，未改`package-lock.json` |

Node绑定：`.runtime/v34/node -> /data1/haodong2/.nvm/versions/node/v22.22.1`（symlink，gitignored）。复用已有安装，未下载；无需SHA256核对（无新下载物）。Python使用`/home/haodong2/.local/bin/uv`（`uv 0.11.19`），未装全局包。

## 1. 后端逐项结论

### 1.1 通用域工具（`src/homemaster/domain/tools.py`）——不可作为物理真理源

| 项 | 结论 | 证据 |
|---|---|---|
| 稳定物品ID | 未验证（无ID概念，只有`target_object`自然语言） | `tools.py:200-214` `_exec_robot_manipulate`直接回显`target_object` |
| 区域ID/当前位置API | 未验证（`room_hint/target_room`自由文本，无区域目录） | `tools.py:185-198` `_exec_robot_navigate`取`room_hint`即算到达 |
| 只读prepare | 未验证（无prepare，只有直接execute stub） | 同上，无`prepare/call`分离 |
| 宏动作集合 | 仅3个stub：navigate/manipulate/verify，无内部步骤声明 | `tools.py:401-449` |
| 执行返回码 | 恒`success=True`，无失败/unknown分支 | `tools.py:185-231` |
| 外部观察 | 无（返回体即断言，无独立状态读回） | 同上 |
| 重启身份 | 无（无持久身份） | — |

用途：仅模型链路占位。Task2/4/9核心测试不得用它充当物理成功，必须用`tests/fixtures/permissions/device_process.py`独立进程。

### 1.2 ALFWorld（`src/homemaster/benchmarking/alfworld/`）——Task8前置，核心开发不阻塞

| 项 | 结论 | 证据 |
|---|---|---|
| 稳定物品ID | 部分存在、跨reset继承未验证 | `env_adapter.py:99-105`有`object_id`，`512`刷`_scene_object_index`，`2067+`用`inventory_object_ids`核对；但`_scene_generation`/`episode_id`（`270-283,314-315,553`）是否保证同物同ID跨reset保持，需真实THOR跑`reset→read→reset→read`对照，缺证据 |
| 区域ID/当前位置 | 未验证 | grounding仅`object/receptacle/toggle`（`tools.py:143-149` `_ground_target`），无`area`目录；`current_state`（`env_adapter.py:307`）未发现room/current_area字段；不得把receptacle标签充当房间 |
| 只读prepare | 未验证（当前`_exec_manipulate`直接副作用） | `tools.py:166-215`内部先`go_to_target`再`manipulate_with_thor`，无只读声明阶段；Task8须拆出 |
| 宏动作集合 | 已知至少：manipulate内含导航；take/use/open/close/put/heat/cool/clean/slice/navigate/verify | `tools.py:156-215` + `types.py:AlfworldAction`；开柜/开电器等隐式效果须Task8逐项声明，否则拒宏动作 |
| 执行返回码 | 结构化反馈存在，运行时映射未验证 | `types.py:ToolExecutionError`+`make_execution_feedback`，`tools.py:_result_from_step`带`backend_attempted`；需真实后端逐case核对 |
| 外部观察 | `current_state`/inventory存在，独立读回未验证 | `env_adapter.py:307,2058+`；Task8须用环境独立状态（非adapter自报）验证 |
| 重启身份 | scene/episode映射仅适配层概念，未验证epoch语义 | `env_adapter.py:263-283` `episode_prefix/scene_generation`；通用schema不得引入scene/episode字段 |

### 1.3 真机——未ready，不阻塞核心开发，阻止宣称上线

驱动、地图/物品档案、物品ID与当前区域接口全部未知。报告记“真机未验收”，Task9真机门按现场操作范围单列。

## 2. 门结论

* 通用核心（Task1-7）+独立进程黑盒（Task9进程部分）：可开工，不依赖ALFWorld readiness。
* Task8 ALFWorld接线：依赖本表1.2逐项转“已验证+证据路径”，否则对应能力标unsupported。
* 真机上线：缺硬件/驱动证据，任何阶段不得记PASS。

## 3. 已知风险（Task1起须盯）

1. 基线漂移：计划写`e4c7940f`，实际`2f6b4581`（含ALFWorld adapter修复）。已用worktree隔离，主树干净，无需合入他人改动。
2. 三处executor构造（`factory.py:88`/`runtime.py:264,440,708`）+四处confirm实现（Web/CLI/Gateway/browser）须原子迁移，bool陷阱回归先行。
3. `place`长期不绑落点 vs 本次binding锁定——最易写错，Task4单测必须覆盖换落点场景。
