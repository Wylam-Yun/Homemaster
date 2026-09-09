# V3.4 单件物品与目的地区域权限设计

- 状态：设计审阅稿；用户已确认第 2 节产品规则，第 3 节之后为具体技术提案，尚未实施。
- 审阅人：项目用户；用途：次日审阅后形成正式实施计划。
- 文档位置：用户指定 `plan/V3.4/`；本文件为本次设计的唯一正文。
- 调查基线：hkust4 当前工作区，HEAD `54aafabe`；存在其他工作中的未提交修改，本设计不包含、不覆盖这些修改。
- 范围：单机器人家庭资源审批，扩展现有 PermissionChecker；不设计第二套家庭策略引擎。
- 日期：2026-09-09 会话设计。

## 1. 要解决的问题与第一版边界

机器人第一次要对某一件物品执行某个动作，或前往某个目的地区域时，请求用户批准。
用户可以允许一次、以后都允许、拒绝。长期授权持久保存，前端可查看和撤销。

例子：“去卧室拿杯子 A”需要 bedroom_01/enter 和 cup_001/pick_up。
缺少的权限在同一张卡里逐项申请。若用户长期允许进入卧室、只允许本次拿杯子 A，
系统只保存前者的长期规则；两项均通过后才开始这次流程。
若拿取被拒绝，不为了这次拿杯子的目的先走进卧室，但明确选择的长期进入授权仍保存。

用户明确要求：用户交互审批只审批物品动作和目的地区域。
不叠加“是否允许 robot_navigate/robot_manipulate/terminal”的通用工具确认，不要求用户设计角色、能力等级或多机器人规则。
现有身份、存储隔离和设备约束不作为这轮产品讨论的扩展项目，也不因去掉工具弹窗而批量删除。
本版本不新增类别通配授权、全屋物品授权、永久拒绝、期限选择、规则优先级语言、禁入区或路径通行权限。

## 2. 已确认产品决策（实现不得自行改动）

| 编号 | 决策 |
|---|---|
| D01 | 单件物品，绑定稳定 ID；不同杯子分别授权，不按类别继承。 |
| D02 | 物品按具体动作授权；拿取不包含放置、清洗、加热等其他动作。 |
| D03 | 具体动作指用户语义动作；夹爪闭合、关节运动等实现步骤不逐个审批。 |
| D04 | 允许一次绑定当前明确动作请求；有限失败重试可复用，成功或终止后失效。结果不明先核实。 |
| D05 | 长期物品权限跟随物品，不绑定所在房间；进入目的地区域仍单独检查。 |
| D06 | 区域允许一次覆盖一次进入，到达后可停留，离开后失效；再次进入重新申请。 |
| D07 | 一张卡合并多项缺失权限，每项独立选择允许一次、以后都允许或拒绝。 |
| D08 | 当前流程必需权限全部通过才开始；部分拒绝不执行该流程，明确批准的长期规则仍保存。 |
| D09 | 只检查目的地区域，不审批途经区域；第一版不承诺机器人绝不会穿过其他区域。 |
| D10 | 第一版只有一台机器人，不增加机器人选择或多机器人继承。 |
| D11 | 物品、区域均绑定稳定 ID，名称和位置只展示。 |
| D12 | 长期授权跨重启、跨会话有效，直到手动撤销；撤销后下次重新申请。 |
| D13 | 放置权限绑定物品和放置动作，不绑定落点；当前落点在请求中展示。其他物品动作和目的地仍检查。 |
| D14 | 用户主动说“拿杯子 A”也不替代首次审批。 |
| D15 | 扩展现有 PermissionChecker；物品动作和目的地区域是本版唯一交互审批内容。 |

三个选择的准确语义：

- 允许一次：只创建当前请求内的临时执行凭据，不新增长期授权。
- 以后都允许：只保存卡片中该项准确资源和动作的长期授权，同时满足当前请求该项。
- 拒绝：结束当前请求，不写永久拒绝；Agent 不得对同一被拒意图自动重新申请或换工具绕过。
- 新的用户明确请求可以再次申请被拒事项。
- 没有回复、卡片过期、服务退出不等于批准。

## 3. 架构选择

| 候选 | 代价与结论 |
|---|---|
| 扩展 PermissionChecker | 用户选定。一个判断入口，新增资源授权查询；通过模块分工避免检查器包办存储与交互。 |
| 独立家庭策略引擎再叠加原工具检查 | 增加策略层与双重审批概念；用户不采用。 |
| 各工具自行审批 | 容易遗漏内部导航、重试和替代入口，规则重复；不采用。 |

