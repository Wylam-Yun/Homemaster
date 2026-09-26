# HomeMaster V3.5 架构重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 每个任务用 checkbox 跟踪，并在阶段门通过后再进入下一阶段。

**Goal:** 在不重写 `ApplicationRuntime`/`AgentRuntime` 的前提下，把 HomeMaster 收敛到唯一的 application composition、唯一 Agent loop、唯一 V1.8 `AlfredThorEnv` Oracle Harness、canonical Tool 协议和两个真正隔离的 Python 环境，并用可复核的外部终态证据完成交付。

**Architecture:** CLI、Web、Feishu 和 ALFWorld benchmark 只负责入口协议、认证、session/profile 绑定，统一调用 `homemaster.application.composition` 创建 `ApplicationRuntime`。Runtime 只执行通用 Agent loop；ALFWorld 语义集中在 `homemaster.alfworld` Harness，独立 worker 只加载 ALFWorld/AI2-THOR，通过版本化 stdin/stdout NDJSON 与主环境通信。MindMemOS 安装在通用 `.runtime/venv`，不建立第三个 Python 环境。

**Tech Stack:** Python 3.11-3.13、`uv`/锁文件、Pydantic dataclass contracts、`ApplicationRuntime`/`AgentRuntime`、ALFWorld + AI2-THOR 2.1.0、Neo4j/Java/Unity/Xvfb、pytest/pytest-asyncio、ruff、JSONL trace、NDJSON IPC。

---

## 0. 约束、证据和基线

> 执行状态（2026-09-26）：Phase 0-5 的实现与主要 gate 已完成。单 episode、bounded
> provider-backed taskset CLI、真实失败黑盒和 action/close SIGINT shutdown 均有逐实例外部证据；
> taskset runner 也保存 worker close/exit/stderr 生命周期证据。gateway smoke、package-data/release
> audit、setup-alfworld probe、通用 Linux setup 和 architecture cross-reference 已补齐；
> CHANGELOG/message 同源 commit 门也已执行。
>
> 2026-09-26 follow-up：单 episode 和连续 taskset runner 均已接入
> `WorkerAlfworldAdapter`；远端 worker black-box 已覆盖 reset、frame、set_task、动作和
> close。随后修复了 worker adapter 丢失 V1.8 `allow_offscreen_object_navigation`
> 配置、trial plan object grounding 和冻结 off-screen pose 的缺陷；真实 worker 黑盒和
> source-backed formal CLI 均以 `won=true` 通过。

### 当前缺口统计（2026-09-26）

本次复核后，计划 checkbox 为 **56/56 已勾选、0/56 未勾选**。

| 类别 | 状态 | 证据/缺口 |
|---|---|---|
| Application Composition、canonical Tool、Harness contract | 已完成 | 本地 focused tests、Ruff、`verify_v35_architecture.py` |
| NDJSON schema、worker isolation、package data | 已完成 | wheel/sdist package-data audit 通过；远端 setup/doctor `16/16 PASS`，MindMemOS origin 在 `.runtime/venv` |
| 单 episode worker 接入 | 已完成 | `phase-5/live-worker-use-20260926/`：reset、take/use、独立 THOR 状态和 close 均逐项通过 |
| taskset 连续 `set_task` worker 化 | 已完成 | runner 已走 `WorkerAlfworldAdapter` + `set_task`；scene digest、返回码、close 和 bounded provider-backed CLI 证据已保存 |
| 主 CLI formal summary | 已完成 | `phase-5/live-cli-grounding2-20260926/` 和 `phase-5/taskset-cli-single-20260926c/`：per-instance `agent_success`、goal rate 1.0、provider/runtime/harness 1.0 |
| action/close SIGINT 黑盒 | 已完成 | `phase-5/sigint-20260926/sigint.json`：两种边界均有 receipt、返回码、worker/descendant cleanup 和 stderr 证据 |
| 真实失败黑盒 | 已完成 | `phase-3/failure-live-20260926b/failure.json`：target unresolved 与 backend failure 逐实例通过 |
| 旧测试/fixture 逐文件清理 | 已完成 | NDJSON live 测试已重命名；TextWorld、HTTP-only、pre-canonical 删除门和 V3.5 focused collection 均通过 |
| clean checkout、正式 runtime setup、最终发布门 | 已完成 | `verify_v35_release.py --json`、wheel/sdist、package-data、worker isolation 均 PASS；远端 `setup.sh` 使用 uv 0.11.19 完成并 doctor `16/16 PASS` |
| 远端 doctor / runtime 绑定 | 已完成 | 远端 doctor `16/16 PASS`；通用 runtime、MindMemOS 读写、worker binding/IPC、runtime ignore 均通过 |
| 计划与代码 checkbox 同步 | 已完成 | architecture target-design、phase-5 evidence、README/guide、CHANGELOG 已同步；commit message 与 CHANGELOG 顶部交付条目同源 |

当前没有未勾选步骤。代码、测试、远端 setup、release evidence 和提交前 CHANGELOG/message 同源门均已完成。

本次复核还确认：taskset CLI 的 `_run_taskset()` 在 `use_isolated_worker=True` 时构造
`WorkerAlfworldAdapter`；`scripts/verify_v35_taskset_worker_live.py` 已证明 reset、连续
`set_task`、scene digest、动作和 close 的 worker 外部终态。单 episode formal CLI 的成功终态见
`phase-5/live-cli-grounding2-20260926/`；bounded taskset CLI success 和同 run 的 worker lifecycle
`worker.json` 见 `phase-5/taskset-cli-single-20260926c/`。

### 0.1 真理源和当前代码落点

