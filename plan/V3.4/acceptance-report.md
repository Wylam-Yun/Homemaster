# V3.4 acceptance-report — process backend (process-001)

> 去敏结论 + 证据引用。原始证据在 gitignored 的 `.runtime/v34/acceptance/process-001/`，
> 以 `manifest.json`（schema 1）为索引。结论：process 后端 **PASS**；
> ALFWorld 真后端与真机**未验收**，不得宣称可用。

## 1. 范围与环境

- 分支 `v34-permissions`，验收 commit 见 manifest `source_commit`。
- Backend：独立子进程设备（`tests/fixtures/permissions/device_process.py`，纯标准库
  JSONL：一机一区两杯 + 动作计数；`reset/read/move/pick_up/place/clean/fail_next`）。
  该设备是可控外部测试系统，不是真机。
- Python 3.11.13，SQLite 3.50.4，Node v22.22.1，Chrome 127 headless（真实浏览器轮次）。
- 编号纪律：两次中断尝试（缺 WS 传输库；页签断言写错）未留证据即删除；
  `process-001` 为首次完整跑，重跑须换新编号，不得复用。
- 环境备注：
  - 项目 venv 无 websockets，uvicorn 无 WS 支持；verifier 从 out-of-tree 的
    `.runtime/v34/ws-lib` 经 `PYTHONPATH` 补充，仅验收环境，不改产品依赖。
    产品部署同样需要 WS 传输库（见 Task 10 后续事项）。
  - 浏览器轮次用的前端为本轮源码构建，构建产物已回退（`static_dist` 不入库）。

## 2. 结果总览

| 验收项 | process | ALFWorld | 真机 |
|---|---|---|---|
| 首次审批前零变化 | PASS | — | 未验收 |
| A 批准不影响 B／拿取不含清洗 | PASS | — | 未验收 |
| once 消费后重申请 | PASS | — | 未验收 |
| 有限失败重试计数 | PASS | — | 未验收 |
| unknown 不重发 | PASS | — | 未验收 |
| 组合部分拒绝零变化 | PASS | — | 未验收 |
| 早先导航保留 | PASS | — | 未验收 |
| destination-only／离开再入 | PASS | — | 未验收 |
| 物品跟随／落点锁定 | PASS | — | 未验收 |
| 撤销 claim 交错／重复提交／事务原子 | PASS | — | 未验收 |
| 断线取消重启恢复 | PASS | — | 未验收 |
| HTTP 提交＋sqlite 直读＋重启持久 | PASS | — | 未验收 |
| 真实浏览器整轮＋截图 | PASS | — | 未验收 |
| 适配器映射（fake seam） | — | PASS（非真机） | — |
| 真后端／真机门 | — | 未验收 | 未验收 |

## 3. 黑盒逐目标结论（16/16，见 `pytest-process_blackbox.log`）

- 首次拒绝：`permission_denied`，设备四类动作计数全零，请求行存在。
- 杯 A 长授不覆盖杯 B；拿取授权不含清洗；once 消费后同动作再申请被拒。
- 注入一次失败后执行门重试恰好两次（`pick_up` 计数 2），终态成功，
  返回 `backend_code=pick_up-ok`。
- 断线：执行中杀进程 → `outcome_unknown`，同 binding 仅一次尝试，不重发；
  重启设备后新调用正常。
- 组合（进入＋拿取）部分拒绝：HTTP 200 blocked 语义在 store 层成立，
  设备零副作用，已存进入授权保留。
- 早先导航保留：进入完成后取物被拒，区域仍为卧室，不回滚。
- destination-only：已在卧室时再进入 → 无新请求、无新动作（`no physical effects`）。
- 离开再入：once 授权离卧室后重进需重新批准。
- 物品跟随：杯 A 的拿取长授随杯跨房间有效（第二次拿取未经过审批通道，
  handler 调用计数不变），区域保持卧室。
- 落点锁定：不同落点的 place 绑定签名不同；执行落点与设备终态一致。
- 撤销 claim 交错：claim 后撤销，`matching_grants` 为空，设备零动作。
- 重复提交：同 `submission_id` 两次提交同一决议，grant 行唯一。
- 失败原子：注入失败的单步执行后设备状态与执行前一致，
  观察为 `no_effect_failure`（`device:injected-failure`），证据引用非空。
- 重启：杀设备＋重开同库，长授仍在，新会话拿取无需审批（审批通道零调用）；
  取消请求状态为 `cancelled`，设备零动作。

## 4. 服务与持久化（9/9，见 manifest `service_http_persistence`）

- 真实 uvicorn 子进程＋文件库：HTTP 提交 200 ready、重复提交幂等、
  API 列表与 sqlite 直读逐行一致、决定行落库、撤销 200、stderr 无异常。
- SIGTERM 后同库重启：撤销记录保留、生效列表为空、stderr 干净。

## 5. 真实浏览器轮次（17/17，截图 `shots/01–04`）

- 待批卡经真实 WebSocket 到达并渲染：逐项单选、无默认选中、仅显示名称／位置／动作，
  DOM 文本无内部 ID（逐条核对）；截图 `01-card.png`。
- 逐项选择（进入＝始终允许，拿取＝本次允许）后提交，卡片经 resolved 事件关闭。
- 权限页列出进入长授（拿取为 once，不入库——符合语义）；API 与 sqlite 直读逐条一致；
  刷新后列表仍在；截图 `02/03`。
- 撤销进入后列表消失；重发动作出现新申请卡（截图 `04`），DOM 同样无内部 ID。
- 重启服务：撤销记录保留、生效为空、悬挂请求标为 `interrupted`（符合 store 恢复语义）。

## 6. 未验收项与上线门

- **真机未验收**：无真实驱动、地图／物品档案、物品 ID 与当前区域接口。
  授权真实操作前须按用户现场操作范围执行 Task 1 适配契约＋上述适用场景，
  读取物品／位置终态并核对设备返回码。
- **ALFWorld 真后端未验收**：hkust4 无 THOR 运行时。Fake-seam 适配器测试通过
  （见 `pytest-alfworld_adapter_fake_seam.log`），但不得充作仿真结果；
  需真实 THOR 跑稳定 ID、跨 scene、内部导航、未知回执逐项核对。

## 7. 证据引用

- `manifest.json`（HTTP 码、backend 码、before/after、DB 引用、stderr 检查）。
- `pytest-*.log`、`db/*-readback.json`、`shots/01-card.png`、`shots/02-grants.png`、
  `shots/03-grants-after-reload.png`、`shots/04-card2.png`、`logs/*.stderr.log`。
- 所有被拥有的进程／socket 已结束；设备与服务 stderr 均干净。