推荐的内部职责（不是多个权限模式）：

1. ResourceResolver：解析准确资源身份，生成不可变 PreparedPhysicalRequest；不决定授权。
2. PermissionChecker：查询准确长期规则和当前请求凭据，返回 allow/ask/deny；唯一权限决策入口。
3. PermissionStore：持久化请求、逐项决定、长期授权与事件；不推断用户意愿。
4. 现有 confirmation handler：把同一结构化请求投影到 Web/CLI 等入口，返回结构化逐项决定。
5. ToolExecutor 与现有 runtime：持有当前请求，执行前复核，调用设备并消费一次性授权。
6. Web：展示、提交决定、查询和撤销；不自行推算规则命中，不直接修改数据库。

必须保留真实目标到最终 backend 参数的绑定。
LLM 可以提出目标和动作，但不能自建批准凭据、写权限表、扩大资源范围或声称已经授权。

## 4. 当前代码证据与改动落点

以下是源码调查，不是运行验证结果。

| 位置 | 当前情况 | 本版需要的改动 |
|---|---|---|
| src/homemaster/permissions/policy.py | evaluate_tool 检查路径、工具、capability、mode；FULL_AUTO/read-only/tool.auto 等分支放行 | 在同一 PermissionChecker 内增加结构化物理请求判断；任何通用 allow 分支不得跳过物品/区域判断；取消通用交互确认分支 |
| src/homemaster/permissions/config.py | DEFAULT/PLAN/FULL_AUTO 与工具规则配置 | 写清迁移含义，不新增家庭权限 mode；历史配置不得导入成物品授权 |
| src/homemaster/tools/executor.py | 验参后检查，confirmation 返回 bool，随后获取 lease 并调用 tool.execute | 增加 prepare 与结构化决定；审批前不得有移动；执行前复核准确请求 |
| src/homemaster/application/factory.py | 创建真实 PermissionChecker 和 ToolExecutor | 注入同一个 store/resolver，管理生命周期 |
| src/homemaster/application/runtime.py | 多处构造或包装 executor | 逐入口检查接线，防止默认 AllowAll 或新建 executor 漏过资源审批 |
| src/homemaster/domain/tools.py | room_hint/target_room/target_object 等自然语言字段 | 解析到 ID，不能直接以名称作为授权键 |
| src/homemaster/benchmarking/alfworld/tools.py | _exec_manipulate 内先 grounding，再可能 go_to_target，然后操作 | 将准确目标解析和副作用需求声明提前；内部导航必须进入同一审批闭包 |
| src/homemaster/benchmarking/alfworld/grounding.py | 存在名称规范化、候选选择和 judge 辅助解析 | 辅助结果不是稳定 ID；跨审批边界不得重新挑选对象 |
| src/homemaster/web/confirmations.py | 内存 pending Future，approve/reject，confirm 返回 bool | 服务端持久化逐项决定，Future 仅唤醒；返回结构化结果 |
| src/homemaster/web/app.py、schemas.py | POST /api/approvals/{approval_id} 接单一 outcome | 迁移成逐项提交，增加授权列表/撤销及请求查询 |
| src/homemaster/web/event_projection.py、web/src/protocol/events.ts | approval.requested/resolved 携带工具数据 | 迁移成物理意图和明细，返回协议版本 |
| web/src/components/ApprovalDialog.tsx | 展示工具名、cwd、原始 arguments，两个按钮 | 改为物品/区域逐项三选一，统一提交 |
| web/src/App.tsx、state/conversation.ts、api/http.ts | 单审批状态和单 outcome 提交 | 支持多项、待处理队列、结果回显及长期权限页 |

新增文件建议：permissions/models.py、store.py、resources.py、actions.yaml。
这些是现有 permissions 包内部职责划分，不是第二个 policy engine。

## 5. 身份与动作模型

### 5.1 资源身份

授权准确键为：
`(environment_id, resource_kind, resource_id, action)`。

environment_id 表示持久家庭/世界身份，不是 run_id、session_id 或进程启动时间。
资源 ID 应由权威环境标识映射到稳定内部 ID；同一家庭重启保持，移动与重命名保持。
被删除资源的 ID 不得分配给新物品。环境重建且无法证明身份延续时，使用新的 identity epoch/环境身份。
旧规则保留用于展示历史，但不能匹配新物品。

