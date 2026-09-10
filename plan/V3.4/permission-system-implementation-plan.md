# V3.4 家庭资源权限 Implementation Plan

> **For agentic workers:** 使用 `executing-plans` 技能逐任务独立执行；遵守项目 AGENTS.md，默认不派生子 agent。所有步骤用复选框跟踪。用户本轮只要求实施计划，尚未要求开始编码。

**Goal:** 在现有 PermissionChecker 中实现单件物品 × 具体动作、目的地区域 × 进入的首次审批、一次允许、长期允许及前端撤销，并保持与 ALFWorld 解耦。

**Architecture:** 每次工具调用只读准备准确资源和实际副作用，现有 PermissionChecker 查询同一 PermissionStore；缺项走现有审批通道，提交后执行锁定绑定。真机和仿真分别提供设备适配实现，核心不依赖仿真类型，不建立全任务规划器或第二个策略引擎。

**Tech Stack:** 项目 Python 3.11 / Pydantic / asyncio / sqlite3 / FastAPI；现有 React / TypeScript / Vite / Vitest；pytest。依赖使用现有 uv.lock 和 web/package-lock.json，不新增数据库服务。

---

## 0. 计划基线、范围与执行纪律

- 设计真理源：[permission-system-design.md](./permission-system-design.md)，含 D01—D16。
- 基线：hkust4，`/home/haodong2/weilin/red_bird/Homemaster`，调查 HEAD `e4c7940f`。
- 已发现其他工作修改 `src/homemaster/benchmarking/alfworld/env_adapter.py`；不得覆盖、回退、混入本任务提交。
- 当前默认 node 为 v10.19.0；项目 .venv Python 为 3.11.13。前端运行时尚未绑定，Task 0 解决后才能声称前端门可运行。
- 本计划不把“去卧室拿杯子”当作一个必须预批的任务：先批准导航，后批准拿取；拒绝拿取不撤销已经完成的导航。
- 一张卡只合并当前工具调用内部的多个实际动作需求。界面只显示名称、位置、动作和必要图片，不显示内部 ID。
- 用户批准了进入实施规划；本轮仅交付文档。真正执行从 Task 0 开始。
- 执行时使用隔离 checkout/worktree；先核对当前 HEAD 和未提交工作，不能以隔离为由丢掉已有后端变更。
- 每一任务先失败测试、再单一实现、再验收；失败先定位根因，不调多种参数碰运气。
- 每次代码提交先把相同的“改什么、为什么、影响”写入 CHANGELOG；只提交当前任务准确文件。
- 正式功能交付与真机上线分开记录：独立适配契约通过不等于真机已经通过。

### 0.1 里程碑

| 里程碑 | 任务 | 可交付结果 |
|---|---|---|
| M0 | 0—1 | 工程前提和通用契约锁定，无 ALFWorld 核心依赖 |
| M1 | 2—4 | 真实数据库 + 检查器 + 当前调用执行门 |
| M2 | 5—7 | 三选项协议、Web 申请卡、长期权限页闭环 |
| M3 | 8 | ALFWorld 作为一个适配实现接入，明确未验证能力 |
| M4 | 9—10 | 独立外部验收和用户文档；真机按设备条件单列验收 |

执行依赖：0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10。
独立测试命令可并发；正常开发由一个执行者完成。任一门失败不得用后续汇总绿灯覆盖。

## 1. 文件职责和接口锁定

以下是计划新增路径；已有路径均已检查。任务中不重组无关目录。

| 新增文件 | 唯一职责 |
|---|---|
| src/homemaster/permissions/models.py | 通用不可变资源键、请求、决定、执行观察 DTO |
| src/homemaster/permissions/resources.py | 最小设备适配 Protocol 和动作目录加载；无仿真 import |
| src/homemaster/permissions/actions.yaml | 用户语义动作名称与准确别名，不含设备命令 |
| src/homemaster/permissions/store.py | 一个 SQLite Store，六张表、事务、状态与审计事件 |
| src/homemaster/benchmarking/alfworld/permission_adapter.py | 仅 ALFWorld 的身份、动作、执行绑定转换 |
| web/src/components/PermissionsPage.tsx | 长期授权查询、分组和逐条撤销 |
| web/src/components/PermissionsPage.module.css | 上述页面样式 |
| tests/homemaster/permissions/conftest.py | 精确资源/请求 fixture，不加载真实外部服务 |
| tests/homemaster/permissions/test_resource_contract.py | 类型、动作、身份与适配契约 |
| tests/homemaster/permissions/test_store.py | 真实 SQLite 原子性、恢复和并发 |
| tests/homemaster/permissions/test_physical_policy.py | 精确授权与硬拒绝优先级 |
| tests/homemaster/permissions/test_execution_gate.py | 当前调用与内部步骤、重试、撤销边界 |
| tests/homemaster/permissions/test_interface_audit.py | 所有实现签名、注册路径和依赖审计 |
| tests/homemaster/permissions/test_blackbox.py | 独立进程、HTTP 与数据库原始读回 |
| tests/homemaster/web/test_permissions.py | 新 API 的真实协议断言 |
| tests/homemaster/benchmarking/test_alfworld_permissions.py | 仿真专用契约验证 |
| web/src/components/PermissionsPage.test.tsx | 权限页交互 |
| scripts/verify_v34_permissions.py | 黑盒编排、证据清单和逐目标结论 |
| tests/fixtures/permissions/device_process.py | 不依赖 ALFWorld 的独立可控设备进程 |
| docs/permissions-user-guide.md | 用户实际操作说明 |
| docs/architecture/permissions.md | 当前实现的不变量、数据流与设备接入契约 |

