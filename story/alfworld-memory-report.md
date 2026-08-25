# HomeMaster 阶段工作汇报

## 汇报主题

HomeMaster 本阶段新增三项能力：记忆系统让 Agent 跨任务积累经验，权限系统让真实操作在执行前受到控制，Web Console 让用户看见执行过程并在必要时介入。

## 记忆系统

记忆系统覆盖完整生命周期：Add 保存事实和经验；Search / Recall 在新任务中检索相关内容；Update 修正旧记忆并保留版本关系；Delete 归档错误或过期记忆；Feedback 根据后续结果规划新增、修改、删除或保持；History 追踪版本演变；Dreaming 定期整理重复、冲突和相关经验。

任务结束时，Session Finalizer 从真实对话和工具结果中提取经验。保存结果需要经过真实存储回读确认。历史经验只作为决策参考，不能授予新的操作权限，也不能替代当前环境核验。

## ALFWorld 真实案例

### Episode 0008：形成经验

- 任务：look at mug under the desklamp。
- 动作：找到并拿起 Mug，接近 DeskLamp，在持有 Mug 时打开灯。
- 外部终态：success=true，goal condition=1.0，4 steps。
- 会话结束后，系统保存了这类任务的操作经验。

### Episode 0009：召回经验

- 新任务：look at basketball under the desklamp。
- Agent 调用 mindmemos_search，查询 desklamp location desk。
- 搜索结果命中 Episode 0008 形成的经验，来源会话明确指向 0008。
- 这证明跨任务写入和召回链路真实生效。Episode 0009 后续因 Provider transport error 中断，因此不能用于证明最终任务成功。

## 权限系统

所有工具调用经过统一执行门：参数校验、权限判断、必要时人工确认，然后才允许申请资源并调用后端。拒绝、超时、会话结束和连接中断均按拒绝处理。

- full_auto：允许自动执行，适合 benchmark 和无人值守任务。
- confirm：修改性操作需要人工确认。
- plan：允许规划和只读操作，阻止真实修改。

CLI、Web 和飞书使用同一套权限判断与审批生命周期，不各自实现第二套权限策略。当前 ALFWorld 十轨迹采用自动执行模式，本页不把它们描述成 ALFWorld 审批实验。

## Web Console

Web Console 是同一个 Application Runtime 的浏览器界面，支持 Session 创建与恢复、实时 Thinking 和回答、逐工具调用状态、Artifact 下载、运行取消以及危险操作审批。当前仅允许 loopback 访问，不应直接暴露到不可信网络。

## 阶段结论

HomeMaster 已形成“任务执行 -> 经验沉淀 -> 相似任务召回 -> 反馈纠错与整理”的记忆闭环，同时由统一权限门控制真实操作，并通过 Web Console 将过程呈现给用户。下一步需要补充 ALFWorld confirm 模式的批准 / 拒绝外部终态实验，并扩大成对实验规模以评估 Memory 对任务成功率的影响。