- 架构决策真理源：`plan/V3.5/architecture-problems-and-target-design.md`。
- 层次与依赖方向真理源：`architecture/home-master-layer-map.md`。
- 当前公共组装入口：`src/homemaster/application/composition/base.py` 的 `compose_application()`。
- 当前 Runtime seam：`src/homemaster/application/runtime.py:244` 的 `ApplicationRuntime`，创建辅助在 `src/homemaster/application/factory.py:53`。
- 当前 canonical Tool seam：`src/homemaster/tools/contracts.py`、`src/homemaster/tools/executor.py` 和 `src/homemaster/adapters/profiles.py`。
- 当前 ALFWorld 分层：`src/homemaster/alfworld/harness.py`、`alfworld/benchmark/episode.py`、`taskset.py`、`runner.py`。
- 当前跨环境 transport：`src/homemaster/alfworld/worker_client.py` 和 `workers/alfworld_worker/` 的版本化 stdin/stdout NDJSON。
- 当前环境绑定：`scripts/setup.sh` 建立 `.runtime/venv`，`scripts/setup-alfworld.sh` 建立 `.runtime/alfworld-venv` 并写入 binding。

### 0.2 不变量（实现中每阶段都必须保持）

1. `ApplicationRuntime` 不 import `homemaster.alfworld`；`AgentRuntime` 不出现 THOR/objectId/scorer 领域判断。
2. 非 CLI 入口不 import `homemaster.cli.composition`；公共创建只能从 `homemaster.application.composition` 到达。
3. `ToolDefinition -> RegisteredTool -> async executor -> ToolExecutionResult` 是唯一运行时协议；不存在同步 executor fallback、alias、adapter 或旧导出。
4. 只支持 `AlfredThorEnv`。源码、配置、测试和文档不再提供 TextWorld 或 legacy THOR 路径。
5. 每个外部动作的验收必须同时拿到：外部返回码为成功；独立读取的真实外部状态满足预期。模型文本、trace “开始执行”或非空图片都不能代替终态。
6. 多 trial/taskset 验证按 `trial_id`、`episode_id`、`subtask_index` 分别断言；不得用 `any()`、全局最高分或“至少一个成功”做门。
7. worker stdout 只能输出协议帧，第三方日志只能写 stderr；关闭后 worker、Unity/Xvfb 子进程全部消失且 stderr 不含 traceback。
8. 每轮循环首次决定的目标（run id、trace root、worker request id、scene pose）必须缓存，重试只重发同一值。

### 0.3 基线任务（先失败测试，再迁移）

**Files:**
- Create: `tests/homemaster/v35/test_architecture_boundaries.py`
- Create: `tests/homemaster/v35/test_worker_protocol_contract.py`
- Create: `tests/homemaster/v35/test_v35_audit_commands.py`
- Read only: `plan/V3.5/architecture-problems-and-target-design.md`, `architecture/home-master-layer-map.md`

- [x] **Step 1: 建立工作分支并保存工作树状态。**

```bash
git switch -c refactor/v35-architecture
git status --short --untracked-files=all > plan/V3.5/baseline-git-status.txt
git rev-parse HEAD > plan/V3.5/baseline-commit.txt
```

Expected: 分支从当前 checkout 创建；不得覆盖用户已有的 `plan/V3.5/architecture-problems-and-target-design.md` 修改或 `architecture/home-master-layer-map.md` 未跟踪文件。

- [x] **Step 2: 记录当前可复现基线，不把已知失败伪装成通过。**

```bash
uv run pytest -q tests/homemaster/application tests/homemaster/tools tests/homemaster/benchmarking/test_alfworld_*.py
uv run ruff check src tests scripts
python scripts/guard_no_legacy_terms.py
```

Expected: 保存三条命令的完整 stdout/stderr、退出码和 collected 用例清单到 `plan/V3.5/baseline-test-output.txt`；当前会暴露旧协议和旧入口，失败项逐项登记，不允许只记录总通过数。

- [x] **Step 3: 写 import/协议边界的失败测试。** 测试必须用 AST/importlib 检查：所有非 `application/composition` 模块不引用 `cli.composition`；`ApplicationRuntime` 不引用 `homemaster.alfworld`；worker source 不含 `import homemaster`/`import mindmemos`；所有公开 registry entry 的 `executor` 为异步 callable。

```python
def test_non_cli_entries_do_not_import_cli_composition() -> None:
    forbidden = "homemaster.cli.composition"
    for path in source_files("src/homemaster"):
        if path.parts[-2:] == ("cli", "composition.py"):
            continue
        assert forbidden not in path.read_text(encoding="utf-8")
```

Run: `uv run pytest -q tests/homemaster/v35/test_architecture_boundaries.py`
Expected: FAIL，并指出至少 `web/serve.py`、`benchmarking/locomo/runner.py`、`adapters/alfworld_entry.py` 或 CLI 辅助仍依赖旧入口。

- [x] **Step 4: 建立阶段证据目录。** 每个阶段只接受以下目录中的证据：`plan/V3.5/evidence/phase-<n>/`；远端 live 阶段仍需补齐 per-trial JSON 和外部状态快照。

**Phase 0 gate（必须全部满足）**

- 基线命令已记录真实退出码和测试收集集合。
- 失败测试确实因当前架构边界失败，而不是测试本身导入错误。
- `git status` 中用户已有改动仍可区分，未被基线脚本改写。

---

## 1. 抽出唯一 Application Composition

### 1.1 建立 composition 包和明确输入输出

**Files:**
- Create: `src/homemaster/application/composition/__init__.py`
- Create: `src/homemaster/application/composition/base.py`
- Create: `src/homemaster/application/composition/providers.py`
- Create: `src/homemaster/application/composition/tools.py`
- Create: `src/homemaster/application/composition/skills.py`
- Create: `src/homemaster/application/composition/memory.py`
- Create: `src/homemaster/application/composition/observability.py`
- Create: `src/homemaster/application/composition/profiles.py`
- Modify: `src/homemaster/application/factory.py`（只保留内部 runtime wiring helper）
- Modify: `src/homemaster/application/__init__.py`
- Test: `tests/homemaster/application/test_composition.py`

