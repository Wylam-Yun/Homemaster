# V2.2 ALFWorld Gateway 动作后观察验证设计

## 1. 文档状态

- 状态：owner 已确认核心设计决策；下一步细化为可执行实施计划。
- 范围：HomeMaster 迁移到 `hkust4` 后，通过飞书 Gateway 驱动同机 ALFWorld，并在每次导航或操作尝试后由模型调用 `observe` 做视觉黑盒验证，同时把同一张图片实时发给飞书用户。
- 当前阶段只写设计，不修改产品代码。
- ALFWorld 真环境位置：`/home/haodong2/weilin/red_bird/alfworld`。AI2-THOR 启动、固定 trial reset、外部动作回执、非空 PNG 和 Unity/Xvfb 清理已经在 `hkust4` 真环境复现成功；最终 demo episode 和完整演示动作链仍须在实施前锁定。

## 2. 目标演示

演示采用一条短而稳定的任务链：

1. 用户从飞书发来一个有歧义的任务。
2. 模型通过现有 `ask_user_question` 追问，飞书用户回复后恢复同一 session。
3. 模型确认目标后调用 `robot_go_to`。
4. ALFWorld 独立返回导航动作成功或失败。
5. 因 `robot_go_to` 的工具声明要求模型观察，Agent Loop 下一轮必须调用 `observe`。
6. `observe` 获取当前 PNG；同一份图片既作为 image block 进入下一次真实 Provider 请求的模型上下文，也通过 Gateway 发给飞书用户。
7. 模型看图后决定继续、重试或调用 `robot_manipulate`。
8. 每次 `robot_manipulate` 尝试后重复“调用 `observe`、模型看图、飞书发图”的过程。
9. ALFWorld 的真实状态判定与模型的视觉判定都满足后，模型才完成任务。

初版不增加独立的“阶段事件”协议。用户进度直接由现有工具结果、`observe` 图片和模型回复表达。

## 3. 核心概念：两种验证不能混用

HomeMaster 当前已经有 `VerificationPolicy`。它解决的是执行器层验证，例如读取外部状态、核对工具回执、判断工具结果能否算成功。它不代表模型已经看过动作后的现场。

V2.2 必须保留两个独立事实：

| 事实 | 谁负责 | 回答的问题 |
| --- | --- | --- |
| 环境/执行验证 | 工具执行器、ALFWorld adapter、现有 `VerificationPolicy` | 动作是否被环境接受，真实位置、inventory、holding、containment 等状态是否符合规则？ |
| 模型观察验证 | Agent Loop、模型、`observe` | 模型是否真的取得动作后的新图片，并用 VLM 黑盒检查现场？ |

因此不能把现有 `VerificationPolicy` 直接改成“动作后调用 `observe`”。那会让一份状态同时表示两个不同事实，并可能把环境回执成功误当成模型已经看图。

## 4. 方案比较

### 方案 1：只在工具描述里提示模型

在 `robot_go_to` 和 `robot_manipulate` 的描述中写“执行后请调用 `observe`”。改动最少，但模型可能跳过、同批调用后续动作或直接回答完成，无法稳定演示，也无法形成可测试的不变量。

### 方案 2：工具声明标记，Agent Loop 强制待观察屏障

工具声明增加一个含义单一的字段：

> 这个工具真实尝试环境动作后，模型必须在下一轮调用 `observe`。

工具自己声明需求；Agent Loop 通用执行需求，不写死 `robot_go_to` 或 `robot_manipulate` 的名字。该方案改动范围可控，规则可测试，正好满足当前演示。

### 方案 3：声明完整的多种验证策略

每个工具声明验证工具、证据类型、通过条件、失败重试策略等。以后可以支持文件重读、数据库查询、传感器检查等不同验证方式，但初版只有一个 `observe`，现在引入会造成没有真实用例支撑的抽象。

### 推荐

MVP 采用方案 2。工具声明先保留“是否要求模型观察”这个简单接口；等出现第二种真实观察手段后，再以实际用例把它升级为方案 3。现有 `VerificationPolicy` 不改语义。

## 5. 工具声明设计

建议在 HomeMaster 内部的 canonical 工具声明上增加 `requires_model_observation`，默认关闭。它不属于工具输入参数，也不原样发送给外部模型 Provider。

初版声明如下：

