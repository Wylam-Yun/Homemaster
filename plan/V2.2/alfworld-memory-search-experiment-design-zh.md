# ALFWorld 隐藏目标记忆搜索实验设计

## 1. 目标

在不删除 V1.8 离屏点导航能力的前提下，为 ALFWorld Gateway 增加实验配置，使模型不能借助 frozen scene index 直接导航到当前不可见的目标物体，但仍能导航到 Cabinet、Drawer、CounterTop 等搜索锚点。开启 HomeMaster 结构化记忆后，验证模型能否自主记录和跨 session 检索“目标物体—搜索锚点”事实。

## 2. 已锁定行为

- 配置开关属于 `alfworld_gateway`，写入真实的 ignored `config/homemaster.yaml`，并在 `config/homemaster.example.yaml` 提供去敏模板。
- 默认值保留现有 V1.8 行为，避免删除以后用于点导航的能力。
- 本次实验配置关闭“离屏目标物体直达”。
- 当前 strict-visible 的任意可公开目标仍按现有路径导航。
- 当前不可见的搜索锚点仍可使用 frozen snapshot pose 导航。
- 当前不可见、非搜索锚点的目标返回可纠正的 `target_not_visible`，backend action count 必须为零，不建立动作后观察屏障。
- 搜索锚点由 reset 时冻结的 THOR object metadata `receptacle=true` 确定，不维护手写类型名单，也不根据任务内容或模型输出动态改变。执行前必须与当前 typed metadata 一致；当前值缺失或发生漂移时 fail closed。
- HomeMaster 现有结构化记忆工具 `add_memory`、`search_memories`、`get_memory`、`update_memory` 保持可用；不强制模型调用、不自动补写记忆。
- ALFWorld session 可关闭和重建，但 HomeMaster memory store 在实验各轮之间保留。

## 3. 配置

配置名锁定为：

```yaml
alfworld_gateway:
  allow_offscreen_object_navigation: false
```

语义：

- `true`：保持当前行为，离屏语义目标可以消费自己的 frozen snapshot pose。
- `false`：离屏搜索锚点可以消费 frozen snapshot pose；其他离屏目标在发送 THOR 动作前以 `target_not_visible` 拒绝。

结构化记忆继续使用顶层配置：

```yaml
memory:
  enabled: true
```

Gateway 组合必须通过真实 Provider 请求边界确认模型实际收到结构化记忆工具 schema，不能只检查 Registry 内部对象。

## 4. 数据流

```text
robot_go_to(label)
  → 冻结场景索引确定 exact target（结果必须确定性）
  → 读取当前 strict visibility
  → 可见：按现有 snapshot pose 导航
  → 不可见且开关开启：按现有 snapshot pose 导航
  → 不可见、开关关闭、target.receptacle is true：
       按现有 snapshot pose 导航
  → 不可见、开关关闭、target 不是搜索锚点：
       target_not_visible / backend_action_count=0
```

配置从 HomeMaster Gateway 传入受管 HTTP worker，并最终进入 `OracleNavigationExecutor`。HTTP 边界不得通过 ambient 环境变量隐式控制此行为。

## 5. 记忆实验

第一轮使用固定 episode：

1. 模型导航多个搜索锚点并逐次 `observe`。
2. 找到隐藏目标后，观察模型是否自主调用 `add_memory`。
3. 独立读取 memory backend，确认记录真实持久化。

第二轮关闭旧对话和 ALFWorld session，reset 同一 episode：

1. 保留 HomeMaster memory store。
2. 观察模型是否调用 `search_memories` / `get_memory`。
3. 核对返回的 memory ID 和内容确实来自第一轮。
4. 核对模型是否优先导航到已记忆锚点。
5. 以外部 `won=true` 和成功返回码验收任务。

对照轮禁用记忆并从同一 episode 初态启动。逐轮比较搜索锚点访问数、`robot_go_to` 数、`observe` 数、失败动作数、耗时和最终成功状态，不使用跨实例聚合最优值。

## 6. 验收

- 配置为 `true` 时，现有离屏直达回归测试继续通过。
- 配置为 `false` 时，离屏普通目标在 THOR 动作前失败，返回 `target_not_visible` 且 backend action count 为零。
- 配置为 `false` 时，离屏搜索锚点仍产生一次真实导航动作，并以目标 strict-visible 和外部 pose 终态验收。
- Gateway HTTP worker 收到与 HomeMaster 配置一致的开关值。
- 真实 Provider 请求包含结构化记忆工具。
- 记忆写入必须从 backend 独立读回；跨 session 检索必须返回同一条已持久化事实。
- 实验最终结果必须同时报告模型是否自主写入、是否自主检索以及记忆是否减少搜索，不能只报告任务成功。

## 7. 不做

- 不删除 frozen scene index、pose snapshot 或离屏点导航实现。
- 不强制模型调用记忆或 planner。
- 不自动把观察结果写入记忆。
- 不根据隐藏 containment 信息替模型选择搜索锚点。
- 不用 prompt 约束冒充后端可执行性限制。