- [x] **Step 1: 在 `base.py` 定义唯一 composition API，不让入口传入 Provider/ToolExecutor。** 输入至少包含 `HomeMasterConfig`、profile、world/memory path、runtime/session root、tenant、event sink、permission/confirmation handler、MCP connector；输出复用现有 `HomeApplicationBundle` 字段，但类型移动到 application composition，避免 CLI 私有类型成为公共契约。

```python
@dataclass(frozen=True)
class ApplicationCompositionRequest:
    config: HomeMasterConfig
    profile: Literal["local_robot", "browser", "alfworld"]
    runtime_root: Path
    session_root: Path | None = None
    world_path: Path | None = None
    memory_path: Path | None = None
    memory_tenant_id: str = "local"
    event_sink: Any | None = None
    mcp_connector: Connector | None = None
    permission_mode: PermissionMode | None = None
    confirmation_handler: Any | None = None
    publish_artifacts: bool = False

def compose_application(request: ApplicationCompositionRequest) -> HomeApplicationBundle:
    """Compose resources; do not start provider connections or run an agent."""
```

- [x] **Step 2: 把 `cli/composition.py:287-638` 按职责搬入模块。** `providers.py` 只构造 `ProviderFactory`；`tools.py` 只构造 ToolRegistry 和 MCP/extension registration；`skills.py` 只加载 SkillRegistry；`memory.py` 只构造 MindMemOS、Neo4j、queues、ledger；`observability.py` 只构造 trace/artifact/EventBus sinks；`profiles.py` 只把 profile 映射到工具和 browser/ALFWorld binding；`base.py` 组装 `ApplicationRuntime` 和 application `RunResourceScope`。

- [x] **Step 3: 保持 `application.factory.create_application()` 为 composition 内部 helper。** 只有 `application/composition/base.py` 可以调用它；`factory.py` 不扫描配置、不判断入口、不 import ALFWorld。对外仅从 `homemaster.application.composition` 导出 `compose_application` 和 request/bundle 类型。

- [x] **Step 4: 让 composition 只组装不运行。** 测试创建 bundle 后断言 provider 未连接、ALFWorld worker 未启动、session 尚未创建；随后显式调用 `bundle.application.start()`/`close()` 验证 resource scope 的 application lifetime。

### 1.2 迁移所有入口

**Files:**
- Modify: `src/homemaster/cli/run_command.py`, `interactive_shell.py`, `gateway_command.py`, `dry_run.py`
- Modify: `src/homemaster/web/serve.py`
- Modify: `src/homemaster/benchmarking/locomo/runner.py`, `benchmarking/alfworld/runner.py`
- Modify: `src/homemaster/browser/application.py`, `src/homemaster/gateway/*.py`
- Delete: `src/homemaster/cli/composition.py`（完成全部迁移后删除，不保留 re-export）
- Test: `tests/homemaster/integration/test_entry_parity.py`, `tests/homemaster/test_import_boundaries.py`, `tests/homemaster/v35/test_architecture_boundaries.py`

- [x] **Step 1: 按入口建立薄 adapter。** CLI adapter 只解析参数和绑定 `HomeCliBackend`；Browser adapter 只绑定 browser session/capability；Feishu/Gateway 只绑定认证、channel/session；ALFWorld benchmark 只传 `profile="alfworld"` 和 Harness/worker 依赖。它们都调用 `compose_application(request)`，不能构造 `LLMClient`、`ToolRegistry`、`AgentRuntime`。

- [x] **Step 2: 修改非 CLI 入口的 import。** `rg -n 'homemaster\.cli\.composition' src tests scripts --glob '!**/__pycache__/**'` 必须只剩迁移中的测试引用；完成后删除该文件并让 import 直接失败，防止未来恢复兼容入口。

- [x] **Step 3: 保持 profile 行为不变但移除入口判断。** Browser 的 prompt 选择在 `profiles.py`；ALFWorld 的 terminal owner、environment binding 作为 `RunRequest.dependencies` 进入 Runtime；Runtime 内不出现 `if profile == "alfworld"` 的领域动作分支。

**Phase 1 tests and gate**

```bash
uv run pytest -q \
  tests/homemaster/application/test_composition.py \
  tests/homemaster/application/test_factory.py \
  tests/homemaster/application/test_application_runtime.py \
  tests/homemaster/integration/test_entry_parity.py \
  tests/homemaster/test_import_boundaries.py \
  tests/homemaster/browser/test_application.py \
  tests/homemaster/gateway/test_runtime.py
uv run python -m homemaster.cli.app doctor --json
```

Expected: 指定文件全部被 pytest 收集；composition bundle、CLI/Web/Browser/Gateway 的 runtime 结果在同一 `RunResult` 契约下；doctor 返回 0 且 JSON 中每个 check 都为通过。黑盒门还要在临时 `runtime_root` 前后比较文件树：composition 未 start 前不能创建 provider/network/worker 外部资源，close 后 application-owned resource 数为 0。

失败判据：任一入口直接 import `cli.composition`、Runtime 直接 import ALFWorld、未调用 `application.close()` 留下 resource、或 doctor 只检查日志不检查实际文件/进程，都判 Phase 1 FAIL。

---

## 2. Tool canonical 协议硬切

### 2.1 让所有正式工具直接生产 `RegisteredTool`