| 工具 | 动作后要求模型观察 | 说明 |
| --- | --- | --- |
| `robot_go_to` | 是 | 只要真实调用了 ALFWorld backend，成功或失败都进入待观察状态 |
| `robot_manipulate` | 是 | 只要真实调用了 ALFWorld backend，成功或失败都进入待观察状态 |
| `observe` | 否 | 它负责满足待观察要求，不能再次触发自身 |
| `robot_verify` | 否 | 保留环境状态验证职责，不替代视觉观察 |
| 查询、记忆、规划、追问等通用工具 | 否 | 默认不增加额外模型轮次 |

字段命名必须明确包含“model observation”，不能沿用含义模糊的 `requires_verification`。仓库旧 `ToolSpec.requires_verification` 与 canonical `VerificationPolicy` 已存在历史语义，实施时需要完成全量引用审计，不能把旧字段直接改义后让其他工具行为静默变化。

该字段只描述需求。真正的执行约束由 Agent Loop 负责。

## 6. Agent Loop 的待观察屏障

Agent Loop 为当前 session/run 保存一条待观察状态，至少记录：触发动作的工具名、tool call id、动作结果、动作是否触达 backend，以及待调用的观察工具 `observe`。

### 6.1 设置屏障

满足以下全部条件时设置：

1. 工具声明 `requires_model_observation=true`。
2. 工具结果明确报告 `backend_attempted=true`。
3. 工具调用已经形成一个可提交的结果，无论该结果是成功、确认失败还是结果不确定。

参数校验失败、权限拒绝、工具不存在、在执行前取消或 backend 根本未调用时，不设置屏障，因为现场没有因该动作产生新的可观察尝试。

### 6.2 屏障期间允许的行为

屏障存在时：

- 下一次 Provider 请求只允许模型选择 `observe`。
- 下一次 Provider 请求动态加入一条短协议提示，说明动作已经尝试，必须先调用 `observe` 并根据视觉证据判断结果；该提示不包含 episode、物体、场景或固定动作顺序。
- 模型不能调用另一个导航或操作工具。
- 模型不能用文本直接宣称任务完成。
- 模型若不调用 `observe`，本轮视为协议不满足，进行有界重试；超过限制后明确失败，不能静默放行。
- Provider 的“强制工具选择”具体 API 在目标 Provider 真环境核对前标 `UNVERIFIED`。即使 Provider 不支持强制参数，Harness 仍需通过工具披露收窄、结果校验和有界重试保证规则。

动态协议提示采用以下固定语义，内容作为外部化 prompt 数据维护：

```text
A state-changing environment action was attempted.
Before taking another action or giving a final answer, call `observe`
and use the returned visual evidence to evaluate the action outcome.
```

正确性不依赖提示词。提示只用于减少模型困惑和无效重试；工具披露收窄、结果校验、有效图片门和有界重试才是强制协议。

这不是执行器偷偷截图，也不是 Harness 伪造一个 `observe` tool result。模型必须在看到动作结果后的新一轮真实发出 `observe` tool call。

### 6.3 解除屏障

只有 `observe` 成功返回一张经过格式和非空像素校验的有效图片后才解除屏障。

- `observe` 截图失败：保留屏障，允许有界重试或明确终止。
- `observe` 返回空图片、损坏图片或非图片：保留屏障。
- 图片成功，但模型认为动作目标未达到：屏障已经完成，因为“模型已观察”这个事实成立；模型随后可以重试动作。新的动作会建立新的屏障。
- ALFWorld 环境报告动作失败，但动作已尝试：仍然必须 `observe` 并发图，让模型和用户都看到失败后的现场。

### 6.4 同一轮多个工具调用

当前 runtime 会批量执行同一 assistant message 中的多个 tool call。若模型同批发出 `robot_go_to + observe + robot_manipulate`，后两个调用可能在模型看见前一个结果之前执行，违反黑盒验证要求。

MVP 采用保守规则：一个 assistant batch 只要包含“动作后要求观察”的工具，该 batch 就只能包含这一个动作调用；否则整批在触达 backend 前拒绝，并要求模型重新发出单个动作。待观察状态下的 batch 同样只能包含一个 `observe`。这样不会在拒绝时留下已执行一半的外部动作。

## 7. 图片进入模型和飞书的同一数据流

`observe` 已能从当前 backend 获取 PNG，并生成模型可见图片。现有 Gateway 也已经具备工具图片的 artifact 发布、公共事件投影、MEDIA 出站和飞书上传发送链路。V2.2 不新建第二套截图或发图通道。

