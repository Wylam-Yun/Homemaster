# ApplicationRuntime → SessionOrchestrator 瘦身设计

来源：implementation-plan Phase-2 line 58（用户裁决"现在做"）。
原文："只留 generation fencing/cancel/turn 生命周期；dedup/registry/terminal/
取消/清理逐项指派给保留 Web 层对象（运行所有权表）；compose_application 拆
per-domain composer + 资源图（谁拥有什么、关闭顺序）。"

## 现状职责盘点（runtime.py 1212 行）

`ApplicationRuntime` 目前一把抓七类职责：

| # | 职责 | 代码面 | 行数 |
|---|------|--------|------|
| 1 | 应用生命周期 | `start()`（starter 回调 + APPLICATION_START hook + permission_store.recover）、`aclose()`（extensions→browser scopes→resource_scope→permission store→event_bus 有序关闭） | ~110 |
| 2 | Extension hook 框架 | `_extension_turn`（RUN_START/RUN_END）、`_execute_extension_hooks`（执行 + `extension.hook_completed` 事件 + quiesce/aclose/失败拒绝） | ~150 |
| 3 | Session/turn 生命周期 | `session()`、`run()` 的 open_or_resume→turn→generation→sink 段、`compact()`、`cancel()`、`status()` | ~200 |
| 4 | Run 执行编排 | `_execute_run`——tool view→provider→assembler→`ApplicationToolExecutor`→RunScope/adapters→`AsAgentRuntime`→recall→commit→save | ~250 |
| 5 | 记忆 recall 管线 | `_automatic_recall`、`_register_user_memory_evidence`、`rearm_recall_after_compaction` 闭包 | ~100 |
| 6 | Tool view 作用域 | `_run_tool_view`（browser_session_factory 时的 per-run registry/executor + scope 跟踪） | ~40 |
| 7 | 结果提交/持久化 | `_commit_result`（generation-fenced apply）、`_save_if_configured` | ~40 |

已有前提：`composition/` 已是 per-domain composer（base/memory/
observability/profiles/providers/skills/tools）——计划里"拆
composer"大头已完成；`SessionManager` 已独立承载 session 持久化/
turn/generation/cancel/apply/save。

## 资源所有权表（当前隐式 → 目标显式）

aclose 顺序即所有权链（不可颠倒——extensions 先关防 callback 晚到，
event_bus 最后关才能接住 cleanup 事件）：

| 资源 | 创建点 | 关闭点（顺序） | 归属建议 |
|------|--------|----------------|----------|
| extension_runner | composition | 1. quiesce→APPLICATION_STOP→aclose→`extension.cleanup_completed` | **ExtensionLifecycle**（新） |
| browser run scopes | `_run_tool_view` per-run | 2. 逐个 aclose，错误聚合 ResourceCleanupError | ApplicationRuntime（薄） |
| resource_scope | composition | 3. aclose | ApplicationRuntime |
| permission_store | tool_executor | 4. close（幂等一次） | ApplicationRuntime |
| event_bus | composition | 5. aclose（始终执行） | ApplicationRuntime |
| session/control | SessionManager | 独立生命周期 | SessionManager（不动） |

## 候选方案

### 候选 A — 只抽协作者（保守拆分）

`ApplicationRuntime` 保持公开类名与全部入口签名（`run`/`session`/
`cancel`/`status`/`compact`/`start`/`aclose`），内部委托：

```
application/
  runtime.py        → ApplicationRuntime：编排核心（~800 行）
  extensions.py     → ExtensionLifecycle：quiesce/stop/hooks/close + _extension_turn
  memory_recall.py  → AutomaticRecallService：recall + evidence + rearm 帮助
  tool_view.py      → RunToolView：browser-session per-run registry/executor
```

- 代价：机械拆文件，委托薄；全部测试零改动预期。
- 收益：七类职责显性化，runtime.py 缩 ~35%。
- 问题：`_execute_run`（250 行）仍长在本体上——"编排器只剩生命周期"
  的字面目标没达成；`_execute_run` 与 self 的 12+ 个字段耦合，拆出去
  才是真瘦身。

### 候选 B — 编排器 + RunDriver 两级（计划字面目标）

```
application/
  runtime.py        → ApplicationRuntime：session/turn/fence/control-plane
                      门面 + 资源所有权（~500 行）
  run_driver.py     → RunDriver：_execute_run + _run_tool_view + recall 串联
                      （provider/assembler/executor/agent 全装配）
  extensions.py     → ExtensionLifecycle（同 A）
  memory_recall.py  → AutomaticRecallService（同 A）
```