**Files:**
- Modify: `src/homemaster/domain/tools.py`
- Modify: `src/homemaster/task_state/tools.py`
- Modify: `src/homemaster/benchmarking/alfworld/tools.py`
- Modify: `src/homemaster/tools/base.py`, `contracts.py`, `adapters.py`
- Modify: `src/homemaster/adapters/profiles.py`, `src/homemaster/application/tool_executor.py`
- Test: `tests/homemaster/tools/test_definition.py`, `test_execution_result.py`, `test_universal_executor.py`, `test_universal_registry.py`, `test_tool_registry.py`

- [x] **Step 1: 以 `tools/contracts.py` 为唯一 context/result 定义。** 删除 `base.py` 中语义重复的 `ToolExecutionContext`/`ToolResult`；`ToolRegistry` 只保存模型可见 projection。`ToolExecutionResult` 必须保留 `status`、`external_return_code`、`backend_attempted`、`evidence_refs` 和 typed error，verifier 不能把没有成功 receipt 的执行改成成功。

- [x] **Step 2: 把 `domain/tools.py` 和 `task_state/tools.py` 的工厂改成 async executor。** 当前 `make_robot_navigate()` 必须重命名为 `make_robot_go_to()` 并同步迁移所有调用方；它返回 `RegisteredTool(definition=..., executor=async def execute(context, arguments) -> ToolExecutionResult)`，模型别名只使用 `robot_go_to`，不得通过 `robot_navigate` 字符串映射。

```python
async def execute(context: ToolExecutionContext, arguments: Mapping[str, Any]) -> ToolExecutionResult:
    backend = context.backend
    receipt = await backend.go_to(str(arguments["target"]))
    return ToolExecutionResult.from_receipt(
        tool_name="robot_go_to",
        receipt=receipt,
        evidence_refs=(receipt.evidence_ref,),
    )
```

- [x] **Step 3: 将 ALFWorld tool 限定为 schema 到 typed request 的映射。** `make_alfworld_robot_go_to()` 和 `make_alfworld_robot_manipulate()` 不再做 grounding/动作分支；它们调用 `AlfworldHarness.execute()` 并透传 Harness 的 `AlfworldExecutionFeedback`、外部返回码和证据引用。

- [x] **Step 4: 从 `profiles.py` 删除 `_adapted_tool()`、`adapt_legacy_tool_spec()`、同步 `_executor` 检测和 `build_universal_tool_registry()`。** profile 直接合并 `RegisteredTool`，碰到 duplicate model alias 只接受显式 `_INTENTIONAL_COLLISIONS`，其余 fail closed。

- [x] **Step 5: 删除旧协议文件和调用方。** Delete `src/homemaster/tools/spec.py`、`results.py`、`legacy_adapter.py`；删除 `adapters.py:from_tool_spec()`；删除旧 `robot_navigate`、`robot_find_object` fixture、trace 和 permission 配置。不要新增 alias 或 fallback。

### 2.2 协议审计和外部工具黑盒

- [x] **Step 1: 更新测试并增加接口审计。** `tests/homemaster/v35/test_tool_interface_audit.py` 遍历所有 `RegisteredTool`，检查 definition/executor/verifier 类型、公开方法集合和 async callable；对每个实现调用一次 canonical executor，检查结果中的 `external_return_code` 与 backend receipt 一致。

- [x] **Step 2: 运行静态删除门。**

```bash
! rg -n 'ToolSpec|ToolResult|legacy_adapter|from_tool_spec|robot_navigate|robot_find_object|build_universal_tool_registry|_executor' \
  src tests scripts --glob '!**/__pycache__/**' --glob '!**/vendor/**'
```

Expected: 退出码 0；文档若需提及迁移历史，只能位于 V3.5 计划/变更记录，不能出现在运行时源码、配置或测试 fixture。

- [x] **Step 3: 进行真实 Home backend 黑盒。** 用 `tests/fixtures/home_tasks/fetch_cup_retry/world.json` 创建实际 Home backend，经过 `ToolExecutor` 调用 `robot_go_to`/`robot_manipulate`，独立读取 world state 的物体位置/持有状态；逐调用断言返回码、状态变化、trace evidence 三者对应。仅 verifier 通过而 world 未变更必须 FAIL。

**Phase 2 gate**

```bash
uv run pytest -q \
  tests/homemaster/tools \
  tests/homemaster/test_tool_registry.py \
  tests/homemaster/test_domain_home_tools.py \
  tests/homemaster/test_task_state_tools.py \
  tests/homemaster/v35/test_tool_interface_audit.py
uv run ruff check src/homemaster/tools src/homemaster/domain/tools.py src/homemaster/task_state/tools.py
```

必须同时满足：所有指定文件确实 collected；所有 registry entry 走 canonical async seam；旧符号 grep 为零；Home world 的外部状态逐调用改变；失败调用保留原始错误和 `outcome_unknown`/certainty，不被 verifier 覆盖。

---

## 3. 收敛 ALFWorld Oracle Harness

### 3.1 先锁定 THOR-only 类型和接口

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/types.py`
- Create: `src/homemaster/alfworld/__init__.py`
- Create: `src/homemaster/alfworld/harness.py`
- Create: `src/homemaster/alfworld/backend.py`
- Create: `src/homemaster/alfworld/lifecycle.py`
- Create: `src/homemaster/alfworld/scene.py`
- Create: `src/homemaster/alfworld/actions.py`
- Create: `src/homemaster/alfworld/outcomes.py`
- Create: `src/homemaster/alfworld/recording.py`
- Test: `tests/homemaster/alfworld/test_harness_contract.py`, `test_scene_grounding.py`, `test_actions.py`, `test_lifecycle.py`, `test_outcomes.py`

- [x] **Step 1: 将 `EnvType` 收窄为 `Literal["AlfredThorEnv"]`，删除 `AlfredTWEnv`、`textworld` backend kind、`debug_admissible_commands`、`require_v18_reset` 和运行时双路径开关。** 配置解析在非法值时 fail closed；不能将旧值静默映射到 THOR。

- [x] **Step 2: 在 `backend.py` 定义最小外部接口。** 只含 reset/set_task/observe/action/close、raw event 和外部返回码；不含 prompt、Provider、评分或 memory。

```python
class ThorBackend(Protocol):
    def reset(self, trial: TrialSelection) -> BackendReceipt: ...
    def observe(self) -> ThorObservation: ...
    def act(self, action: ThorAction) -> BackendReceipt: ...
    def close(self) -> CleanupReceipt: ...
