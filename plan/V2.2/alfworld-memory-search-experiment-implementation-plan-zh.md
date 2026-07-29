# ALFWorld 隐藏目标记忆搜索实验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: use the repository execution discipline and implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留离屏 receptacle 点导航的同时，通过 `config/homemaster.yaml` 禁止离屏普通物体直达，并确认 HomeMaster 结构化记忆工具在 ALFWorld Gateway 中真实可用。

**Architecture:** `AlfworldGatewayConfig` 持有部署开关，HomeMaster 通过受管 worker 的显式 CLI 参数传递该值，worker 构造 `AlfworldEnvAdapter`，最终由 `OracleNavigationExecutor` 在任何 THOR 动作之前执行可见性与 receptacle 门。搜索锚点身份来自真实 THOR `receptacle` metadata，并进入冻结场景对象契约；结构化记忆沿用 HomeMaster 通用 `add_memory/search_memories/get_memory/update_memory` 工具，不引入第二套记忆写入路径。

**Tech Stack:** Python 3.12、Pydantic、dataclass contracts、受管 loopback HTTP worker、pytest、ALFWorld/AI2-THOR。

---

### Task 1：锁定配置与冻结对象契约

**Files:**
- Modify: `src/homemaster/config/config.py`
- Modify: `src/homemaster/benchmarking/alfworld/pose_snapshot.py`
- Modify: `src/homemaster/benchmarking/alfworld/env_adapter.py`
- Modify: `tests/homemaster/test_config_resolution.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_pose_snapshot.py`

- [ ] 在配置测试中断言 `alfworld_gateway.allow_offscreen_object_navigation` 默认是 `true`，显式 `false` 可解析，未知字段仍被拒绝。
- [ ] 在冻结对象契约测试中构造 `receptacle=True/False`，断言 canonical snapshot 的身份和 round-trip 保留该值。
- [ ] 运行上述测试并确认先失败。
- [ ] 给 `AlfworldGatewayConfig` 增加 `allow_offscreen_object_navigation: bool = True`。
- [ ] 给冻结 `SceneObjectRef` 和当前 event 的 `SceneObjectScanInput` 都增加非可空 `receptacle: bool`；两者都只接受真实 metadata boolean，缺失或类型错误时使 scene/event 进入既有 malformed/uncertain 路径，禁止猜测类型。
- [ ] 更新所有 `SceneObjectScanInput` 实现和测试构造点，运行 pose snapshot、reset transaction 和 config 测试至 PASS。

### Task 2：在导航执行器前置拒绝离屏普通物体

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/execution.py`
- Modify: `src/homemaster/benchmarking/alfworld/env_adapter.py`
- Modify: `tests/homemaster/benchmarking/test_alfworld_navigation.py`

- [ ] 新增失败测试：开关关闭、目标 `strict_visible=False`、`receptacle=False` 时返回 `target_not_visible`，`backend_action_count == 0`，Gateway 收到零动作。
- [ ] 新增成功测试：同一配置下 `receptacle=True` 的离屏目标仍消费其 frozen pose，准确目标在返回 event 中 strict-visible，`backend_action_count == 1`。
- [ ] 新增兼容测试：开关开启时，`receptacle=False` 的离屏目标保持 V1.8 一次导航行为。
- [ ] 新增漂移测试：frozen `SceneObjectRef.receptacle` 与当前 typed metadata 不一致时返回 `execution_state_uncertain` 且不动作；资格判断只使用经一致性核对的 frozen 值。
- [ ] 运行三个测试并确认首个失败。
- [ ] 给 `OracleNavigationExecutor` 增加 `allow_offscreen_object_navigation: bool = True`；目标锁定后、pose lookup 前执行：

```python
if (
    not self._allow_offscreen_object_navigation
    and lock.observation.strict_visible is not True
    and not lock.target.receptacle
):
    return _navigation_failure(requested_label, "target_not_visible", trace, lock)