ALFWorld 的 “cup 1” 等文本标签不视作已验证的全局稳定 ID。
不同 episode、scene 的同名标签不能共享权限；同一场景重置能否继承，必须先验证身份契约。
环境提供的 room、receptacle、object 也不能互相替代：柜子是物品，不自动等同房间区域。
不应为了做演示把“到柜子旁”冒充“进入房间”。

推荐 ResourceRecord：
environment_id、resource_kind(object/area)、resource_id、native_ref、identity_epoch、
display_name、current_area_id（物品可空）、image_ref（可空）、status(active/missing/retired)、revision。

名称重名时前端显示照片或简短 ID。无法唯一解析返回 target_unresolved，要求澄清目标；
此时不得发一个模糊的长期授权申请，也不得执行。不要求用户做额外“权限批准”来替代目标识别。

### 5.2 动作

第一版动作目录采用精确动作键，不做层级继承或动作组：

| 动作键示例 | 用户文案 |
|---|---|
| enter | 进入目的地区域 |
| pick_up | 拿取 |
| place | 放置 |
| open / close | 打开 / 关闭 |
| clean | 清洗 |
| heat / cool | 加热 / 冷却 |
| slice | 切割 |
| turn_on / turn_off | 开启 / 关闭电器 |

目录列出规范表达，不代表当前每个 backend 均支持。真实支持集合在实施第一步逐 backend 核实。
take/put 等后端别名通过静态目录确定性映射到 pick_up/place。
use、toggle、manipulate 等宽泛调用必须在 prepare 阶段解析到实际动作，不能成为“所有操作”授权。
纯观察、读取现有状态不新增第三类审批；观察若会移动或操作，则声明实际副作用的目的地/动作。

“放置杯子 A 到柜子 B”：place 规则仅匹配杯子 A。
若柜门要打开，增加柜子 B/open；如果只是放到已有开放表面，不凭空增加容器操作权限。
一次请求的落点仍然锁定；长期规则不绑落点，不代表审批等待中可悄悄换落点。

## 6. 当前请求与执行范围

### 6.1 准备协议（内部技术提案）

PreparedPhysicalRequest 字段：
request_id、environment_id、session_id、run_id、intent_id、
intent_summary、revision、requirements[]、steps[]、target_snapshot_revision、
created_at、deadline_at、status。

每个 requirement 包含：
item_id、resource_kind、resource_id、action、display_snapshot、step_ids。

每个 step 包含：
step_id、operation_id、准确参数、resource bindings、required_item_ids、
状态与 backend receipt 引用。工具名只作为机器路由，不作为审批文案或授权键。

当前“动作流程”是准备后明确声明的一组有依赖动作，不是整个聊天、整个无限任务，
也不是任意单个底层 tool call。例如“去卧室拿杯子 A”包含导航与拿取两个步骤。
runtime 保存这一流程，并确保准备出的必需项齐备才发第一个有副作用步骤。
模型不能通过先发 go_to 再发 pick_up，把已知的同一流程拆开绕过整体批准门。
独立的“去卧室”指令则只需要区域项，不推测用户没有要求的物品动作。

一次性物品授权实际绑定 request_id + step_id + 精确资源 + 动作。
同一步有限重试复用；成功后同一工具重复回调不能重做。
若同一流程确实需要成功拿取两次，分别声明两次动作 occurrence，在卡片里展示次数，
不能把一个 item 的 allow_once 解读为无限重复。

### 6.2 完整执行顺序

1. 接收用户意图，准备准确动作流程；解析只读，不得偷偷移动来“准备”。
2. 确认资源身份和实际操作需求，锁定请求 revision 与 backend target binding。
3. PermissionChecker 检查每一必需项。已长期授权项也在流程摘要显示，但不要求重复选择。
4. 缺失项生成一张审批卡。全部命中则直接进入复核。
5. 用户一次提交所有缺失项；数据库事务保存全部决定和明确批准的长期规则。
6. 任一项拒绝：流程 blocked，零新物理副作用；保留明确的长期允许。
7. 全部通过：执行前复核资源、revision、当前长期授权是否仍有效以及一次性凭据。
8. 获取设备 lease，执行准确步骤；每一后续步骤也复核其授权，不能只凭初始整体验证。
9. 用 backend 返回码和外部观察决定成功、可重试失败或 outcome_unknown。
10. 成功/终止后消费或作废对应临时授权；向 UI 输出实际结果，不把“批准成功”显示成“动作完成”。