```

- [x] **Step 3: 将现有逻辑按责任迁移而不是机械复制。** `lifecycle.py` 吸收 `reset_transaction.py`/`pose_snapshot.py` 的 reset-time identity、controlled-time scan、immutable pose snapshot、goal advance、quarantine；`scene.py` 吸收 `execution.py` 的 `SceneObjectRef`、`SceneObjectIndex`、grounding/visibility/inventory；`actions.py` 吸收 `OracleNavigationExecutor`、`OracleManipulationExecutor`、`OracleActionGateway`；`outcomes.py` 统一 `AlfworldExecutionFeedback`、classification、terminal owner；`recording.py` 吸收 tracing/trajectory writer，但不能参与动作成败判定。

- [x] **Step 4: 在 `harness.py` 暴露唯一深接口。** `reset()`、`execute(AlfworldActionRequest)`、`advance_goal()`、`close()`；`execute()` 固定顺序为 `ground -> immutable pose lookup -> precondition -> one gateway action -> return-code check -> raw-state verification -> feedback`。

- [x] **Step 5: 完成包边界迁移。** 将 `src/homemaster/benchmarking/alfworld/types.py` 的 canonical contracts 移到 `src/homemaster/alfworld/types.py`；将 `grounding.py`、`object_view.py`、`pose_snapshot.py`、`reset_transaction.py`、`tracing.py`、`traj_index.py`、`trajectory_memory.py`、`prompt.py`、`taskset_loader.py`、`trial_selection.py`、`permission_adapter.py` 分别迁入 Harness 或 `src/homemaster/alfworld/benchmark/`。`benchmarking/alfworld_reference.md` 和 `alfworld_tasksets.yaml` 迁入 `src/homemaster/alfworld/` 的 package data/config 位置。所有 import 迁移后，`src/homemaster/benchmarking/alfworld/` 整个旧 package 删除，不留下第二套类型或工具入口。

### 3.2 删除 legacy 和重写 benchmark tools

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/tools.py`（迁移内容至 `src/homemaster/alfworld/benchmark/tools.py`）
- Modify: `src/homemaster/benchmarking/alfworld/runner.py`（迁移内容至 `src/homemaster/alfworld/benchmark/runner.py`）
- Create: `src/homemaster/alfworld/benchmark/runner.py`
- Create: `src/homemaster/alfworld/benchmark/episode.py`
- Create: `src/homemaster/alfworld/benchmark/taskset.py`
- Modify: `src/homemaster/benchmarking/alfworld/gateway.py`, `permission_adapter.py`（迁移后从旧 package 删除）
- Delete after import migration: `src/homemaster/benchmarking/alfworld/env_adapter.py`, `execution.py`, `translator.py`（仅删除其中的 legacy/双环境实现；仍有 Oracle 代码先迁移）
- Delete: `src/homemaster/adapters/alfworld_entry.py`
- Test: 将 `tests/homemaster/benchmarking/test_alfworld_*.py` 中的 Oracle 用例迁移到 `tests/homemaster/alfworld/`，新增 `tests/homemaster/alfworld/benchmark/test_episode_lifecycle.py`、`test_taskset_lifecycle.py`；删除只覆盖旧 package/legacy 的测试

- [x] **Step 1: 让 episode/taskset 共享 lifecycle。** `episode.py` 管理一 scene/一个 goal/一个 session；`taskset.py` 只传入连续 goal 参数并调用同一 lifecycle，不复制 Provider loop、SessionManager、ToolExecutor 或 recording 逻辑。

- [x] **Step 2: 删除 runner 中的 composition。** runner 只做 trial selection、创建 Harness、构造 prompt/RunRequest、调用已组装的 `ApplicationRuntime.run()`、接收 typed outcome、写 summary/score/artifact；禁止直接构造 `LLMClient`、`ToolRegistry`、`AgentRuntime`。

- [x] **Step 3: 更新工具名称和 prompt。** 只暴露 `robot_go_to`、`robot_manipulate`、`robot_verify`；prompt 公开达到目标所需动作语义，但不泄漏 exact objectId、pose、hidden containment 或专家轨迹。`robot_verify` 的成功只认 Harness/环境 terminal owner 的权威状态。

- [x] **Step 4: 删除明确旧路径。** 删除 `_execute_thor_manipulation()`、`_legacy_execution_feedback()`、`virtual_navigate()`、admissible command 搜索、运行时候选 pose 枚举、`ManipulationExecutor`/`LegacyManipulationExecutor`/`ManipulationRouter` 和所有 TextWorld translator 分支。删除相应旧 fixture/test，而不是加 skip 或 alias。

### 3.3 Harness 阶段验证

- [x] **Step 1: 先跑纯 Harness 对抗测试。** 使用 fake Thor backend 注入：重复同名物体、过期 snapshot、不可见对象、动作返回码失败、返回码成功但 raw state 未变化、close 失败。每种情况分别断言 classification、`backend_attempted`、终态证据和不会继续下一动作。