`observe` 成功返回的 `ResultImage` 必须保留在紧接着的 Provider 请求上下文中。snapshot 可以继续按现有策略剥离旧图片，但不得在模型首次消费该观察结果前剥离、文本化或只保留内部 hash。

目标数据流：

```text
ALFWorld 当前 frame
        |
        v
observe 返回唯一 ResultImage
        |
        +--> Provider 下一轮消息中的 image block --> 模型视觉判断
        |
        +--> ArtifactPublisher --> Gateway MEDIA --> 飞书图片
```

两条分支必须来自同一个 canonical 图片结果。验收时用 content hash 和 pixel hash 证明模型收到的图片与飞书 artifact 对应同一帧，不能各自重新截图。

图片发送不以动作成功为条件。唯一条件是本次 `observe` 成功取得有效图片。若飞书发送失败，Gateway 必须显式报告媒体出站失败；不能因为模型已看到图片就宣称用户也看到了。

## 8. CLI 环境选择与工具披露

环境选择是进程启动时的上游决策，不做运行中的动态多环境切换：

```bash
homemaster --gateway
# 只启用通用工具；默认读取 config/homemaster.yaml

homemaster --gateway --alfworld
# 启用通用工具 + 具身工具；默认读取 config/homemaster.yaml
```

`--config` 只用于覆盖默认配置路径，例如 `homemaster --gateway --alfworld --config other.yaml`。MVP 只承诺用户指定的根入口，不要求额外保留 `homemaster gateway --alfworld` 等价入口。

工具披露规则：

- 通用工具与环境无关，至少包括 `ask_user_question` 和 `observe`；规划、任务状态、记忆等工具按其真实依赖逐项归类，不能因为历史模块名叫 Home 就自动算环境工具。
- 具身工具包括 `robot_go_to`、`robot_manipulate`、`robot_verify`，只在 `--alfworld` 模式披露。
- Coworker 工具只在未来明确的 Coworker 入口披露；普通 Gateway 和 `--alfworld` Gateway 都不披露。
- 普通 `--gateway` 只披露通用工具，不创建具身或 Coworker backend。
- `--gateway --alfworld` 披露通用工具和具身工具，创建 AI2-THOR adapter，但不创建 Coworker backend。
- 环境选择必须在 Registry 组成阶段完成，不能只设置 `profile=alfworld` 却继续把所有环境工具都发给 Provider。

| 启动模式 | 通用工具 | 具身工具 | Coworker 工具 |
| --- | --- | --- | --- |
| `homemaster --gateway` | 开启 | 关闭 | 关闭 |
| `homemaster --gateway --alfworld` | 开启 | 开启 | 关闭 |
| 未来 Coworker 专用入口 | 开启 | 关闭 | 开启 |

当前 `build_universal_tool_registry()` 会合并 Home、ALFWorld 和 Coworker 工具，`profile` 主要绑定 backend，不能满足上述披露约束。实施时应消除含义模糊的“Home 工具”分类，把组成关系改成“通用工具 + 当前显式启用的环境工具”，并在实际 Provider request 边界核对最终工具名单。

## 9. ALFWorld 与 Gateway 的生命周期

当前 benchmark runner 会显式创建、reset、注入和关闭 ALFWorld adapter；Gateway 只创建不带 ALFWorld environment 的 application，飞书产生的 `RunRequest` 也没有 ALFWorld environment、translator 和 terminal owner。这是 `--alfworld` 的主要接线缺口。

有三种部署选择：

1. **单 episode、单活动会话**：Gateway 启动时加载一个锁定 demo case，同一时间只接受一个演示 session。实现最短、演示最稳定，但不支持多人并发。
2. **每个 Gateway session 一个 episode**：按 session 建立、恢复和关闭 adapter。并发隔离更好，但资源、恢复和清理复杂度明显增加。
3. **ALFWorld 独立服务**：HomeMaster 通过远程协议管理 episode。部署灵活，但当前同机演示没有必要新增网络协议。

MVP 推荐方案 1：启动 `--alfworld` 时创建一个 application-owned 的固定 demo adapter，reset 到锁定 episode，注入 Gateway 每次 run，并对其他并发 session 明确拒绝。进程关闭时由 application 生命周期关闭 adapter。接口边界保留按 session 解析 environment 的可能性，但初版不实现方案 2。

