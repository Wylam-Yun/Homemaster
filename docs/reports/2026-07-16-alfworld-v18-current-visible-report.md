# ALFWorld V1.8 Current-Visible Oracle 执行交付报告

报告日期：2026-07-18

文件名沿用实施计划锁定的 `2026-07-16` 交付路径；本报告记录截至 2026-07-18 的最终实施与证据状态。

## 一、结论

V1.8 产品代码已经完成 trial pinning、controlled-time reset、immutable pose snapshot、current-model-view authorization、one-pose navigation、exact manipulation、typed feedback、Provider attempt/retry、Runner/taskset lifecycle、计数和分类迁移。内部实现回归通过，真实 THOR 产品入口也已执行。

外部 Gate 没有被描述为完美通过：

- Gate A `discovery-run-015`：20 workers 中 19 PASS，补充 Slice worker 失败；
- `exact-cases-v3.json`：未生成；
- Gate B 完整 20-case matrix：不可运行；
- Gate B 单切片：真实进入 THOR，但 reset 恢复终止；
- 固定十 Episode manifest：未生成；
- 十 Episode 真实 API run：因清单和凭据均不可用而未启动。

因此本次交付是“产品实现完成，内部验证通过，外部验证带明确失败/缺失证据”，不是 V1.8 全外部行为 PASS。

## 二、实现范围

### 2.1 Trial 与运行身份

`TrialSelectionManifest` 只接受 canonical POSIX-relative trial ID、trial SHA-256、逻辑场景、goal identity/fingerprint 和 identity status。Loader 在 Adapter 构造前拒绝路径逃逸、symlink escape、未知字段、bytes drift、scene drift 和 goal drift。

普通 THOR Episode 要求 entry 数与 Episode 数完全一致。Taskset 在构造 Adapter 或 Provider 前验证整条链的所有 trial，并且 trial bytes 只能通过 `load_verified_trial_data()` 读取。

### 2.2 Controlled-Time Reset

实现的成功序列是：

```text
initial event
-> ChangeTimeScale(0.01)
-> GetReachablePositions
-> N scan Teleports
-> restore exact initial pose
-> ChangeTimeScale(1.0)
-> atomic snapshot publish
```

成功 setup 计数为 `N+4`。slow-time 请求一旦尝试，任何后续失败都会先恢复 pose、再恢复 normal time；恢复不可确认时不发布 snapshot，关闭/quarantine 环境，并在 Provider 构造前返回 typed setup terminal。

### 2.3 Snapshot、可见性与导航

reset scan 生成 scene-generation 级 frozen snapshot。每个 exact object 最多一个 direct 或 unique-parent pose；lookup 不返回候选列表。

snapshot 不授权屏外目标。导航必须先证明准确目标存在于当前模型已成功看到的 event/frame：Provider request bytes、ordered image binding、持久化 frame、decoded pixel hash、event sequence、`metadata.visible=true` 和正面积 bbox 全部一致。通过后只发送一个 snapshot pose；失败不回退到 candidate search。

### 2.4 Manipulation 与反馈

`take/open/close/put/use/slice/heat/cool/clean` 走 V1.8 exact-context/gateway/evaluator 路径。Adapter 构造唯一 `AlfworldExecutionFeedback`，Tools/Dispatcher/Runner 不从文本重新推导终态。

反馈分别携带 inventory/object/target/state-changed 值及各自 read status。任何 absent/malformed/stale/error 状态都必须成为 terminal uncertainty，不能伪装成可重试的模型错误。

### 2.5 Provider Attempt 与 Runtime Retry

`LLMClient` 每次只使用一个选定 key 和一个请求，不内部轮换、不剥离图片重试。GenericRuntime 最多重试一次，只接受三个 closed pair：transient network、rate limit、历史 `message_delta_before_message_start` stream protocol error。

每个 attempt 有独立 ID、call-scoped sink、准确 serialized request hash 和 ordered outbound image binding。完整 assistant response append 后、tool dispatch 前提交 model view；同一 response 的多个 tool call 共享同一个 view。

### 2.6 Runner、Taskset 与指标

每个普通 Episode 独立构造并关闭 Adapter。reset/goal terminal 发生在 transport/runtime construction 之前。Taskset not-run 行不拥有 classification 或 action count，root 独立拥有 setup/control/model/total ledger 和唯一 terminal record。

CLI/summary 分开报告 raw Agent 成功、Agent-on-valid、evaluation/Harness coverage、Provider/Runtime availability、cancelled 和 formal-score availability。未知 runtime termination 不默认归类为 Agent failure。

## 三、内部验证

最终远端验证为：

```text
ALFWorld/provider/runtime focused: 202 passed, 1 skipped
full repository: 394 passed, 1 skipped
Ruff check on 48 changed Python files: PASS
compileall: PASS
cleanup/interface/V1.8 guards: 11 passed
changed JSON / Gate evidence JSON parse: 2 / 14 PASS
Markdown fence / secret / placeholder / whitespace checks: PASS
Gate B helper lint and format: PASS
```