- [x] **Step 2: 真实 THOR 单 trial 黑盒。** 使用仓库已有 `tests/fixtures/alfworld/gateway_smoke_trials.json` 的每个 trial entry，逐个启动 worker/Harness，执行 reset、一个导航、一个可验证 manipulation、close。每个 entry 单独写 JSON：`trial_id`、`request_id`、`external_return_code`、动作前后 raw event 摘要、目标 objectId、pose/visibility/terminal proof、worker pid、stderr path、close result。证据：`evidence/phase-5/gateway-smoke-live/`。

- [x] **Step 3: 真实失败黑盒。** 对一个已知不可见/不存在目标和一个强制 backend failure 分别执行；要求返回非成功码或 typed failure，真实世界不得出现错误动作，worker 最终仍可 close；若 timeout 后无法证明未变更，必须返回 `outcome_unknown` 且禁止自动重试。

**Phase 3 gate**

```bash
uv run pytest -q \
  tests/homemaster/alfworld \
  tests/homemaster/benchmarking/test_alfworld_grounding.py \
  tests/homemaster/benchmarking/test_alfworld_navigation.py \
  tests/homemaster/benchmarking/test_alfworld_outcome.py \
  tests/homemaster/benchmarking/test_alfworld_permissions.py
uv run python scripts/guard_no_legacy_terms.py
uv run pytest -q -m live_alfworld tests/homemaster/test_alfworld_live_smoke.py tests/homemaster/gateway/test_alfworld_http_live.py
```

Live 命令只有在每个 trial 的外部返回码、raw event/terminal 状态、close 状态和进程清理都通过时才 PASS；任何聚合分数为 1 但某个 trial 缺证据都判 FAIL。测试文件必须逐个出现在 collected list 中。

---

## 4. 两环境和 NDJSON worker

### 4.1 固化协议和 worker source tree

**Files:**
- Create: `protocols/alfworld-v1.schema.json`
- Create: `src/homemaster/alfworld/worker_protocol.py`
- Create: `src/homemaster/alfworld/worker_client.py`
- Create: `workers/alfworld_worker/__init__.py`
- Create: `workers/alfworld_worker/protocol.py`
- Create: `workers/alfworld_worker/main.py`
- Create: `workers/alfworld_worker/thor_backend.py`
- Create: `config/alfworld/requirements.lock`
- Create: `scripts/setup-alfworld.sh`
- Modify: `pyproject.toml`, `uv.lock`, `.gitignore`
- Delete after migration: `src/homemaster/benchmarking/alfworld/http_client.py`, `http_worker.py`
- Test: `tests/homemaster/alfworld/test_worker_protocol.py`, `test_worker_isolation.py`, `tests/homemaster/gateway/test_alfworld_worker_live.py`

- [x] **Step 1: 把协议写成版本化 schema。** 每行请求严格为：

```json
{"protocol":"homemaster-alfworld-v1","request_id":"uuid","operation":"reset|set_task|observe|act|close","payload":{}}
```

响应必须包含 `protocol`、同值 `request_id`、`status`、`external_return_code`、`backend_attempted` 和 typed `result`；ready 响应增加 `worker_version`、`python_executable`、`alfworld_origin`、`ai2thor_version` 和 capability。未知 operation、重复 request id 与超长行 fail closed。

- [x] **Step 2: 主环境实现 `AlfworldWorkerClient`。** 用 `subprocess.Popen` 的 stdin/stdout/stderr 管道启动 worker；启动时清空继承的 `PYTHONPATH`，只传显式 worker source、`.runtime/alfworld` binding、frame/artifact root；每个 worker 只串行一个 episode。client 固化 `worker_id`、`run_id`、request id 和 artifact root，重试不重算。

- [x] **Step 3: worker 只使用独立 source tree。** `workers/alfworld_worker/main.py` 只能 import 标准库、ALFWorld、AI2-THOR、Torch/OpenCV/Pillow/NumPy；禁止 `homemaster`、`mindmemos`、主环境 site-packages 或主 benchmark package。所有日志用 `print(..., file=sys.stderr)`；stdout 每行只能是 schema 合法 JSON。

- [x] **Step 4: 大对象走 artifact reference。** frame/raw event 写双方可见 repo-local artifact path；响应只回 `path`、`size`、`sha256`、`mime_type`。client 读回后重新计算 SHA-256、大小和 PNG 可解码性；hash 不符或路径越界立即失败，不把图片 bytes 塞进 IPC body。

- [x] **Step 5: close 拥有完整生命周期。** client 先发 `close`，核对 `external_return_code` 和 cleanup receipt，再 wait；超时按 SIGTERM/SIGKILL 清理 worker 及 descendants，逐 PID 检查不存在。worker stderr 在 close 后完整读完，含 traceback、ALFWorld/Unity 错误或迟到异常则整次 run FAIL。

### 4.2 安装、绑定和启动路径

- [x] **Step 1: 修正根项目依赖。** 将 `third_party/MindMemOS/src/mindmemos` 作为通用环境的本地 workspace dependency 写入根 `pyproject.toml`/`uv.lock`，删除依赖源码路径的 `PYTHONPATH` 方案；修正 package discovery，确保 benchmark/Harness 源码被打包但 worker 不随主包导入。

- [x] **Step 2: 重写 `scripts/setup.sh`。** 只创建/同步 `.runtime/venv`，安装 HomeMaster、MindMemOS 和 provider/tools/skills/mcp；不创建 `.venv` 再软链，不接受 ALFWorld Python 参数。执行后必须 `import homemaster; import mindmemos`。hkust4 使用 uv 0.11.19 完成 setup，doctor `16/16 PASS`，证据：`evidence/phase-5/setup-gates-20260926.json`。