`RunDriver` 持有 provider_factory、context_assembler_factory、settings、
artifact_publisher、working_directory、memory service；`run()` 拿到
turn+generation+sink 后调 `driver.execute(...)`，driver 返回结果交给
orchestrator commit。

- 代价：`_execute_run` 12+ 处 `self.*` 全参数化；rearm 闭包依赖
  assembler/run_context 局部变量，跨对象传递要小心闭包捕获；
  commit/save 回指 orchestrator 的接口要定清（回调 vs 返回值交给
  orchestrator 提交——建议后者：driver 纯执行，commit 留在
  orchestrator，generation fence 不越界）。
- 收益：orchestrator 真正只剩 session/turn/fence/control-plane +
  资源关闭；driver 独占"一次 run 的装配与执行"；per-run 资源
  （provider_scope/browser scope）的 owner 就是 driver，所有权表
  与代码结构一一对应。
- 风险：最大的一处是把 `rearm_recall_after_compaction` 这类同时摸
  runtime(generation)/assembler/run_context 的闭包拆清——设计上
  归 driver（它持有 assembler 引用和 memory service），orchestrator
  只暴露 `require_recall_after_compaction`/`save` 回调。

### 候选 C — 只改类名/文档（不做）

`ApplicationRuntime` 改名 `SessionOrchestrator` + 别名兼容。
纯改名无瘦身，排除；类名保持 `ApplicationRuntime` 不动（公开 API，
shell/web/gateway/tests 全线引用，改名 churn 无收益）。

## 推荐

**候选 B**。理由：候选 A 只解决"文件长"没解决"责任混"——
`_execute_run` 才是 god-object 的主体；B 让"turn 生命周期"
（orchestrator）与"run 装配执行"（driver）成为显式两层，per-run
资源 owner 与所有权表对齐。commit/save 必须留在 orchestrator 一侧：
generation fence 是 session 所有权语义，driver 不该直接写
session_manager。

## 独立评审结论（已执行，采纳 B + 8 处修正）

评审确认 B 可行且是唯一真正消除 god-object 的方案，但发现文档未覆盖的
耦合点；以下修正并入设计：

1. **Driver 必须 per-run 构造，不得 `__init__` 快照依赖**。`provider_factory`/
   `context_assembler_factory`/`settings`/`artifact_publisher`/`session_manager`
   是公开可变属性，生产与测试在运行后重绑（`episode.py:60`、
   `test_entry_parity.py:123/150/160/211/230`——其中 compact() 在重绑后调用，
   必须看到新 factory；`test_application_runtime.py:2547/2558/1895-1896`、
   `test_app.py:405`）。`run()` 内每次构造 `RunDriver(...)` 传当前 `self.*`
   值，零成本保住重绑契约。
2. **`_browser_run_scopes` 集合留在 ApplicationRuntime**。`aclose` 必须在
   run 进行中关闭 scope（`test_browser_run_scope.py:414-435` 锁定：mid-run
   aclose → run CANCELLED；`RunResourceScope.aclose` 幂等），per-run driver
   够不到。driver 收同一 set 引用做 add/discard，语义逐字节不变。
3. **Commit 边界（实现后修正：envelope → `commit_fn` 回调）**。
   初版按评审建议用 `RunOutcome` envelope 把 commit 挪到 `run()`——
   二轮 diff review 抓到不变量漂移：原代码 commit+save 在
   `provider_scope`/`_run_tool_view` **内部**执行，挪出后 provider/
   browser 清理失败会先于 commit 抛出（`test_save_error_remains_primary_
   when_provider_cleanup_fails` 语义；resumed run 下 mid-run save 不再
   掩盖）。**定稿**：orchestrator 注入 `commit_fn=self._commit_and_save`
   回调，driver 在 scope 内原位置调用，commit 策略仍归 orchestrator、
   fence 不越界，`RunOutcome` envelope 随之删除——`execute()` 直接返回
   `RunResult`。
4. **修正 fence 表述**。fenced 中段写经 `_FencedAgentSession →
   session_manager.apply(generation)` 贯穿 run 全程——driver 必须持
   `session_manager`（或 fenced-session factory）。orchestrator 只保留
   *最终* commit+save 策略；"driver 不写 session_manager"不成立。
5. **`ExtensionLifecycle` 需暴露 `closed`/`released`** 供 `start()` 回滚
   判定（原 337 行 `extension_runner is None or extension_runner.closed`；
   `test_blocked_application_start_rolls_back_extension_and_resources`
   锁定）。`event_bus` 共享不独占；`_close_extensions` 仍是 `aclose` 首句、
   try 外（fail-closed 顺序，`cleanup_completed` 事件需要 bus 未关）。
   `HookRunner.execute` 已验证：`event is not APPLICATION_STOP` 在
   `_closing` 下直接返回空结果——RUN_END 与 quiesce 并发安全，原样保留。