### 6.3 新发现、位置变化和内部副作用

- 已经知道需要开柜子时，open 必须在最初闭包中；不得等进入后才补已知项。
- 确实执行后才发现新需求时，暂停剩余流程，生成新 revision 或后继请求，列清新项；
  之前完成的物理动作不能回滚，UI 必须说明已完成部分。
- 杯子在审批期间移到另一房间：物品长期授权不变，但旧执行请求作废/重新准备目的地；
  新目的地未授权就申请，不能沿用旧请求进入。
- backend 宏动作若会自动开门、启用设备或去其他目的地，adapter 必须准确声明；
  不能声明完整需求的宏动作不进入本版可执行集合。
- 路径途经区域不增加权限项；内部动作有新的停靠目的地时，该目的地是新的检查项。

## 7. 状态、重试、离开与撤销

请求状态：
prepared → awaiting_approval → ready → running → succeeded / failed / outcome_unknown。
任一拒绝进入 blocked；等待超时进入 expired；用户取消或服务退出进入 cancelled/interrupted。
已终态请求不得回到 running。持久化状态是真理源，Future 不是授权凭据。

推荐失败重试上限为每个步骤总共 3 次尝试（首次 + 2 次重试），与现有更严格预算取较小值。
这是待审技术默认值，不是已由用户指定的数值。
仅在确认未完成目标、仍是同一绑定且请求未过期时重试。
换物品、换动作、换当前落点、切新流程均不算重试。
outcome_unknown 先只读核实；确认成功则消费授权，无法判断则暂停，不自动重发。

进入一次：
- 同一进入步骤在途失败可有限重试。
- 权威观察确认到达后，建立当前 request 的区域 occupancy 记录，可继续停留。
- 确认离开后关闭 occupancy；再次以前述区域为目的地需要新批准。
- 已在区域内不凭空发起新的 enter；区域状态未知时先确认。
- 后端拒绝导航或未到达不得把 occupancy 标为有效。
- 进程重启不恢复可执行的一次性授权；重新观察位置，当前位置已在房间内不等于再次进入。

撤销：
- 提交撤销后，尚未开始的步骤必须重新检查；原先依赖长期规则的 ready 请求不能悄悄降级成一次性允许。
- 已经真正开始的物理动作不承诺瞬间回滚；标明“撤销已生效，进行中的动作可能已开始”。
- 由单机器人执行调度 owner 串行化开始与撤销的线性化点：后获得顺序的未开始动作不能用旧规则启动。
- 不自动驱赶已经在房间内的机器人；下次进入重新申请。
- 完成的操作与已发生物理结果保留，不伪装成未执行。

## 8. 持久化设计

推荐项目运行数据目录下的独立 SQLite 库，通过 PermissionStore 接口访问，
复用现有依赖与应用生命周期，不新增远端数据库。
数据库路径从运行数据配置获得，不硬编码服务器绝对路径；真实文件 gitignore。
schema_version=1；UTC 时间；外键启用；事务和唯一约束承担并发一致性。
环境与资源目录可由现有权威资源服务提供，不另造第二份身份真理源。

### 8.1 表结构

| 表 | 核心字段与约束 |
|---|---|
| permission_grants | grant_id 主键；environment_id、resource_kind、resource_id、action；created_at、created_by、source_request_id、source_item_id、revoked_at、revoked_by、revision |
| permission_requests | request_id 主键；environment_id、session_id、run_id、intent_id、revision、immutable_plan_json、plan_digest、status、created_at、deadline_at、resolved_at |
| permission_request_items | item_id 主键；request_id 外键；准确资源/动作、展示快照、step_ids_json、decision、matched_grant_id；单请求同一作用域合并展示 |
| permission_steps | step_id 主键；request_id 外键；准确参数快照、required_item_ids、status、attempt_count、backend_receipt_ref、occupancy_state |
| permission_submissions | submission_id 主键；request_id、request_revision、payload_digest、response_json；确保重试幂等 |
| permission_events | event_id 主键；request_id、step_id、grant_id、event_type、timestamp、payload_json；业务决定与同事务事件 |

有效 grant 对 environment_id/resource_kind/resource_id/action 建立部分唯一索引（revoked_at IS NULL）。
再次长期允许已有规则为幂等，不新增重复有效记录。撤销后再次批准生成新 grant_id，保留历史。
第一版不开放手工创建任意 grant API；长期规则必须来自准确审批明细。
只存正向长期规则，不设 effect=deny，不做通配符、优先级或按名称匹配。