```

- [ ] 在上述门之前要求 `lock.target.receptacle == lock.current_object.receptacle`，否则 fail closed；`AlfworldEnvAdapter` 保存开关并传入每次 `OracleNavigationExecutor` 构造；运行导航和 adapter 测试至 PASS。
- [ ] 增加 canary 负向测试：隐藏目标的 parent/child/exact ID/pose/hash 不得进入模型可见 tool result、下一次 Provider request 或 public event；模型只收到请求 label、`target_not_visible` 和 `backend_attempted=false`。受限内部 JSONL 可保留诊断 ID。

### Task 3：显式穿过受管 HTTP worker 边界

**Files:**
- Modify: `src/homemaster/benchmarking/alfworld/http_client.py`
- Modify: `src/homemaster/benchmarking/alfworld/http_worker.py`
- Modify: `src/homemaster/gateway/alfworld.py`
- Modify: `tests/homemaster/gateway/test_alfworld_gateway_binding.py`
- Modify: `tests/homemaster/gateway/test_alfworld_http_live.py`

- [ ] 在 Gateway binding 测试中断言配置值原样传给 `AlfworldHttpEnvironment.start`。
- [ ] 在 HTTP client 命令测试中断言只生成一个明确参数：`--allow-offscreen-object-navigation=true|false`，不读取 ambient 环境变量。
- [ ] 在 live worker 测试中使用 `false` 启动固定 episode。先从 worker-side raw event 独立锁定普通目标确实 `strict_visible=false/receptacle=false`、锚点确实 `strict_visible=false/receptacle=true`；逐实例比较 THOR raw action sequence，证明失败目标没有新增动作，成功锚点新增一次动作。
- [ ] 运行测试并确认传递断言先失败。
- [ ] `AlfworldHttpEnvironment.start()` 接收 bool 并生成规范小写 CLI 值。
- [ ] worker parser 使用严格 `true/false` 解析函数，构造 adapter 时传入 bool。
- [ ] worker readiness 和 `/v1/health` 回报 canonical bool；client 与请求值精确比对，不一致时 fail closed。测试证明同名 ambient 环境变量不能覆盖 argv。
- [ ] `create_alfworld_gateway_binding()` 从 `AlfworldGatewayConfig` 显式传入该值。
- [ ] 成功锚点逐实例核对 THOR `lastActionSuccess=true`、actual pose 命中锁定 pose、目标 action 后 strict-visible；运行 Gateway binding 和 HTTP live 测试至 PASS，并确认 worker/Unity/Xvfb 全部退出。

### Task 4：确认结构化记忆真实披露并配置实验环境

**Files:**
- Modify: `tests/homemaster/integration/test_observation_profiles.py`
- Modify: `tests/homemaster/tools/test_universal_registry.py`
- Modify: `config/homemaster.example.yaml`
- Modify ignored deployment file: `config/homemaster.yaml`

- [ ] 增加 Registry 测试：`environment="alfworld", memory_enabled=True` 只包含结构化 `add_memory/search_memories/get_memory/update_memory`，不包含 legacy `memory_retriever/memory_writer`；`memory_enabled=False` 时六者都不包含。
- [ ] 增加真实 Provider request 边界测试，逐项断言上述正负名单，并断言两种情况下 Gateway `AlfworldBenchmarkConfig.memory_mode` 都保持 `"disabled"`，不把 legacy benchmark memory 与 HomeMaster 结构化记忆混为一谈。
- [ ] 运行测试，确认现有装配若已正确则形成回归保护；若失败，只修复通用 HomeMaster memory 装配，不启用 legacy `memory_retriever/memory_writer` 双轨。
- [ ] 在模板加入 `allow_offscreen_object_navigation: true` 及语义注释。
- [ ] 在 ignored `config/homemaster.yaml` 设置：

```yaml
memory:
  enabled: true
alfworld_gateway:
  allow_offscreen_object_navigation: false
```

- [ ] 用配置加载器读取真实配置，断言两项实际值分别为 `true` 和 `false`，不输出任何密钥。

### Task 5：回归、文档和实验 episode 候选

**Files:**
- Modify: `README.md`
- Modify: `docs/alfworld-user-guide.md`
- Modify: `docs/architecture/alfworld-harness.md`
- Modify: `CHANGELOG.md`

- [ ] 运行 formatter、ruff、compileall、接口实现一致性审计及相关 ALFWorld/Gateway/memory 测试。
- [ ] 启动真实 Gateway，独立检查 health 返回成功、worker 使用预期配置、飞书连接建立。
- [ ] 从 trial manifest 逐项读取真实 task、scene 和初始 object metadata，筛选至少三个“目标普通物体初始 strict-invisible、存在可导航 receptacle 搜索路径”的候选；不得仅凭任务名猜测。在这次真机逐对象核对完成前，`receptacle` metadata 完整性及“receptacle 即稳定导航锚点”统一标 `UNVERIFIED`。
- [ ] 在切换 `trial_index` 前向用户报告候选 episode、目标物体、场景、为什么适合测位置记忆及预计搜索路径，等待用户选择或确认推荐。
- [ ] 更新用户指南、架构不变量、README 能力说明和 CHANGELOG；说明默认兼容、本次配置关闭、记忆工具不强制调用。
- [ ] 完成最终代码评审后逐条处理发现，针对性复测；以 CHANGELOG 同源内容提交。

### Task 6：用户确认 episode 后执行真实记忆实验

**Artifacts:**
- Runtime JSONL、ALFWorld raw events、memory backend readback、每轮指标报告。

- [ ] Round A 使用用户确认的固定 episode 和全新对话执行；不提示或强制记忆调用，记录模型是否自主 `add_memory`。
- [ ] 若模型自主写入，绕过模型和 tool trace 从 memory backend 独立读取同一 memory ID/content，确认真实持久化；若未自主写入，如实记录“自主写入失败”，不代写。
- [ ] 关闭旧对话和 ALFWorld session，保留 memory store，reset 完全相同 episode 后执行 Round B；记录是否自主 `search_memories/get_memory`，并核对返回 ID/content 与 Round A 持久化记录一致。
- [ ] 使用隔离的 memory-disabled 配置从相同初态执行 Round C，不删除或污染真实 memory store。
- [ ] 对 A/B/C 每轮分别报告锚点访问数、`robot_go_to` 数、`observe` 数、失败动作、耗时、每个外部返回码和最终 `won`；只有 B 的真实检索影响了导航且相对 C 减少搜索时，才判定记忆对该 episode 有用。
