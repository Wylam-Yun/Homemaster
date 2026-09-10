# V3.4 家庭资源权限架构

## 不变量

- 精确资源键：`environment_id × resource_kind(object|area) × resource_id × action`
  是唯一的授权真理源；名称只用于展示。
- 一个 checker：`PermissionChecker` 是唯一的家庭策略引擎；不允许第二个策略引擎，
  不允许通用工具弹窗。
- 当前调用范围：一张卡只覆盖当前工具调用（含其内部物理副作用）；不预批未来调用；
  早先完成的调用不回滚。
- 提交先行：决定先原子落库（决定＋长授＋请求状态＋回执＋审计同一事务），再唤醒等待调用；
  调用只读已提交决定，不信任布尔返回值。
- 未知不重发、失败有界重试（目标不变＋预算允许）、撤销与 claim 线性化。

## 数据流

```text
模型工具调用
 -> ToolExecutor._execute_physical
 -> adapter.prepare（只读，锁定 binding；同一调用在审批等待后重做并比对签名）
 -> store.create_request（持久化请求＋明细）
 -> checker.evaluate_physical（长授命中 vs 缺失项）
 -> 缺失项经确认通道逐项决定（Web/CLI；飞书不可用）
 -> store.submit（原子提交；重复 submission_id 幂等）
 -> 审批等待后只读重验 binding 未变
 -> store 重新匹配长授快照 -> claim -> device-lease 下按内部步骤执行
 -> adapter.observe 读回 -> store.finish_step（成功／有限失败重试／未知终止）
 -> adapter.release；事件投影到 WebSocket（requested/resolved/grants_changed）
```

## 最终接口

- 通用契约（`permissions/models.py`＋`resources.py`）：
  `ResourceKey`、`Requirement`、`PreparedStep`、`PreparedPhysicalRequest`、
  `ItemDecision`、`ApprovalSubmission(protocol_version=2)`、`ApprovalResolution`、
  `ExecutionObservation(succeeded/no_effect_failure/unknown)`、
  `PhysicalDeviceAdapter(prepare/execute/observe/release)`、动作目录
  （`pick_up/place/open/close/clean/heat/cool/slice/turn_on/turn_off/enter`，
  别名 `take→pick_up、put→place`；`use/toggle/manipulate` 由适配器先消解）。
- Web API（`web/app.py`＋`schemas.py`）：`POST /api/approvals/{id}`（逐项提交）、
  `GET /api/approvals/{id}`（查询）、`POST /api/approvals/{id}/cancel`（取消）、
  `GET /api/permissions/grants`（分页）、`POST /api/permissions/grants/{id}/revoke`
  （带 `expected_revision` 的 CAS 撤销）。旧单 `outcome` 形状返回
  `approval_protocol_outdated`。
- 事件：`approval.requested/resolved`（版本 2 明细）、`permission.grants_changed`。
- 存储（`permissions/store.py`，SQLite 六表＋`schema_version=1`）：请求、明细、
  步骤 claim、长授（有效唯一索引）、提交回执、审计事件；`store_path` 可配置，
  默认按 tenant 分区。
- 前端：逐项申请卡（无默认选项，`submission_id` 按选择锁定、重试复用）、
  长期权限页（动作分组＋撤销＋服务端分页）、断线重连后以后端为准。

## 真机接入（单独一节，独立验收）

1. 实现同一 `PhysicalDeviceAdapter` 四方法；`prepare` 只读解析并锁定 binding，
   `execute` 只消费已锁定 binding（不再重新选目标），`observe` 读回真实终态与
   设备返回码，`release` 在终态／取消时释放原生绑定（不删审计）。
2. 提供稳定身份：物品 ID 跨重启保持同一；目的地区域目录与当前位置接口真实存在；
   身份 epoch 变化时旧授权不得继承。
3. 未知结果按 `unknown` 上报，由执行门处理，不自动重发、不落成失败。
4. 不要求实现 ALFWorld 的 scene/episode 概念，也不读取 benchmark 元数据；
   仿真细节留在适配器内，通用 schema 不得新增仿真字段（接口审计强制）。
5. 上线门：取得真实驱动、地图／物品档案、物品 ID 与当前区域接口后，
   用同一核心＋前端跑验收适用场景，逐条核对物品／位置终态与设备返回码。
   当前状态：真机未验收（见 `plan/V3.4/acceptance-report.md`）。

## 已知边界

- ALFWorld 后端无权威区域模型：独立导航沿用旧路径，区域用例不声称通过；
  仅 `robot_manipulate` 经适配器设门（内部导航纳入同一闭包）。
- WebSocket 需要传输库；断线／重连后以服务端 `GET` 为准，旧卡不恢复成待批准。