### 8.2 原子性与恢复

审批事务一次完成：校验 pending/revision/deadline → 校验所有 item 决定 →
写逐项结果 → upsert 长期授权 → 标记 ready 或 blocked → 写 submission 回执与审计事件 → commit。
commit 后才返回成功并唤醒执行。存储失败返回 503，不执行，不显示“已长期允许”。
部分拒绝属于正常业务结果，HTTP 200 返回 blocked 和已保存 grant 列表。

重复相同 submission_id/相同内容返回原回执，不重复执行；同 ID 不同内容返回 409。
首次提交必须覆盖全部待选项，拒绝未知 item、重复 item、缺项和过期 revision。
服务重启：长期 grant 保留；非终态请求标为 interrupted 并作废临时凭据，
已运行但未确认终态的步骤记录 outcome_unknown，先核对外部状态，不自动续跑。
审批已 commit 但 HTTP 回执丢失时，可读回/幂等提交确认已保存的决定；
这不授予重启后继续执行的权利。

结构化 JSONL 镜像包含请求/步骤/资源 ID、决定、命中规则、耗时、backend 返回码与结果引用。
权限决定的耐久事件随业务事务保存；JSONL 镜像失败单独记录，不逆转已提交决定。
不把模型口头“完成”当作 backend receipt。

## 9. 后端接口与事件提案

所有接口沿用现有 Web 身份和请求归属处理；environment/session 不从卡片自由文本推断。
返回码区分业务拒绝和协议失败，不靠前端猜测。

### 9.1 提交审批

沿用 POST /api/approvals/{approval_id}，协议版本 2：

```json
{
  "protocol_version": 2,
  "submission_id": "submission-001",
  "request_revision": 1,
  "decisions": [
    {"item_id": "item-enter", "choice": "allow_always"},
    {"item_id": "item-pick", "choice": "allow_once"}
  ]
}
```

服务端从 item_id 取权威 scope，客户端不提交可篡改的资源/action 来创建规则。
响应示例：

```json
{
  "approval_id": "approval-001",
  "request_id": "request-001",
  "request_status": "ready",
  "execution_started": false,
  "persisted_grant_ids": ["grant-enter"],
  "items": [
    {"item_id": "item-enter", "choice": "allow_always"},
    {"item_id": "item-pick", "choice": "allow_once"}
  ]
}
```

200 表示决定已保存，不代表动作完成；400/422 非法或缺失项；404 未知；
409 revision/提交冲突；410 已过期；503 存储不可用。身份错误沿用已有认证状态。

### 9.2 读与撤销

- GET /api/approvals/{approval_id}：读权威请求、逐项状态、实际流程状态，供重连/提交结果核实。
- GET /api/permissions/grants?resource_kind=object|area&status=active|revoked：
  返回 grant_id、资源显示数据、准确 ID、动作、授权/撤销时间与 revision；支持分页。
- POST /api/permissions/grants/{grant_id}/revoke：
  body 含 submission_id 和 expected_revision；幂等撤销，返回数据库提交后的记录。
- 列表只展示当前环境匹配的记录；历史环境不可用资源可通过历史视图解释，不误报当前可执行权限。
- 第一版 UI 提供撤销，不提供任意编辑范围；改授权通过撤销和下次重新批准完成。

### 9.3 事件

沿用 approval.requested、approval.resolved 名称，payload 增加 protocol_version=2、
request_id、revision、intent_summary、items、expires_at 和 request_status。
新增 permission.grant_changed 用于刷新列表，物理动作结果仍使用现有运行事件投影。
前端收到事件后可以重新 GET 对账，事件遗漏不造成永久错误列表。

旧 approve/reject bool 无法表达逐项长期授权，本版同时迁移所有确认接口实现与前端。
不得用 bool(structured_decision) 判断批准：非空的拒绝对象也会被转换为 true。
旧前端发单 outcome 必须明确拒绝并提示刷新版本，不隐式“全项允许”。
不建立长期双协议 mode。

## 10. 前端具体设计

### 10.1 申请卡

标题：“需要你的许可”
说明：“为了去卧室拿取杯子 A，需要以下权限。”
每行显示：资源名称、简短 ID/可选图片、准确动作、当前位置/当前落点说明。