- [x] **Step 3: 实现 `scripts/setup-alfworld.sh --root <path>`。** 首次命令接受服务器绝对路径，按 `config/alfworld/requirements.lock` 创建 `.runtime/alfworld-venv`，校验 `alfworld`、`ai2thor==2.1.0`、Torch、NumPy、OpenCV、Pillow，校验 `configs/base_config.yaml` 和 `data/json_2.1.1`，把解析后的 binding 写入 `.runtime/`。hkust4 setup probe PASS，证据：`evidence/phase-5/setup-gates-20260926.json`。

- [x] **Step 4: 重写 `scripts/homemaster`。** 所有 CLI（包括 benchmark）使用 `.runtime/venv/bin/python`；benchmark 由主环境启动 `AlfworldWorkerClient`，不切换到 `.runtime/alfworld-venv`，不注入通用 site-packages/MindMemOS 源码，不 import `homemaster` 到 worker。

- [x] **Step 5: 更新 doctor。** `src/homemaster/cli/doctor.py` 增加四组独立检查：通用 import；MindMemOS 实际读写；ALFWorld worker import/asset binding；隔离 IPC reset/act/close。任何组失败都返回非零，不因关闭 memory 或只通过 FIFO ready 而报告 ready。

**Phase 4 protocol and environment gate**

```bash
uv run pytest -q \
  tests/homemaster/alfworld/test_worker_protocol.py \
  tests/homemaster/alfworld/test_worker_isolation.py \
  tests/homemaster/test_memory_runtime_setup.py \
  tests/homemaster/test_cli_doctor.py
uv run python -c 'import homemaster, mindmemos; print("general-imports-ok")'
.runtime/alfworld-venv/bin/python -c 'import alfworld, ai2thor; print(ai2thor.__version__)'
.runtime/alfworld-venv/bin/python -c 'import importlib.util; assert importlib.util.find_spec("homemaster") is None; assert importlib.util.find_spec("mindmemos") is None; print("worker-isolation-ok")'
scripts/homemaster doctor --json
```

Expected：通用与 worker 的 import 证据分别来自各自 interpreter；worker 隔离命令明确证明两个主环境包不可导入；doctor 的每一项为 PASS。然后用真实 worker 做一轮 `reset -> act -> close`，逐项核对 request id、返回码、raw state 读回和进程/子进程清理。单独的 Python import 成功、ready pipe 或 client 日志不算通过。

---

## 5. Runtime、Gateway 和 benchmark 端到端收口

### 5.1 入口 adapter 与生命周期

**Files:**
- Modify: `src/homemaster/gateway/alfworld.py`, `src/homemaster/gateway/runtime.py`
- Modify: `src/homemaster/benchmarking/alfworld/benchmark/*.py`
- Modify: `src/homemaster/cli/benchmark_alfworld.py`, `src/homemaster/cli/app.py`
- Modify: `src/homemaster/browser/application.py`, `src/homemaster/web/serve.py`
- Delete: `src/homemaster/adapters/alfworld_entry.py`
- Test: `tests/homemaster/gateway/test_alfworld_worker_live.py`, `tests/homemaster/integration/test_entry_parity.py`, `tests/homemaster/benchmarking/test_alfworld_runner.py`

- [x] **Step 1: Gateway 使用 `AlfworldWorkerClient`。** `AlfworldGatewayApplication` 只固定 worker/session ownership、RunRequest dependencies、terminal owner；不要重新实现环境动作或 HTTP health/token。

- [x] **Step 2: benchmark 显式拥有 async lifecycle。** `benchmark/runner.py` 创建 composition bundle，episode/taskset 只创建/关闭 Harness 和 session；`ApplicationRuntime.run()` 是唯一 Agent loop。同步 CLI 入口若需要，只负责 `asyncio.run()`，名字中明确包含 event-loop owner，不做隐式 composition。

- [x] **Step 3: 修正 shutdown/取消。** Runtime 的 absolute deadline 覆盖 worker cancel/join、bus drain、channel stop 和 resource close；`SIGINT` 注入正在执行的 worker action 和 close 边界，确认主进程、worker、Unity/Xvfb 全部退出。主动取消不能伪装成故障，晚到外部结果必须 reconciliation 后再决定 terminal。

- [x] **Step 4: 保持 Browser/Web/Feishu 行为并做入口 parity。** 用同一 composition factory 创建三个 profile；分别验证普通 reply/final 不重复、事件 generation 不串线、外部 artifact/attachment 仍按原 ACL/tenant 规则投影。

### 5.2 逐实例 live acceptance

- [x] **Step 1: 单 episode。** 固定 manifest entry 已生成 `evidence/phase-5/<trial_id>/episode.json`；证据包含 reset identity、scene snapshot、每个 tool call 的 request/response pair、external return code、动作前后 raw state、最终 `won`/terminal owner、退出码、持久化 stderr 和 cleanup。

- [x] **Step 2: taskset 连续目标。** `easy_living_room_219` 的两个 subtask 已在同一 THOR scene 上运行；分别保存 scene/goal generation、goal advance 前后 digest、动作前后外部状态和 cleanup。subtask 2 的真实持有状态导致 `take CellPhone` 失败，已逐实例分类为 `agent_model_failure`，没有被汇总成 Harness 成功。

- [x] **Step 3: 主 CLI 黑盒。**

```bash
set -o pipefail
scripts/homemaster benchmark-alfworld \
  --split valid_seen --episodes 1 --run-id v35-live-episode \
  2>plan/V3.5/evidence/phase-5/episode.stderr \
  | tee plan/V3.5/evidence/phase-5/episode.stdout
episode_rc=${PIPESTATUS[0]}
test "$episode_rc" -eq 0
```