Gate B 首次运行暴露 runner 的 keyword-only 调用错配；直接审查又发现 runtime scene identity omission。两处均有定点回归，第二处修复后的 `run-003` 保留相同 reset terminal。最终全仓、静态门和独立 verifier 结果已写入同一交付 commit 的 CHANGELOG 与 live progress 记录。

`ruff format --check` 的全改动集仍报告 26 个文件；这是本次恢复时已经存在的实现格式 baseline，没有为追求全绿而批量改写无关代码。所有手工新增/修改的 Gate B 脚本均通过单独 format check。

## 四、Gate A 证据

Gate A 保持冻结。`discovery-run-015` 执行 20 workers，19 PASS；`supplemental-slice-contract` 因 slow-time 请求后的无关 Apple settling 失败。没有生成 `exact-cases-v3.json`，也没有执行完整 exact case run。

这意味着：

- 已通过 worker 的证据仍可用于约束已观察行为；
- 失败 worker 保持失败，不做 any/best 聚合；
- Slice 精确 identity/terminal behavior 保持 `UNVERIFIED`；
- 不再扩展 standalone Gate A helper 来复制产品恢复逻辑。

## 五、Gate B 证据

### 5.1 run-001

第一条产品切片在 Adapter 构造前失败：

```text
TypeError:
build_alfworld_batch_env_with_first_trial() takes 1 positional argument
but 2 were given
```

根因是 `_build_pinned_adapter()` 把 keyword-only `first_trial_path` 当成位置参数。已改为显式关键字调用，并增加严格签名回归。`run-001` 原目录保留，不覆盖失败证据。

### 5.2 run-002

修复后，产品 `AlfworldBenchmarkRunner -> pinned Adapter -> THOR reset` 真实运行。结果：

```text
trial: valid_unseen/look_at_obj_in_light-Statue-None-FloorLamp-219/
       trial_T20190908_041333_727215/traj_data.json
expected scene: FloorPlan219
setup trigger: scan_pose_mismatch
final failure: scan_time_scale_restore_rejected
classification: execution_state_uncertain
score eligible: false
setup backend actions: 5
model backend actions: 0
Provider requests/attempts: 0
total backend/external requests: 5
```

独立 verifier 输出 `slice_verified=true`、`overall_status=incomplete`、exit 2。这里的 `slice_verified` 只表示现有 artifact 的 bijection/计数检查没有发现额外矛盾；由于 reset terminal 发生在 Provider 构造前，Provider/image/tool 链没有被执行，不能把该字段解释为行为 PASS。

### 5.3 run-003

直接完整 diff review 发现，产品已验证磁盘 trial 身份，但普通 THOR reset 尚未把实际 runtime scene 与 manifest 的 `expected_logical_scene` 比较。修复后只接受精确的 `FloorPlanN_physics -> FloorPlanN` 映射；错误 physics scene 返回 `runtime_scene_mismatch`，bare/noncanonical scene 返回 `reset_identity_unreadable`，两者都在 setup action 前终止并关闭环境。对应两个参数化回归通过。

`run-003` 是该生产代码修复后的最终 Gate B 复跑。真实 FloorPlan219 通过新增 identity gate，随后复现 `run-002` 的同一 reset terminal、五次 setup action、零 model/Provider action，以及 `slice_verified=true`、`overall_status=incomplete`、verifier exit 2。这证明新增 identity gate 没有阻挡正确场景；它不把后续 reset failure 变成 PASS。

两个可运行切片还共同暴露一个证据缺口：terminal 引用了 `reset-transaction.json`，但两个 run directory 都没有该文件。该 ref 尚未形成可独立读取的持久化 reset ledger，保持公开缺陷。

## 六、十 Episode 运行

实施计划要求从六个 historical exact row 和四个预先锁定 candidate 构造固定十 Episode manifest，禁止根据 Gate 结果挑选更好 trial。由于 Gate A 没有产生 `exact-cases-v3.json`，仓库中不存在 `config/alfworld_v18_regression_trials.json`。

运行环境同时没有可用 API key 环境变量。缺少任一前提都不能诚实启动固定十 Episode real-API run，因此本次没有 PID、没有部分 Episode，也没有十行 summary。状态记录为 `UNAVAILABLE`，不是 0/10、不是 skipped PASS，更不是用旧 Shelf 证据替代。

## 七、已知边界

- 完整 Gate B 20-case matrix 未运行；
- Gate B 可运行切片在 reset recovery terminal，未触达 Provider/tool dispatch；
- reset transaction evidence ref 未持久化为同名 artifact；
- Slice exact behavior 未验证；
- 固定十 Episode run 不可用；
- V1.8 正式 call graph 已被 AST guard 证明不进入 V1.7 candidate navigation/local-Put，但 compatibility implementations 仍物理存在于 `env_adapter.py` 和 `execution.py`，严格“产品源中零 legacy symbol”目标未满足；
- 更换 Python、ALFWorld、ai2thor 或 Unity build 后必须重新验证 runtime contract。

## 八、交付处置

用户明确要求不再追求 Gate A 完美，直接完成产品实现并让 Gate B 暴露问题。本次按该约束保留所有失败/缺失结果，完成直接 diff review、最终内部回归和一个远端本地 commit；不 push。