| 申请事项 | 允许一次 | 以后都允许 | 拒绝 |
|---|---|---|---|
| 卧室 · 作为目的地进入 | 单选 | 单选 | 单选 |
| 杯子 A · 拿取 | 单选 | 单选 | 单选 |

每行初始未选择，全部明确选择后可“提交决定”，不默认预选长期允许。
提交中禁重复点击；失败保留选择并提供重试，不把失败显示为批准。
已有长期授权项在摘要中标“已长期允许”，不混成仍需选择的项。
显示持久化范围：“以后允许拿取这一个杯子，不包含放置或其他杯子。”
放置申请显示当前落点，并说明长期授权不限定落点。

部分拒绝提交后明确显示：“本次流程未执行；已保存：卧室进入权限。”
全部允许显示：“权限已确认，等待执行”，随后依据运行结果显示完成或失败。
关闭/Escape 取消当前申请，不写任何尚未提交的长期选择。
无用户回复沿用明确超时时间，倒计时来自服务端 deadline，不由前端独立决定授权过期。

多请求使用按创建时间的待审批队列；一张卡对应一个请求，不跨请求合并。
断线后的 pending 请求按现有保守生命周期取消；重连查询终态，不恢复已取消卡片。
已经成功提交的长期选择不因浏览器断线丢失。

### 10.2 长期权限页

入口名称“权限”，分两个页签：

1. 物品动作：物品名称/图片、简短 ID、当前区域、允许动作、授权时间、撤销。
2. 目的地区域：区域名称、ID、“可作为目的地进入”、授权时间、撤销。

物品可按物品分组展示，但每个动作仍是一条独立可撤销 grant。
“撤销拿取”不能连带撤销该杯子的放置。
页面提示：“区域权限只检查目的地，不限制途经区域。”
空态：“尚无长期授权；首次执行时可以选择以后都允许。”
资源暂时不可识别显示状态，不自动改成同名物品。
撤销完成后读回后端状态，刷新后与新浏览器会话保持一致。
无须新增机器人选择、角色管理、工具列表或永久拒绝列表。

## 11. 配置和入口迁移

- 不把旧 allowed_tools、FULL_AUTO、tool.auto 或历史 approve 转成物品/区域长期规则。
- 所有正式物理执行入口都经过同一 PermissionChecker；无 UI 的调用缺授权时返回 approval_required，
  保持零副作用，不自动批准，也不一直等待不存在的用户。
- Web 是本版主要交付入口；现有 CLI/Feishu confirmation 实现同步新结构化契约。
  可交互入口用同一逐项选择语义；暂不支持呈现的入口明确返回 approval_channel_unavailable。
  不复制一套 channel 专属规则，不用 bool 兼容掩盖未迁移实现。
- 通用工具不再触发本版本的交互审批；既有非交互硬约束保持其用途，不扩展这轮权限产品。
- 现有 plan 执行限制仍可阻止副作用，但不产生第二类审批；FULL_AUTO/confirm 不控制家庭权限是否询问。
- 迁移完成时更新 config 示例和既有文档，明确旧模式对家庭权限没有跳过作用；
  不留可让第一次申请消失的隐式配置通路。

## 12. 验收：外部终态黑盒门

以下均是实施后的必跑门，本设计交付没有宣称已经运行通过。
每个场景独立断言 backend 返回码、准确目标终态和 HTTP/持久化结果，不用“任一成功”汇总。