随后解析正式 summary/artifact，而不是 stdout 日志：每个 trial 必须有 terminal result、`external_return_code == 0`、真实 THOR state 满足目标、worker close succeeded；stderr 不能含 traceback、外部 DB error、Unity late error。`run-id` 已存在、trace root 冲突或 resource 残留时必须 fail closed。

- [x] **Step 4: shutdown black box。** 在耗时 action 和 close 各注入一次真实 `SIGINT`；分别断言退出码、terminal 状态、worker/descendants 消失、外部连接关闭次数和无迟到 stdout。只 mock signal handler 或只看到“开始清理”日志不算通过。

**Phase 5 gate**

```bash
uv run pytest -q \
  tests/homemaster/application \
  tests/homemaster/gateway/test_alfworld_worker_live.py \
  tests/homemaster/integration/test_entry_parity.py \
  tests/homemaster/benchmarking/test_alfworld_runner.py
scripts/homemaster benchmark-alfworld-taskset --run-id v35-live-taskset
```

所有 trial/subtask 逐项通过才算 PASS；不能用全局 score、任一 trial 成功或 summary JSON 的 `status=success` 掩盖某个实例的缺失终态证据。

---

## 6. 删除、审计、文档和发布验收

### 6.1 最终删除和静态审计

**Files:**
- Delete: 所有已迁移的 `benchmarking/alfworld/http_*.py`、`adapters/alfworld_entry.py`、旧 Tool 协议文件和 legacy 执行模块
- Modify: `scripts/guard_no_legacy_terms.py`
- Create: `scripts/verify_v35_architecture.py`
- Create: `tests/homemaster/v35/test_final_architecture_audit.py`

- [x] **Step 1: 运行并修正依赖方向审计。** `verify_v35_architecture.py` 用 AST 检查：composition 是唯一公共创建入口；ApplicationRuntime/AgentRuntime 没有 ALFWorld import；Harness 不构造 Provider/Memory/Agent；benchmark 不构造 LLM/ToolRegistry；worker source 无主包 import；旧符号、旧路径、TextWorld 配置均不存在。

- [x] **Step 2: 运行 package-data 和 clean checkout 审计。** 在临时 clean checkout 安装通用包和 worker 包，确认 `homemaster.alfworld` 可被通用 runtime 使用，而 worker 仍不能 import `homemaster`；确认 `uv.lock`、`config/alfworld/requirements.lock` 和 protocol schema 都在版本控制中；确认 `.runtime/`、私有 config、真实 token/路径被 `.gitignore` 且只提交 `.example`。`scripts/verify_v35_release.py --json` PASS。

- [x] **Step 3: 逐文件清理旧测试/fixture。** 删除只测试 TextWorld、`AlfworldApplicationEntry`、`robot_navigate`、HTTP token/port/health 的测试；保留并迁移 V1.8 Oracle grounding/pose/feedback/permission 测试，使其命中新的 Harness 文件。NDJSON live 测试已重命名为 `test_alfworld_gateway_live.py`，删除门 PASS。

### 6.2 文档同源更新

- [x] **Step 1: 更新 `architecture/home-master-layer-map.md`。** 把代码路径、worker package、NDJSON schema、`.runtime/venv`/`.runtime/alfworld-venv` 和实际命令更新为已交付实现；删除“当前仍使用 HTTP”陈述。

- [x] **Step 2: 更新 `plan/V3.5/architecture-problems-and-target-design.md`。** 状态已更新为 2026-09-26 已实现，并在 Application、入口适配器、Harness、Tool 协议章节加入实际实现路径和 evidence 交叉引用。

- [x] **Step 3: 更新 `README.md`、`plan/README.md`、`CHANGELOG.md`。** README 给出可执行路径：

```bash
cp config/homemaster.example.yaml config/homemaster.yaml
./scripts/setup.sh
./scripts/setup-alfworld.sh --root /path/to/alfworld
scripts/homemaster doctor --json
scripts/homemaster benchmark-alfworld --split valid_seen --episodes 1
```

同时说明 MindMemOS 属于通用环境、worker 隔离、协议版本和 live acceptance 的事实；CHANGELOG 记录删除旧 Tool/HTTP/TextWorld 路径及其原因，不写“兼容保留”。

- [x] **Step 4: 按 commit 更新 CHANGELOG。** 本次提交前已写入同源 CHANGELOG 条目；commit message 包含改动、原因、影响和验证命令。

### 6.3 最终验收命令

```bash
git diff --check
uv run ruff check src tests scripts
uv run pytest -q tests/homemaster/v35
uv run pytest -q \
  tests/homemaster/application \
  tests/homemaster/tools \
  tests/homemaster/alfworld \
  tests/homemaster/benchmarking \
  tests/homemaster/gateway \
  tests/homemaster/integration
uv run python scripts/verify_v35_architecture.py
scripts/homemaster doctor --json
```

最终 live 命令必须重新执行 Phase 3/5 的所有 trial/taskset，而不是复用旧 summary；逐 instance evidence 目录、返回码、外部终态、stderr 和进程清理都存在，才可把计划标记完成。

**Final DoD**

- composition/Runtime/Agent/Harness/worker 的依赖方向与两份架构文档逐项一致。
- 源码中无 `AlfredTWEnv`、legacy navigation/manipulation/feedback、`require_v18_reset`、HTTP worker、旧 Tool protocol 或兼容 alias。
- 通用环境能真实 import/读写 MindMemOS；worker 能真实 import ALFWorld/AI2-THOR 且不能 import HomeMaster/MindMemOS。
- 每个 live episode/taskset 实例都有外部返回码、真实终态、close、stderr 和 process cleanup 证据；没有聚合判据掩盖失败。
- 文档、README、CHANGELOG、锁文件和 `.example` 配置与代码同源；`git diff --check`、ruff、指定测试集合和架构审计全部返回 0。