ALFWorld root、data root、split 和固定 episode 标识必须来自 ignored 的真实配置，仓库只提交完整的 `.example` 占位配置，不能硬编码 `hkust4` 用户目录。启动 composition 必须从配置确定性解析 data root，不能依赖交互 shell 是否碰巧设置 `ALFWORLD_DATA`。episode 必须在真环境列举后选定并锁定；目前最终 demo ID 仍为 `UNVERIFIED`。

## 10. 模糊意图确认

现有 `ask_user_question`、`waiting_user` 和 Gateway resume 已能支撑飞书追问。MVP 不新增一套意图状态机，但给 ALFWorld demo context 一条明确规则：目标物、目标位置或期望操作存在实质歧义时，先调用 `ask_user_question`，不得启动环境动作。

演示用输入应固定为一个能稳定触发单次追问、又能在用户补充后映射到锁定 episode 的例子。最终文案要等真环境 episode 确认后确定，不能先写一个与实际物体列表不一致的任务。

仅靠模型 prompt 不能证明所有模糊输入都必然追问，因此 V2.2 MVP 的承诺是“锁定演示用例通过真实 Provider 验收”，不是建立通用自然语言歧义判定器。若以后要求产品级强制确认，再单独设计结构化意图门。

## 11. 状态、恢复与可观测性

待观察状态属于 Agent Loop 的执行状态，而不是图片事件。它必须写入现有 session snapshot，确保进程中断或 Gateway 恢复时不会遗忘一次尚未完成的观察。恢复后第一步仍只能 `observe`。

关键 JSONL 至少记录：

- 环境模式和最终披露的工具名集合。
- 动作 tool call id、工具名、参数、结果、`backend_attempted`、耗时。
- 待观察屏障的建立、保持、解除和失败原因。
- `observe` tool call id、来源动作 call id、图片 content/pixel hash。
- artifact handle、Gateway MEDIA 投影和飞书出站回执。

日志证明数据流经过了哪些边界，但不能替代 ALFWorld 外部状态和飞书实际可见图片的黑盒验收。

## 12. 实施工作包

### WP0：真环境 linchpin 核对

- 建立一个由项目 lock 管理、同时包含 Gateway 与 ALFWorld/AI2-THOR 依赖的目标 venv；不能继续依赖两个互相缺包的运行环境。
- 在该目标 venv 中复验已经通过的 AI2-THOR 启动、reset、当前 frame、动作返回码、终态查询和关闭。
- 从 ignored 配置显式解析 ALFWorld data root，并回归证明未设置 ambient `ALFWORLD_DATA` 时仍能找到锁定 episode。
- 枚举可用 episode，选择一个动作短、视觉变化明显、可重复 reset 的 demo case并锁定目标。
- 核对目标 Provider 是否支持单工具披露及强制 tool choice；未核对前保持 `UNVERIFIED`。
- 用真实飞书应用核对图片上传/发送返回码和用户端可见终态。

### WP1：工具声明与一致性审计

- 增加“动作后要求模型观察”的 canonical 声明。
- 同步所有工具实现/adapter 的接口，并增加全实现一致性审计。
- 只给 ALFWorld `robot_go_to`、`robot_manipulate` 开启。
- 保证字段不泄漏为 Provider 不认识的 schema 字段。

### WP2：Agent Loop 待观察屏障

- 在批量执行前拒绝含标记动作的多调用 batch。
- 根据 `backend_attempted` 建立屏障。
- 屏障期间只披露 `observe`，动态加入固定的短协议提示，并要求下一轮真实调用 `observe`，阻止动作和直接完成。
- 有效图片解除屏障；截图失败保持屏障并有界处理。
- 将屏障纳入 snapshot/resume。

### WP3：环境选择和工具披露

- 增加 `--alfworld`。
- Registry 改为通用工具与显式选中环境工具的组合，移除含义模糊的“Home 工具”分类。
- 默认模式只包含通用工具，明确排除具身和 Coworker schema。
- ALFWorld 模式包含通用工具与具身工具，明确排除 Coworker schema。
- 从真实 Provider request 边界审计，而不是只看 Registry 内部列表。

### WP4：Gateway ALFWorld composition

- application-owned 创建、reset、注入、独占和关闭固定 demo adapter。
- 给飞书 `RunRequest` 注入 ALFWorld environment、translator、trace/observer 和 terminal owner。
- `homemaster --gateway --alfworld` 默认读取 `config/homemaster.yaml`，仅在显式传入 `--config` 时覆盖。
- 复用现有 artifact/MEDIA 链路，不增加阶段事件。