| 用例 | 必须看到的外部终态 |
|---|---|
| 首次主动指令 | 审批前机器人位置/物品状态不变；卡片出现准确 ID 与动作 |
| 同名两只杯子 | 长期批准 A/pick_up 后 B/pick_up 仍申请；B 未被操作 |
| 同物品不同动作 | 批准 A/pick_up 后 A/clean 仍申请，未清洗 |
| 一次成功后重复 | 首次准确拿取成功；新拿取请求再次申请，旧凭据不可复用 |
| 有限重试 | 注入可确认无效果失败，重试同一目标；超过预算停止；换目标不放行 |
| 结果未知 | 注入已执行但回执丢失；核实实际状态，不能重复拿取/切割 |
| 长期跨进程 | 批准后重启服务、新会话查询同一 grant；同目标动作无新卡片并真实执行 |
| 部分拒绝 | 数据库保留明确长期批准项；位置与物品状态均未因被拒流程改变 |
| 区域一次 | 首次到达并停留；离开后再次以该区域为目的地需申请 |
| 只检查目的地 | 经过未授权中间区不新增申请；目的地区域缺权限仍阻止出发 |
| 物品移动 | 同 ID 换房间仍命中物品规则，新目的地缺授权需申请 |
| 放置换落点 | 新请求不同落点可命中 place 长期规则；需要开柜/进入区域时分别申请 |
| 内部导航/宏动作 | robot_manipulate 的自动导航与隐藏操作在批准前均为零；拒绝后真实位置不变 |
| 执行前撤销 | ready 后撤销 grant，下一步骤没有外部副作用；UI 刷新显示撤销 |
| 并发重复提交 | 同 submission 幂等一个决定、一个有效 grant、一次实际执行；冲突返回 409 |
| 存储故障 | 注入事务失败，503，重启读库无部分长期规则，设备没有开始 |
| 超时/断线/重启 | pending 不恢复成批准；临时授权失效；长期已提交规则保留 |
| 世界 ID 碰撞 | 两个 scene 同 cup 1 不继承；同名不同身份没有误操作 |
| 前端真实链路 | 浏览器逐项选择、提交、查看列表、刷新、撤销，再发动作重新申请，逐项与 DB 原始读回一致 |
| 所有 executor/handler | 每个实际构造入口均到达同一判断；接口 audit 覆盖全部实现；拒绝对象不被 bool 当允许 |

测试分层：
1. 纯规则测试：精确键、动作映射、状态机、逐项决定、事务幂等。
2. 集成测试：真实 SQLite、真实 HTTP/WebSocket、真实 serialization 和 application composition。
3. 外部黑盒：可控设备后端或仿真器，以独立状态读回验证位置/物品；真实浏览器验证 UI。
4. 正式运行 backend：先核实可用动作和稳定身份。仿真若没有区域模型，区域用例不得声称通过；
   应补有真实区域状态的后端验证，并准确注明各 backend 已验证的能力范围。
单测 mock 和同源 trace 不能代替 3/4；不能把测试 harness 自报成功作为物理成功。

## 13. 分阶段实施顺序（审阅后再写执行级计划）

这是重大改动：涉及公开审批协议、持久化结构及跨 runtime/工具/前端数据流。
本节为拆解方向，不是批准立即施工。

1. 验证关键前提：所有物理 backend 路径、稳定 ID、区域定义、真实动作、副作用闭包；
   锁定真实黑盒环境。先证明可识别同一物品和目的地区域，再写规则。
2. 数据与状态：实现 DTO/Store/精确匹配/原子提交/恢复/撤销及失败测试。
3. 执行接线：prepare、请求生命周期、同一 PermissionChecker、内部导航和 retry 的强制覆盖。
4. 协议与前端：同步全部 confirmation 实现、HTTP/事件、逐项卡片与长期权限页。
5. 外部验收：逐用例真实终态，修复根因，运行接口一致性 audit。
6. 文档交付：README、架构、用户指南、配置示例、CHANGELOG 同步；
   严重问题按仓库纪律写 pitfalls 与正向规则；最后更新活交接。

当前待验证的工程前提（不反复让用户选择已定产品规则）：
- 权威 object ID 是否跨同一家庭的重启保持；ALFWorld 重置的 identity epoch 怎样取得。
- 真实区域目录/当前位置 API 是否存在；不得把 receptacle 标签充当房间。
- 当前每一种宏动作会隐式调用哪些操作，以及能否拆出只读 prepare。
- intent/step 的现有 runtime 挂载点、取消与 deadline 的准确生命周期。
这些前提未通过时标为未验证，先调查；若必须改变产品语义或结构，带证据提交审阅。

## 14. 审阅重点与本次交付状态

已确认规则集中在第 2 节，不再把类别授权、多机器人或途经审批当候选。
明日建议按以下顺序审阅：

1. 第 6 节：当前流程与一次性授权的边界，是否符合实际使用。
2. 第 7 节：3 次总尝试的技术默认值、撤销/重启/未知结果行为。
3. 第 8—10 节：数据库、API 和前端是否能清楚呈现批准范围。
4. 第 11—12 节：现有入口迁移与外部终态验收是否覆盖实际后端。

本次仅生成设计文档与交接/索引记录，未实现数据库、API、前端或机器人行为。
未运行机器人、未发送审批请求、未宣称任何功能已经生效。
设计审阅通过后，基于当时工作区重新核对代码并编写正式实施计划。