优先修改现有文件：permissions/policy.py、config.py、__init__.py；
tools/base.py、adapters.py、executor.py；application/factory.py、runtime.py；
web/confirmations.py、app.py、schemas.py、event_projection.py、serve.py；
cli/confirmation.py；gateway/confirmation.py；
web/src/App.tsx、state/conversation.ts、protocol/events.ts、api/http.ts、ApprovalDialog.tsx 及对应测试。

六张数据库表保留设计中的划分，但都由一个 Store 实现，不拆六个服务。
资源感知与地图不新增到权限模块；通过设备适配器获取，沿用现有设备连接与 lease。

### 1.1 通用 DTO 的最小字段

下面为接口基准代码；实现时补齐设计约束校验，不改变名称和语义。所有 DTO 禁止额外字段并冻结。
JSON 持久化只保存通用字段；native 句柄由 adapter 持有，不把仿真对象写入通用请求。

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class FrozenDTO(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

class ResourceKey(FrozenDTO):
    environment_id: str = Field(min_length=1)
    resource_kind: Literal["object", "area"]
    resource_id: str = Field(min_length=1)
    action: str = Field(min_length=1)

class Requirement(FrozenDTO):
    item_id: str
    key: ResourceKey
    display_name: str
    location: str
    action_label: str
    step_ids: tuple[str, ...]
    image_ref: str | None = None

class PreparedStep(FrozenDTO):
    step_id: str
    binding_ref: str
    required_item_ids: tuple[str, ...]
    summary: str

class PreparedPhysicalRequest(FrozenDTO):
    request_id: str
    approval_id: str
    environment_id: str
    session_id: str
    run_id: str
    intent_id: str
    intent_summary: str
    revision: int = Field(ge=1)
    requirements: tuple[Requirement, ...]
    steps: tuple[PreparedStep, ...]
    target_snapshot_revision: str
    created_at: str
    deadline_at: str

class ItemDecision(FrozenDTO):
    item_id: str
    choice: Literal["allow_once", "allow_always", "reject"]

class ApprovalSubmission(FrozenDTO):
    protocol_version: Literal[2] = 2
    submission_id: str
    request_revision: int
    decisions: tuple[ItemDecision, ...]

class ApprovalResolution(FrozenDTO):
    approval_id: str
    request_id: str
    request_status: str
    execution_started: bool
    persisted_grant_ids: tuple[str, ...]
    items: tuple[ItemDecision, ...]

class ExecutionObservation(FrozenDTO):
    outcome: Literal["succeeded", "no_effect_failure", "unknown"]
    backend_code: str
    binding_ref: str
    observed_resource_id: str | None
    current_area_id: str | None
    evidence_ref: str
```

实现时 request_status 使用设计第 7 节列出的枚举；时间用 UTC aware datetime 校验并规范序列化。
验证集合闭包：所有 step 引用 item 都存在，所有 item 至少属于一个 step，
资源环境与请求一致，area 只允许 enter，动作必须在目录中，不允许通配符。
当前落点绑定保存在 adapter 的不可变 binding 中，不能因长期 place 不限落点而改变当前请求。

### 1.2 方法职责表

| 实现 | 方法 | 输入/输出与约束 |
|---|---|---|
| 设备适配 Protocol | prepare(call, context) | async → PreparedPhysicalRequest；只读且锁定 binding；无法明确目标抛 TargetUnresolved |
| 同上 | execute(binding_ref, context) | async → 现有 ToolResult；只执行已绑定准确目标 |
| 同上 | observe(binding_ref, context) | async → ExecutionObservation；不能隐式重试或移动 |
| 同上 | release(request_id) | async → None；终态/取消后释放 native 绑定，不删除审计记录 |
| PermissionStore | open(path) / close() | 建库、schema 校验和关闭；不启动任何设备 |
| 同上 | create_request(request) | 原子落请求/项/步骤；相同 ID 不同 payload 冲突 |
| 同上 | matching_grants(keys) | 返回准确 key → 有效 grant，不模糊匹配 |
| 同上 | submit(approval_id, submission, actor) | → ApprovalResolution；事务先 commit，后唤醒 |
| 同上 | get_request(approval_id) | 返回权威请求和状态 |
| 同上 | claim_step(request_id, step_id, revision) | 在启动/撤销顺序门内复核并记录开始；拒绝重复 claim |
| 同上 | finish_step(request_id, step_id, observation) | 保存结果、消费临时授权或确认可重试 |
| 同上 | list_grants(kind, status, cursor, limit) | 当前可信环境分页；不是客户端任意选环境 |
| 同上 | revoke(grant_id, submission_id, expected_revision, actor) | 幂等返回已提交记录 |
| 同上 | cancel(request_id, reason) / recover() | 作废未执行临时授权；不把未知执行重置成可重跑 |
| PermissionChecker | evaluate_tool(...) | 保留非交互硬检查，不再弹通用工具批准 |
| 同上 | evaluate_physical(request, context) | 同一个 checker 上判断所有物理必需项，返回准确缺项/拒绝理由 |
| confirmation handler | confirm(request, missing_item_ids, context) | async → ApprovalResolution；不返回 bool，不重新决定规则 |

Store 实例绑定现有可信 tenant 分区和 environment；即使两个家庭资源 ID 相同也不能串用。
表主键作用域的实际实现沿用现有身份隔离，不增加新的用户产品配置。
新接口签名必须在所有 checker/adapter/confirmation 实现的 audit 测试里逐个核对。

## Task 0：锁定工作区与验证入口

**Files:** 新建 plan/V3.4/backend-readiness.md；更新 docs/session-handoff.md。
这是调查记录，不写业务代码、不操作真实机器人。

- [ ] 读取 CLAUDE.md、相关 pitfalls（Web permission composition、线程取消和外部未知结果），记录本轮 HEAD/status。
- [ ] 创建隔离工作区；若其他未提交 backend 改动是必需前提，明确纳入来源后再合并，不能默默引用原目录。
- [ ] 运行下列调查命令，记录原始输出与退出码：

```bash
git status --short
git rev-parse HEAD
.venv/bin/python --version
.venv/bin/python -c 'import sys,sqlite3,pytest; print(sys.executable); print(sqlite3.sqlite_version)'
rg -n 'ToolExecutor\(|PermissionChecker\(' src/homemaster/application
rg -n 'go_to_target|manipulate_with_thor|force_toggle|step\(' src/homemaster/benchmarking/alfworld
rg -n 'robot_|observe' src/homemaster/domain/tools.py
node --version
```

- [ ] 检查 web/package-lock.json 中 vite/vitest/jsdom/typescript 的 engines；选择全部满足的 Node，
  放在项目忽略目录 .runtime/v34/node，绑定 .runtime/v34/node/bin。不得安装到系统全局。
  使用现有缓存优先；需要下载时实施当天从官方发行源核对版本和 SHA256，再记录确切版本/路径。
- [ ] 用锁定 Node 运行 npm ci，不改 package-lock；Python 使用项目 uv 管理，不裸 pip 升级。
  已核实 pyproject.toml 定义 optional-dependencies.dev；隔离工作区没有 venv 时运行
  `uv sync --frozen --extra dev`。使用当前项目绑定的 uv 路径，不安装全局包。
- [ ] backend-readiness.md 按后端逐项记录：
  稳定物品 ID、区域 ID/当前位置、只读 prepare、宏动作集合、执行返回码、外部观察、重启身份。
  每项填“已验证 + 证据路径”或“未验证 + 缺什么”；真机驱动不明不能写 ready。
- [ ] 门：通用核心开发不依赖 ALFWorld readiness；Task 8 的具体 backend 接线依赖自己的身份和动作门。
  缺真机不阻塞核心开发，但阻止宣称真机上线完成。

## Task 1：建立最小通用模型与声明式物理入口

**Files:** 新建 models.py、resources.py、actions.yaml、test_resource_contract.py、test_interface_audit.py；
修改 tools/base.py、tools/adapters.py、pyproject.toml（仅动作目录打包必要项）。

- [ ] 按 1.1 写失败测试，先证明资源键不同不会相等、无效动作/混合环境被拒绝。
- [ ] 加入以下具体回归：

```python
def test_exact_resource_identity():
    from homemaster.permissions.models import ResourceKey
    a = ResourceKey(environment_id="home", resource_kind="object",
                    resource_id="cup-a", action="pick_up")
    b = a.model_copy(update={"resource_id": "cup-b"})
    c = a.model_copy(update={"action": "clean"})
    assert a != b
    assert a != c
    assert a != a.model_copy(update={"environment_id": "other-home"})
```

- [ ] 在 models.py 定义 TargetUnresolved、TargetChanged、ApprovalConflict、ApprovalExpired、PermissionStorageUnavailable
  五种具名异常，分别对应目标澄清、绑定失效、409、410、503；异常不得当作普通成功结果吞掉。
- [ ] 定义资源适配器四方法契约；BaseTool 增加默认为 None 的 physical_adapter 字段。
  FunctionTool 构造与 from_registered_tool 包装必须显式透传同一实例；
  注册的物理工具缺 adapter 时拒绝注册/执行，不能当普通工具放行。
  物理工具以注册侧显式 metadata 声明，不用名字前缀或 LLM 参数猜。
- [ ] 动作目录准确配置 pick_up/take、place/put、open、close、clean、heat、cool、slice、turn_on、turn_off、enter。
  use/toggle 不直接映射，adapter 必须按实际状态解析为准确动作。
- [ ] 添加 AST 依赖审计：permissions 包禁止 import benchmarking、alfworld、THOR；
  同时检查通用模型无 scene/episode/native simulator 字段。
- [ ] 用以下命令观察失败后补齐实现，再运行到 PASS：

```bash
.venv/bin/python -m pytest tests/homemaster/permissions/test_resource_contract.py tests/homemaster/permissions/test_interface_audit.py -q
```

- [ ] 打包动作目录后从项目外 cwd import/load，证明 wheel 内资源存在；记录准确加载结果。
- [ ] CHANGELOG/commit：说明新增通用契约和物理入口声明，不宣称审批已生效。

## Task 2：实现一个 SQLite PermissionStore

**Files:** 新建 store.py、tests/homemaster/permissions/conftest.py、test_store.py；
修改 permissions/config.py、config/homemaster.example.yaml、.gitignore。

- [ ] conftest 建立 request 工厂：固定 environment=home、两个不同杯子、一间卧室，
  每次独立 request_id，默认一个 pick_up 步骤；组合用例额外加 enter 步骤和 item。
  时间由测试显式注入 clock，不使用 sleep 等过期。
- [ ] 先写真实 SQLite 测试：提交 once 后 grant=0，always 后 grant=1，reject 后 grant=0；
  组合 always+reject 返回 blocked 且 grant=1；同提交重试不重复，冲突 409 语义。
- [ ] 事务实现固定如下，不拆成先写 grant 再写决定：

```sql
BEGIN IMMEDIATE;
-- 在同一连接内核对 pending、revision、deadline、全部 item 和 submission。
-- 写逐项决定、长期授权、请求状态、submission 回执和 permission_events。
COMMIT;
```

- [ ] 创建设计第 8 节六张表与 schema_version=1；有效 grant 的唯一约束：

```sql
CREATE UNIQUE INDEX active_permission_scope
ON permission_grants(environment_id, resource_kind, resource_id, action)
WHERE revoked_at IS NULL;
```

- [ ] create_request 的通用快照不包含 native handle；环境目录查找不复制成新的授权真理源。
- [ ] SQLite 路径增加 permissions.store_path（配置路径，不是新增审批 mode）。
  默认从 `Path(config.observability.session_dir).expanduser().parent / 'permissions.sqlite3'` 派生，
  再应用既有 tenant 分区；显式 store_path 优先。example 只放占位/相对目录，不写真实个人路径。
- [ ] 实现 1.2 所有 Store 方法；claim/finish、恢复、撤销采用设计状态机。
  持久事件同事务写，JSONL 用已有事件设施镜像，不建新后台审计服务。
- [ ] 在插入第二个 item 后注入异常，重新开连接 raw SELECT 所有表，断言没有半笔提交。
  开两个连接交错提交/撤销，逐条核对有效 grant、submission 和状态。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/homemaster/permissions/test_store.py -q
```

- [ ] 外部门：子进程退出后另一个 Python 进程直接 sqlite3.connect 查询有效规则和 PRAGMA integrity_check；
  必须返回 ok 且每个预期 key/行数准确，不通过 Store 自读冒充独立验证。
- [ ] CHANGELOG/commit：记录持久化、原子决定与恢复语义。

## Task 3：扩展现有 PermissionChecker，移除通用工具弹窗

**Files:** 修改 permissions/policy.py、permissions/__init__.py、tools/executor.py 中 checker Protocol；
新建 test_physical_policy.py；修改现有 test_home_policy.py。

- [ ] 先参数化 FULL_AUTO/DEFAULT、tool.auto、allowed_tools、read-only 标记的绕过用例：
  注册物理动作缺资源授权时所有变体都 ASK。
- [ ] 保留原 evaluate_tool 的非交互硬拒绝；取消“mutating tools require confirmation”的通用弹窗。
  PLAN 仍可阻止副作用；其余普通工具通过原有非交互检查后不申请物品规则。
- [ ] 在同一 PermissionChecker 新增 evaluate_physical：
  拒绝已取消/过期/目标失效请求；逐 item 匹配当前 once 或准确有效 grant；
  缺项返回它们的 item_id，不把一个 item 的允许扩散到其他项。
- [ ] 对 A/pick_up 的允许分别验证 B/pick_up、A/clean、其他环境 A/pick_up 均 ASK；
  A 改名/换房间仍命中物品授权，area 缺项仍保留。
- [ ] 对同一被拒用户意图增加当前运行的重复申请抑制：
  从 runtime 可信 user-turn identity + 准确 scope 构造键，不信模型传来的 intent_id。
  新的用户输入才允许再次申请；这不是永久 deny 表。
- [ ] 更新 AllowAllPermissionChecker：普通测试仍可显式使用；
  物理入口没有真实家庭 checker 时返回配置错误，不得自动允许。
- [ ] 运行旧/新策略测试，预期旧“普通写工具弹批准”的断言按新语义迁移：

```bash
.venv/bin/python -m pytest tests/homemaster/permissions/test_home_policy.py tests/homemaster/permissions/test_physical_policy.py -q
```

- [ ] CHANGELOG/commit：明确审批仅物品动作和目的地区域，旧工具授权不迁移成长期规则。

## Task 4：当前调用执行门、绑定、重试与资源生命周期

**Files:** 修改 tools/executor.py、application/factory.py、application/runtime.py、tools/adapters.py；
新建 test_execution_gate.py；修改 test_universal_executor.py、application/test_factory.py。

- [ ] 先写一个调用内部 enter+pick_up 的失败用例：审批 pending 或任一拒绝时两步均未启动。
  另写两个独立调用：导航允许并完成，拿取拒绝，位置仍在卧室但杯子没变。
- [ ] 执行接线遵循以下单一路径；已有 deadline/lease/cancellation 逻辑直接复用：

```text
validate tool args
→ evaluate_tool 硬检查
→ physical_adapter.prepare（若注册为物理工具）
→ store.create_request
→ checker.evaluate_physical
→ confirm 缺项并读取已提交决定
→ 每个内部 step：复核绑定 + claim_step + 现有 lease 下执行
→ adapter.observe + store.finish_step
→ adapter.release
```

- [ ] adapter 接入是工具真实执行路由；不得先 adapter.execute 再重复 tool.execute。
  普通工具沿用原执行路径。物理包装必须仍返回现有 canonical ToolResult，不丢 backend_attempted。
- [ ] 同一 current call 只 prepare 一次；审批等待后先只读确认绑定未变。
  目标/落点改变返回 target_changed，不重选杯子继续执行。
- [ ] 对单机器人开始动作与撤销，在同一应用 owner 上使用短启动互斥和 Step 状态 claim：
  锁内验证/claim 并把同一执行任务登记给 owner，然后释放锁；不能持锁等待整个机械动作结束。
  撤销先获得顺序时步骤拒绝，执行先获得顺序时标记已在进行，仍阻止后续步骤。
- [ ] 执行中取消不等于设备停止：保留 tracked task，按已有线程 owner 收回晚到结果；
  出现超时但 backend 可能执行则 unknown，禁止未经 observe 重试。
- [ ] 有限 retry 仅在当前步骤 no_effect_failure、目标不变且预算允许时进行，
  总尝试默认 3，与现有更严格预算取小值；成功立刻消费 once。
  claim_step 仅允许 ready 或已确认 no_effect_failure 的同一步重试；attempt_count 达上限拒绝，
  running/succeeded/unknown 不可再次 claim。
- [ ] 进入权限以当前位置与目标区域判断是否真的跨入；停留不新增 enter，
  离开后再回需要新请求。路径经过区域不生成 requirement。
- [ ] factory 注入 application-owned Store；start/recover 一次，close 一次；
  runtime 所有重建 executor 透传同一个 checker/Store/handler，不依赖默认构造。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/homemaster/permissions/test_execution_gate.py tests/homemaster/tools/test_universal_executor.py tests/homemaster/application/test_factory.py -q
```

- [ ] 外部门：由 Task 9 的独立设备进程读取位置/物品状态，确认阻止的是实际副作用。
- [ ] CHANGELOG/commit：记录当前调用执行门，不写成整个任务预批。

## Task 5：结构化审批服务、Web API 和其他入口迁移

**Files:** 修改 web/confirmations.py、schemas.py、app.py、event_projection.py、serve.py；
cli/confirmation.py、gateway/confirmation.py、gateway/runtime.py；
对应既有测试；新建 web/test_permissions.py。

- [ ] 先写旧 bool 陷阱测试：非空 reject 的 ApprovalResolution 不能被 bool 当成批准。
- [ ] 所有 handler 改用 1.2 confirm 签名；Web resolve 只调用 Store.submit，commit 后唤醒；
  Future 只传递提交完成信号，executor 从持久状态核对决定。
- [ ] Web 实现设计第 9 节三个新增接口和迁移后的提交接口：
  GET approval、GET grants、POST revoke；请求模型 extra=forbid。
  POST /api/approvals/{id} 只收 protocol_version=2、submission_id、revision、decisions。
- [ ] 实现准确状态码和测试：200 blocked（业务拒绝）、422 缺项/重复项/旧单 outcome、
  404 不存在、409 冲突、410 过期、503 落盘失败；越权沿用已有身份校验。
- [ ] 关闭卡片必须有显式取消协议：POST /api/approvals/{id}/cancel，
  body 为 submission_id/request_revision；原子取消 pending，不写未提交选择。
  已 resolved 的请求返回既有终态，不回滚已提交 grant；此接口是设计“关闭取消”的具体落点。
- [ ] approval.requested/resolved 保持事件名、版本 2 明细；新增 permission.grant_changed。
  raw backend 参数不用于卡片展示；事件数量与内容和权威结果一致。
- [ ] CLI 按每项读 1=once、2=always、3=reject；非法输入重问当前项，EOF/取消终止；
  全项明确后一次提交，不边读边落长期 grant。
- [ ] 本版 Feishu 同步结构化接口，但暂不扩建三选项卡片：
  无完整交互实现时确定返回 approval_channel_unavailable，物理动作不启动。
  保留既有连接归属校验，不把旧“同意”扩成所有项目的长期允许。
  这使用设计允许的明确不支持分支，用户指南必须写明；Web/CLI 完整交付。
- [ ] 单独审计全部 confirm 实现和调用方，不能只修改 Web。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/homemaster/web/test_permissions.py tests/homemaster/web/test_confirmations.py tests/homemaster/web/test_event_projection.py tests/homemaster/test_cli_confirmation.py tests/homemaster/gateway/test_confirmation.py tests/homemaster/permissions/test_interface_audit.py -q
```

- [ ] 外部门：真实 HTTP 提交后直接读 SQLite；重复提交不新增执行；断开 WebSocket 后 pending 取消。
- [ ] CHANGELOG/commit：记录协议破坏性变化、CLI 行为及 Feishu 明确边界。

## Task 6：前端逐项申请卡与请求状态

**Files:** 修改 web/src/components/ApprovalDialog.tsx、ApprovalDialog.module.css、
state/conversation.ts、protocol/events.ts、api/http.ts、App.tsx；
对应 App.test.tsx、components/components.test.tsx、state/conversation.test.ts、api/http.test.ts。

- [ ] 先写用户行为测试：初始没有默认允许选项；全部选择后才能提交；
  一项 always 一项 once 发送准确两个 item；拒绝一项显示本次调用未执行。
- [ ] TypeScript DTO 严格对应后端，不使用顶层单 outcome。关键选择状态：

```typescript
type Choice = 'allow_once' | 'allow_always' | 'reject'
type Decisions = Record<string, Choice>
const canSubmit = approval.items.every(item => decisions[item.item_id] !== undefined)
const payload = {
  protocol_version: 2,
  submission_id: submissionId,
  request_revision: approval.revision,
  decisions: approval.items.map(item => ({
    item_id: item.item_id,
    choice: decisions[item.item_id],
  })),
}
```

- [ ] submissionId 在一次提交尝试首次决定时锁定；网络重试复用同 ID。
  用户改选择后生成新 submission，但 resolved/expired 请求不能重新提交。
- [ ] 卡片显示 display_name/location/action_label；不渲染 ID、revision、cwd、tool name 或 JSON。
  图片失败保留可辨认名称位置，不回退成资源编号。
- [ ] Close/Escape 调用取消接口；提交中禁重复提交。
  pending 队列按服务器创建时间显示；刷新和断线重连 GET 对账，不把旧卡恢复成待批准。
- [ ] “已批准”与“执行完成”分别渲染；前一导航成功、后一拿取被拒时不说整个任务零执行。
- [ ] 运行（Task 0 已绑定 Node）：

```bash
PATH="$PWD/.runtime/v34/node/bin:$PATH" npm --prefix web test -- src/components/components.test.tsx src/App.test.tsx src/state/conversation.test.ts src/api/http.test.ts
PATH="$PWD/.runtime/v34/node/bin:$PATH" npm --prefix web run typecheck
```

- [ ] 用户可见 DOM 文本断言不出现 fixture ID；请求 body 仍包含准确 ID，不能删机器关联字段。
- [ ] CHANGELOG/commit：记录逐项卡片与按当前调用提示。

## Task 7：长期权限页与撤销闭环

**Files:** 新建 PermissionsPage.tsx、PermissionsPage.module.css、PermissionsPage.test.tsx；
修改 App.tsx、api/http.ts、相关测试。

- [ ] 先写页面行为测试：两个页签，杯子按动作展示；撤销 pick_up 不影响 place；
  重名显示位置，不显示编号；无记录显示准确空态。
- [ ] API 获取分页 grant 列表；选择撤销后 POST revoke，成功再读回列表。
  不采用只改本地数组就宣称撤销完成的实现。
- [ ] 组件关键加载/撤销约束：

```typescript
type RevokeOperation = {
  grantId: string
  submissionId: string
  expectedRevision: number
}
function prepareRevoke(grant: Grant): RevokeOperation {
  return { grantId: grant.grant_id, submissionId: crypto.randomUUID(),
    expectedRevision: grant.revision }
}
async function submitRevoke(operation: RevokeOperation) {
  await api.revokeGrant(operation.grantId, {
    submission_id: operation.submissionId,
    expected_revision: operation.expectedRevision,
  })
  await reloadGrants()
}
```

Grant 定义在 api/http.ts，字段来自设计 API；reloadGrants 为当前页 kind/status 的重新 GET，
保留当前查询参数和服务端分页游标规则。网络重试复用已生成的 submission_id，不重新随机生成。
prepareRevoke 仅在用户首次点击撤销时调用并保存结果；重试只调用 submitRevoke(同一 operation)。

- [ ] 页面明确说明“区域权限只检查目的地，不限制途经区域”。
  permission.grant_changed 到达刷新当前数据；新浏览器会话仍从后端读。
- [ ] 运行：

```bash
PATH="$PWD/.runtime/v34/node/bin:$PATH" npm --prefix web test -- src/components/PermissionsPage.test.tsx
PATH="$PWD/.runtime/v34/node/bin:$PATH" npm --prefix web run build
```

- [ ] 外部门留给 Task 9：真实浏览器撤销、刷新、重新动作申请，三个终态逐个核对。
- [ ] CHANGELOG/commit：记录长期权限查看与逐动作撤销。

## Task 8：ALFWorld 只作为适配器接入

**Files:** 新建 benchmarking/alfworld/permission_adapter.py、test_alfworld_permissions.py；
修改 benchmarking/alfworld/tools.py、grounding.py、adapters/profiles.py；
必要时修改 env_adapter.py，必须先处理该文件的其他现有修改来源。

- [ ] 检查 Task 0 readiness，逐动作锁定权威资源身份和区域模型；
  不可用能力返回明确 unsupported，不制造房间 ID 或宣称真机兼容已验证。
- [ ] adapter.prepare 只读解析 native_ref，映射通用 ResourceKey 和锁定步骤；
  ground_text 的 judge 结果须对应唯一权威物品，不能直接把名称当稳定授权 ID。
- [ ] 从 _exec_manipulate 提前声明内部导航和操作；execute 消费 binding，
  不再调用会重新选择同名目标的 grounding。
- [ ] take/put 等仿真动作映射在本 adapter 内；permissions 不 import translator。
  scene/episode ID 只参与本 adapter 的 environment 映射，不扩散到通用 schema。
- [ ] 底层宏动作会开柜、开电器等时逐项声明；无法声明完整效果就拒绝该宏动作，
  不用“工具只有一个”掩盖多个用户语义动作。
- [ ] 运行：

```bash
.venv/bin/python -m pytest tests/homemaster/benchmarking/test_alfworld_permissions.py tests/homemaster/permissions/test_interface_audit.py -q
```

- [ ] 对稳定 ID/跨 scene/内部导航/未知回执逐项真实后端验证；
  每个测试记录 backend 原始返回码与环境独立状态，不拿通用测试进程结果充作 ALFWorld 结果。
- [ ] CHANGELOG/commit：只声明实际验证的仿真能力；真机仍需 Task 9 实机门。

## Task 9：独立外部黑盒验收与真机接入门

**Files:** 新建 tests/fixtures/permissions/device_process.py、
tests/homemaster/permissions/test_blackbox.py、scripts/verify_v34_permissions.py；
输出 reports 到项目忽略的独立 runtime 目录；
新建 plan/V3.4/acceptance-report.md（仅保存去敏结论和证据引用）。

- [ ] 先建立独立设备进程：仅 Python 标准库，JSONL stdin/stdout 协议，
  管理 area、两个不同杯子的状态和操作计数；命令仅 reset/read/move/pick_up/place/fail_next。
  不 import homemaster、不 import ALFWorld、不调用 PermissionChecker。
  read 返回完整状态，副作用命令返回明确 code。
- [ ] 测试适配器通过子进程协议实现 Task 1 契约； verifier 直接 read 子进程，
  不经过 adapter.observe 来验证 adapter 自己的假设。
  这个设备是可控外部测试系统，不是真机；报告必须明确区分。
- [ ] test_blackbox 逐目标执行：
  首次审批前零变化；A 批准不影响 B；A 拿取不包含清洗；
  once 成功后新调用再申请；有限确认失败；unknown 不重发；
  组合部分拒绝本调用零变化；两个独立调用保留早先导航；
  destination-only；离开再入；物品换房间；place 换落点；
  撤销与 claim 交错；重复提交；事务失败；断线/取消/重启。
- [ ] 真实服务启动后以 HTTP/WebSocket 完成批准，并直接 sqlite3 读库核对；
  关闭服务、全新进程重启、新会话复用长期规则，验证无新申请且真实动作发生。
- [ ] 用真实浏览器完成一轮：
  查看待批 → 逐项选择 → 提交 → 权限页 → 刷新 → 撤销一个动作 →
  重发动作出现申请。截图保存卡片和列表，DOM 判读无内部 ID，
  API 状态和 raw DB 逐条一致。
- [ ] verifier CLI 定义如下，创建时锁定唯一证据目录，目录已存在则失败：

```bash
.venv/bin/python scripts/verify_v34_permissions.py --backend process --output .runtime/v34/acceptance/process-001
```

输出 manifest 含：schema_version、源码 commit、环境版本、每个 case/target 的 HTTP code、
backend code、before/after 原始状态引用、SQLite raw readback 引用、stderr 检查与 PASS/FAIL。
退出码 0 只允许全部必需 case/target 都 PASS；unsupported/缺证据不能计作 PASS。
目录不得重复复用；上面的名称只适用于首次，重跑一次性选择新的编号。

- [ ] 完整回归命令：

```bash
.venv/bin/python -m pytest tests/homemaster/permissions tests/homemaster/web tests/homemaster/tools/test_universal_executor.py tests/homemaster/application/test_factory.py tests/homemaster/test_cli_confirmation.py tests/homemaster/gateway/test_confirmation.py tests/homemaster/benchmarking/test_alfworld_permissions.py -q
PATH="$PWD/.runtime/v34/node/bin:$PATH" npm --prefix web test
PATH="$PWD/.runtime/v34/node/bin:$PATH" npm --prefix web run build
.venv/bin/python -m ruff check src/homemaster/permissions src/homemaster/tools/executor.py src/homemaster/web tests/homemaster/permissions
git diff --check
```

除此以外，Task 0 登记的所有本次修改文件都加入 lint；collection 清单逐文件对照任务表，
不能仅凭 pytest 汇总数量推断前置测试已收集。

- [ ] 真机上线门：取得真实驱动、地图/物品档案、物品 ID 与当前区域接口后，
  实现同一 Task 1 适配契约，使用同一核心和前端运行上述适用场景；
  实际读取物品/位置终态并核对设备返回码。授权真实操作之前按用户现场操作范围执行。
  没有硬件或驱动证据时报告“真机未验收”，不能给真机 PASS。
- [ ] 进程测试、ALFWorld、真机分别有独立结果栏；关闭时每个 owned 进程/socket 都结束且 stderr 无迟到异常。
- [ ] CHANGELOG/commit：只陈述实际通过的外部终态门，缺硬件如实记录。

## Task 10：文档、迁移说明与交接

**Files:** 新建 docs/permissions-user-guide.md、docs/architecture/permissions.md；
修改 README.md、docs/architecture/application-runtime.md、docs/web-console-user-guide.md、
docs/skills-and-config-user-guide.md、config/homemaster.example.yaml、
CHANGELOG.md、docs/session-handoff.md、plan/V3.4/README.md。

- [ ] 用户指南用“去卧室拿杯子”真实例子讲清导航先审批、拿取后审批；
  三选项、同件不同动作、目的地-only、永久保存与撤销、失败/重试提示。
- [ ] 架构文档写准确最终接口和实际数据流；不复制已被实现替换的提案。
  单列真机接入：prepare/execute/observe/release、稳定身份、未知结果处理；
  不要求实现 ALFWorld scene/episode 或读取 benchmark 元数据。
- [ ] 旧 confirm/full_auto 文案统一更新：不能跳过家庭资源权限；
  普通工具不再额外弹批准。CLI 完整支持；Feishu 本版未支持逐项交互时明确告知。
- [ ] README 只列真实完成能力，真机独立验收结果从 acceptance-report 引用。
- [ ] 若发现严重假阳性或多次错误修复，按仓库规则补 pitfalls 与 CLAUDE 正向规则；无新坑不编造记录。
- [ ] 对设计 D01—D16 和下表逐项核对；无缺口后写最终交接：
  当前 commit、测试命令/退出码、外部证据、已完成、真机缺失条件、下一步。
- [ ] CHANGELOG 文本与最终 commit message 内容一致；不提交机密、数据库、截图原始隐私数据或他人修改。

## 附录 A：设计覆盖矩阵

| 设计要求 | 任务 | 主要独立证据 |
|---|---|---|
| D01/D02 单件×动作 | 1/2/3/9 | 不同物品与动作逐条 raw DB、外部状态 |
| D03 语义动作 | 1/8 | 宏动作 effect 闭包、注册审计 |
| D04 once/retry/unknown | 2/4/9 | 实际操作次数与丢回执后状态 |
| D05/D13 移动与放置 | 3/4/9 | 同物品不同房间/落点，区域分别检查 |
| D06/D09 区域 | 4/9 | 到达、离开、再次进入，途经不问 |
| D07/D08 当前调用逐项批准 | 4/5/6/9 | 组合调用与分开调用的不同外部终态 |
| D10 单机器人 | 4/7 | 无多机器人 UI，单执行 owner |
| D11 ID 内部、名称展示 | 1/6/7/9 | DOM 不显示 ID，backend 仍准确绑定 |
| D12 持久和撤销 | 2/5/7/9 | 重启、新会话、撤销后再次申请 |
| D14 主动指令仍申请 | 3/4/9 | 自然语言指令不产生隐含 grant |
| D15 一个 checker | 3/4/5 | 所有构造入口和 handler audit |
| D16 真机解耦 | 1/8/9/10 | AST 依赖门、独立进程、真机单列验收 |
| 原子性/重连/协议迁移 | 2/5/6/9 | HTTP code + 独立 raw readback |
| 文档同步 | 10 | 实现、用户指南、配置与 CHANGELOG 对照 |

## 附录 B：本计划交付检查

本节由写计划者核对，执行任务仍保持未勾选：

- [x] 已按最新设计采用“当前工具调用”，未恢复旧全任务预批。
- [x] 已明确普通工具无额外交互批准，保留非交互既有约束。
- [x] 已覆盖 D01—D16，真机核心与仿真适配分离。
- [x] 已给准确文件、执行顺序、失败用例、验证命令和外部终态门。
- [x] 已记录实际 Node/Python 前提，不把计划命令写成已通过测试。
- [x] 已定义取消接口与旧协议失败行为，避免 UI 关闭只有本地效果。
- [x] 已明确没有硬件时不宣称真机可用。
- [x] 正常实施独立执行，不主动拆给子 agent。

计划编写不安装依赖、不启动服务、不操作机器人；功能测试将在执行阶段运行。