### WP5：演示验收和文档同源

- 完成单测、跨边界集成测试和 `hkust4` + 真实 Provider + 真实飞书黑盒验收。
- 更新 README、用户指南、架构文档、CHANGELOG 和 `progress.md`。
- 全部实现、验证和文档完成后执行一次最终代码 reviewer gate。

## 13. 验收门

每项都按实例独立断言，不能用任意一个成功掩盖其他失败：

1. `homemaster --gateway` 默认读取 `config/homemaster.yaml`；真实 Provider 请求包含 `ask_user_question`、`observe` 等通用工具，不含具身或 Coworker 工具。
2. `homemaster --gateway --alfworld` 默认读取同一配置；真实 Provider 请求包含通用工具、`robot_go_to`、`robot_manipulate`、`robot_verify` 和 `observe`，不含 Coworker 工具，且具身动作绑定 ALFWorld 实现。
3. 模糊演示输入先产生飞书追问；用户回复恢复同一 session 后才首次触发环境动作。
4. 每个已触达 backend 的 `robot_go_to` 实例后，下一次模型工具调用都是独立的 `observe`。
5. 每个已触达 backend 的 `robot_manipulate` 实例后，下一次模型工具调用都是独立的 `observe`。
6. 每个待观察实例的下一次 Provider 请求只披露 `observe`，包含固定的短动态协议提示，且提示不含 episode、物体或场景特例。
7. 动作成功和动作失败各至少一个实例都触发 `observe` 并发图。
8. 参数校验失败或权限拒绝的动作实例证明 backend 调用次数为零，且不错误建立观察屏障。
9. 同 batch 的动作加后续工具在任何 backend 调用前被整体拒绝。
10. `observe` 失败时屏障未解除，模型不能继续动作或完成任务。
11. 每张成功观察图片在紧接着的真实 Provider 请求中是可解析的 image block，且在模型首次消费前未被 snapshot 图片剥离策略移除。
12. 同一图片产生 Gateway MEDIA；hash 与 Provider image block 一致；飞书 API 返回成功，用户端真实可见。
13. ALFWorld 动作返回码成功，并通过独立外部状态查询确认位置、inventory 或目标状态真的变化。
14. Gateway 关闭后 ALFWorld adapter、Unity、Xvfb、飞书 worker 和相关外部连接均终止，无遗留进程。

第 12 项最后的“用户端真实可见”如无法自动从飞书接收端独立读取，只能标为人工黑盒门，不得用上传 API 成功或内部 trace 替代。

## 14. 明确不做

- 不新增阶段事件协议。
- 不让执行器隐藏调用 `observe`。
- 不把截图静默合并进动作工具来冒充模型观察。
- 不让 `observe` 替代 ALFWorld 的环境状态验证。
- 不在 MVP 中引入多种 observation/verification policy。
- 不支持一个 Gateway 进程内动态切换具身与 Coworker 环境。
- 不在 MVP 中承诺多用户并发 ALFWorld episode。
- 不先构造通用自然语言歧义分类器。

## 15. 当前阻断点与结论

逻辑上没有不可行的阻断点，现有图片链路、飞书恢复和 ALFWorld 截图能力都可复用。真正需要实现的阻断点是：

1. 当前工具声明没有独立表达“要求模型动作后观察”。
2. Agent Loop 当前会批量执行 tool call，也没有待观察屏障。
3. 默认 Registry 当前混合多个环境工具，环境选择没有控制 Provider 工具披露。
4. Gateway 尚未拥有和注入 ALFWorld adapter 生命周期。
5. 当前 Gateway venv 与 ALFWorld venv 互相缺少对方依赖，尚未形成一个受项目 lock 管理的统一运行环境。
6. ALFWorld data root 仍可能依赖 ambient `ALFWORLD_DATA`，必须改为 ignored 配置驱动。
7. 固定 demo episode、Provider 强制 tool choice 和飞书用户端图片终态仍需真环境锁定。

推荐的 V2.2 MVP 是：**`--alfworld` 在启动时选择环境；工具声明 `requires_model_observation`；Agent Loop 在真实动作尝试后强制下一轮模型调用 `observe`；`observe` 的同一张图片同时给模型和飞书用户；ALFWorld 自己继续负责真实状态验真。**