6. **`AutomaticRecallService` 持 `settings` 对象**（非 services 快照），
   每次调用内 lazy 读 `application_services`（原 676/744 行模式），
   与可变 settings 契约一致。
7. **再导出**：`_FencedAgentSession`（test_model_observation_resume.py:8）、
   `AutomaticRecallRunDeadlineExceeded`（test_application_runtime.py:29）
   从 runtime.py 保持可 import；`_GenerationFencedEventSink` 原地不动
   （test_runtime_stress.py:15）。
8. **死代码删除**：`_completion_requires_external_owner`/
   `_verification_required_tool_names`（272-277 行从未被读，
   _execute_run 在 463-468 行按 run_tools 重算）、`Deadline`（__all__ 中
   但零消费者；tests 里的同名类是测试本地定义）、runtime 版
   `_backend_generation`（无调用点——lease_manager 用的是它自己的
   同名函数）。

### 定稿文件布局

```
application/
  runtime.py               → ApplicationRuntime：session()/run() turn+generation+
                             sink 段、compact/cancel/status、aclose 所有权关闭序、
                             _commit_result/_save_if_configured（fence 终点）
  run_driver.py            → RunDriver（per-run 构造）+ RunOutcome +
                             _FencedAgentSession + _run_tool_view +
                             _maybe_await/_provider_binding/_stop_condition/
                             _is_agentscope_provider helpers
  extension_lifecycle.py   → ExtensionLifecycle（execute/turn/close + released）
  memory_recall.py         → AutomaticRecallService +
                             AutomaticRecallRunDeadlineExceeded +
                             _emit_automatic_recall_event/_await_with_remaining_deadline
```

命名说明：不用 `extensions.py`，避免与顶层 `homemaster/extensions/`
包混淆；`extension_lifecycle.py` 名实相符。

`ProviderFactory`/`ContextAssemblerFactory` 定义移入 run_driver.py
（driver 是主消费方），runtime.py re-import 保持
`from homemaster.application.runtime import ProviderFactory` 兼容
（factory.py 依赖）。`ApplicationStarter`/`SessionEndHandler` 留 runtime.py。

`run()` 新骨架：

```python
async with self._extensions.turn(
    self.session_manager.turn(session_id, environment_ref=environment_ref),
    request=request, run_id=run_id,
) as (runtime, generation, _, hook_blocked_reason):
    if hook_blocked_reason: ...            # 原样
    run_event_sink = _GenerationFencedEventSink(...)   # 原样
    ...strip/bind/application_control 原样...
    driver = RunDriver(provider_factory=self.provider_factory, ...,
                       save_fn=self._save_if_configured,
                       browser_run_scopes=self._browser_run_scopes)
    outcome = await driver.execute(request=request, runtime=runtime, ...)
    if outcome.result is not None:
        return outcome.result
    try:
        result = self._commit_result(runtime, generation, outcome.agent_state,
                                     outcome.task_state_store, outcome.evidence_refs,
                                     outcome.generic)
        await self._save_if_configured(session_id, generation)
    except SessionGenerationError:
        return RunResult(..., CANCELLED, "stale_generation")
    return result
```

`runtime.extension_runner` 改为只读 property 转发 `self._extensions.runner`
（零外部 rebind 已核实），构造签名不变。

## 行为不变量（实现必须守住）

- `run()` 的 turn/generation/事件 sink/extension_turn 顺序逐字节不变。
- `_execute_run` 里 recall→bind→deps→scope→agent→commit 的调用序不变；
  `rearm_recall_after_compaction` 语义（generation fence + 内联 rebind +
  save-if-attempted）逐语义不变。
- `aclose` 五步关闭顺序不变；browser-scope cleanup_error 聚合语义不变。
- `compact()`/`cancel()`/`status()` 签名与返回值不变。
- `ApplicationRuntime` 类名与构造签名不变（factory.py、composition、
  全部入口、测试零改动）。
- generation fence：`_commit_result`/`_save_if_configured`/session apply
  只在 orchestrator 内发生；driver 拿 `propagate_exceptions` 原样透传。

## 验收门

- `tests/homemaster/application/` 全文件零断言改动通过（行为不变证明）。
- 宽回归（application+substrate+web+cli+alfworld runner）对基线零新增。
- 远端 `.venv` 全量回归对基线零新增 + ALFWorld 单集 e2e 干净 stderr。
