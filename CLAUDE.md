# HomeMaster Agent Rules

## Graceful cleanup 信号纪律

- 区分 session rotate 与 process shutdown：`/new` 只能把旧 Session 收尾排入 application-owned FIFO 后立即
  返回，禁止等待 queue idle；只有 terminal exit 才 drain。不要用“Session 结束”这个共同表象合并两种边界。
- terminal queue drain 和 owned-resource close 前，生命周期 owner 必须显式接管 SIGINT；repeated SIGINT
  不得绕过已经承诺的清理阶段。后台 Session finalization 不得接管 SIGINT，普通 run 的取消语义保持不变。
- 信号回归必须把真实 SIGINT 分别注入耗时 finalizer 和 close 边界，断言信号后的外部可见完成状态、退出码和
  resource close 次数；只 mock handler、只看“开始清理”日志或只断言协程被调用都不算完成证据。

## 共享 deadline 异常分类纪律

- 用 `asyncio.timeout()` 共享总预算时，只在该 `Timeout` 对象的 `expired()` 为 true 时把
  `TimeoutError` 分类为总 deadline 耗尽；被 await 的 backend 主动抛出的同类异常必须保留
  backend 语义。测试必须在存在但未到期的 deadline 下注入 backend timeout，并与真正到期对照。

## V2.0 精确运行时文本纪律

- 已锁定 owner 决策为 candidate 2：经过现有认证、权限、tenant/session/run ownership、事件字段
  allowlist 和 size/binary 边界后，运行时文本保持原值。不得按 key、credential 形状、配置字面值、
  URL、路径或 chunk 重写 user/provider/tool/MCP/Feishu/config/doctor/dry-run/log/trace/audit/repr 文本。
- 精确保真不扩大字段集合，也不授予权限。认证失败不得回显来访 credential；Git 中的配置、示例、源码、
  文档和 fixture 只允许占位值；二进制及仅用于 transport 的宿主存储路径继续使用 tenant ACL artifact
  或 opaque reference。
- 任何外部 sink 只输出其 typed schema 已选择的字段，但已选择字段的文本值必须精确。不得新增可选
  raw/redacted mode，也不得以收尾为由重建工具路由架构。
- canonical immutable result 进入 Pydantic/session/provider message 前必须递归 thaw 为普通 JSON 容器；
  回归要对包含 nested mapping 的真实 message 执行 deep copy，不能只断言顶层 dict。

## Gateway 远程边界纪律

- 浏览器 clipboard 回填成功必须验证目标内容与发送字节完全一致，并在回执中核对两侧 SHA-256；
  `preventDefault`、clipboard item 数量或任意 DOM 变化都不能单独证明回填成功。对抗测试必须覆盖
  “接受 paste 并显示错误状态、但未生成目标内容”的控件。
- 飞书部署订阅的每个事件都必须有准确 dispatcher 注册：业务事件进入 typed ingress，非业务访问事件使用
  显式 no-op ACK，未知事件 fail closed。用锁定真实 SDK payload 逐事件断言 ACK 和副作用；一个消息事件
  `SUCCESS` 不能证明其他订阅也会 ACK，更不能证明 Runtime 或出站终态。
- 飞书 SDK 枚举必须在 transport normalize 边界确定性映射为内部枚举；例如真实私聊 `p2p` 只能映射为
  canonical `private`，不能要求 SDK 发内部值。回归必须把真实 SDK payload 连续送过 dispatcher、normalize、
  Channel 和 inbound bus，禁止从 normalize 后手造 `private/group` fixture 代替跨边界验证。
- 飞书群 mention 只接受 SDK 结构化 mention 中准确 bot `open_id`；bot name 和纯文本 `@name` 只能用于
  展示清理，不能授权。`group_policy=open` 也不能绕过 exact sender principal。
- 飞书事件顺序固定为 sender/bot reject、exact principal、mention policy、message-id dedup、外部资源、
  安全落盘、reaction、publish；未授权和重复事件的下载、reaction、Runtime 次数都必须为零。
- SDK WebSocket 没有真环境验证的 public stop API 时必须隔离到可终止子进程并回传 fatal/completion；
  设置本地 running flag、daemon thread 或 mock join 不能作为 deadline shutdown 证据。
- Gateway CLI 必须把 `SIGINT`/`SIGTERM` 转成 Runtime service task 的受控取消，使既有 absolute-deadline
  shutdown 路径实际 stop/join WebSocket 子进程；只验证主进程退出不算关闭。进程级回归必须逐个断言主进程、
  worker 和其出站 socket 都已消失，防止孤儿 channel 继续接收同一 app 的事件。
- logger filter 不能只挂在依赖父 logger 上并假设 propagation 会执行；依赖 SDK 与应用 logger 的 typed
  字段和自由文本按 candidate 2 保持原值。结构化 API audit 仍只包含其既有 allowlist 字段，但字段值不改写。
- reply/receive target 只来自认证 SDK envelope 的 immutable `ChannelDeliveryContext`；renderer metadata、
  chat id 前缀和模型参数不能覆盖。群 create/rename 的 member/chat target 同样只从可信 route 派生。
- Tool image/attachment 可在 dispatch 后写入 tenant/session/run ACL store，但 artifact 投影不得改写
  canonical result 或 provider-facing content；模型消息保留所需媒体，公共事件只携带 opaque ref，每个
  媒体独立进入不可 coalesce/evict 的 MEDIA outbound。必须在同一集成测试中分别断言模型图片和 Gateway ref。

- Outbound 消息在 egress 消费时必须再次核对 session generation 与 authoritative identity；只在 producer
  入队时检查不能阻止 reconnect 后发送已排队旧消息。
- Channel 必须先完成 exact principal/authentication，再进行任何外部 attachment 查询、下载或落盘；未授权
  sender 的资源调用次数必须为零。
- `RunResult` terminal final 是唯一远程 final；public event 只能补充非 terminal progress。测试必须按完整
  outbound 序列断言没有 duplicate final。
- Gateway supervisor 必须观察 channel、ingress、egress、public-event 全部 service task；任一真实异常
  fail-fast。shutdown 先拒绝新输入并保留 egress drain，再 stop channel；主动取消不得伪装成故障。
- Gateway shutdown 使用一个 absolute deadline 覆盖 active worker cancel/join、bus drain、channel stop 和
  service task join；对抗取消 task 用 `asyncio.wait` 硬上限，禁止用会等待 cancellation 完成的
  `wait_for` 冒充总 deadline。未全部完成只返回 false，不能提前标 close complete。
- Public progress 的 Gateway generation 必须在 RunRequest/event 生产时固化到 RuntimeEvent；消费 backlog
  时只核对事件携带的 generation 与 current generation，禁止读取 current 值后给旧事件重新贴标签。
- 公共投影没有用户语义内容时必须丢弃事件，禁止用内部 event type 作为 fallback 文本。usage、thinking
  和 raw tool lifecycle 只留内部 JSONL；具身动作进度必须与其后 observe MEDIA 用源 tool-call ID 关联。
- 任何依赖 `backend_attempted` 建立后续协议的工具适配层，都必须把该 machine-only 字段保留到 canonical
  `ToolResultMessage.data`，并在真实 serialized envelope 上测试；模型可见正文是否包含该字段不影响机器协议。
- 所有 progress、final、error、cancel 文本必须经过同一 public projection；projection 负责事件类型与字段
  allowlist、ownership、generation 和 artifact 边界，不改写已选中的文本值。

## Tenant 与外部资源边界纪律

- ACL、quota、artifact 和 connection ownership 必须使用 typed `tenant_id`，不得用 principal
  `subject_id` 代替；回归 fixture 必须让两个值不同，并从真实 tenant partition 验证读写。
- 外部 resource URI 必须分别审计 discovery、read payload、model result、public event 和 audit。经过 MCP
  权限及 tenant resource ownership 的文本 URI/content 在 typed result 中保持原值；二进制及仅用于存储或
  transport 的路径继续使用 ACL artifact/opaque reference，public event 仍受字段 allowlist 约束。
- Audit/trace sink 是可观测旁路，其失败必须 typed 留存并与业务生命周期隔离。用多实例 cleanup 测试
  逐个断言关闭，禁止 sink 异常改变连接状态、留下 connection 或中止后续清理。
- Authoritative device event 必须先提交到控制面 store，再 best-effort 镜像到 audit sink；审计失败不得
  从 append 抛出。lease acquire/release 与 emergency-stop 用例必须注入 sink failure，并分别断言 lease
  归零、后端 stop 实际调用和 typed audit failure。
- 外部工具没有经真环境核对的 read-only contract 时一律按 mutating fail closed。连接后已经尝试的
  timeout/call failure 若无法证明外部未变更，必须返回 `outcome_unknown` 且禁止自动重试；外部
  annotation 存在但未验证时继续标 `UNVERIFIED`。
- 去重必须区分处理中占位与成功完成记录：副作用前 reserve，只有权威 publish/commit 成功才保留；
  解析、下载、落盘、队列拒绝、异常或取消都 rollback，并测试同 ID 失败后重投成功、成功后重投拒绝。

## 测试工作区纪律

- 已通过 typed schema 和 evidence 校验的结构化记录进入 LLM pipeline 后，类型、identity 和完整 record 仍以
  调用方 typed payload 为唯一真理源；必须在 LLM 抽取输出后确定性投影并逐条 raw readback，禁止只靠 prompt
  要求模型保持 fact/procedure 类型。语义近似不得触发跨 identity 合并；连续写入两个相近但不同 identity 的
  真环境用例，分别断言准确 ID 和原始 record，单个成功样本不能证明类型门可靠。

- 浏览器 actionability 的 obscured 判断必须在目标滚入 viewport 后基于刷新状态执行；inspect
  时的首屏外坐标不能直接作为动作拒绝依据。回归必须用真实首屏外控件完成一次点击或回填并读取
  DOM 终态，不能只断言 inspect 返回目标。
- verifier 不得把 executor 失败认证成成功，也不得用 verifier 自身异常覆盖原始工具错误。没有
  成功 backend receipt 时保留 executor 的 status/error/outcome certainty；测试必须从最终模型可见
  ToolResult 断言原始错误码，而不是只直接调用 executor。

- 迁移完成态不得只信任 journal/manifest 的 identity/status；逐组件核对锁定 source/target、publication、
  digest schema，以及所有已发布目标的存在和结构。发布时在适用的旧 store 锁或一致性 snapshot 下复制，并在
  atomic publish 前重读源；源变化必须 fail closed。发布摘要只证明迁移瞬间一致，活跃数据正常变更后不得用
  陈旧摘要冒充永久内容完整性。
- 声明只读的 doctor/inspect 必须用完整文件树前后对比验零写入，ready、migration-required、cold-cache 都要
  覆盖。不得从诊断路径启动 store、创建数据库、物化 cache 或为了 import check 执行有全局副作用的第三方包；
  真正 backend 可用性只由显式启动或独立 mutating probe 验证。

- 第三方库若在 import 时读取环境变量、注册退出钩子或创建全局资源，文件定位、版本检查、doctor 和完整性
  preflight 不得提前 import 该库。先设置全部进程级开关，再从唯一业务边界 import；纯文件校验用 spec/resource
  定位，并回归断言 `sys.modules` 未被污染。隔离 HOME 后通过只能证明共享用户目录参与了故障，不能替代真实
  并发进程下的组合测试。

- 非 PyPI 依赖必须通过标准 PEP 508 direct reference 写入项目依赖和构建后的 wheel `Requires-Dist`；不得只依赖
  `[tool.uv.sources]` 等源码仓库私有映射。每次变更都从源码外只拿 wheel 建立空 venv，先检查 wheel 元数据包含
  准确 URL，再让安装器真实解析全部依赖并 import 目标包；源码环境的 `uv sync` 成功不能替代发布包安装门。
- 运行必需的离线模型/词表工件不得依赖 `/tmp`、用户级默认 cache 或首次联网下载。把锁定工件作为
  package data 分发，显式 materialize 到可配置的持久项目/部署目录，并让所有调用链使用同一个 cache path。
  每次变更必须在空 cache、断网条件下分别验证源码和已安装 wheel 的真实加载与外部功能终态。
- 同一模型能力只允许一个公开工具名，名称必须表达模型要执行的动作。重命名时同步所有 Profile、上下文索引、
  投影、安装门和文档，并断言旧名称不再出现在模型工具列表；不得用并列暴露同义工具维持兼容。
- 工具输入校验失败只报告工具边界可观测事实：稳定错误码、工具名、收到的参数键、缺失字段、逐项问题和
  `backend_attempted=false`。禁止在工具反馈中猜测 Provider/文本格式来源、解析模型叙述、推荐替代工具或
  注入重试提示；错误文本与结构化 metadata 必须同源，并测试 backend/lease 调用次数为零。
- 需要搜索能力时，模型可选择完整命令入口 `terminal` 或结构化入口 `search_files`。结构化搜索可以在
  Runtime 选择环境程序，但必须通过真实子进程监督层执行；schema 中的 timeout 必须约束实际墙钟并清理
  整个进程组。禁止在 Agent 主进程内同步遍历大目录后返回一条未生效的 timeout 字段，也禁止 fallback
  偷改模型请求的搜索范围或语义；实际程序、返回码、耗时和 truncation 必须进入结构化结果。
- 跨平台工具不得用 `os.name == "posix"` 推导 GNU/BSD 命令行兼容性。对 Linux 与 macOS 分别锁定最终
  argv，并在对应真机执行至少一个成功命令和一个非零返回码命令；移植 OpenHarness 工具时同步其平台
  分支及上游测试，禁止只复制主执行路径后另写简化判断。
- Service-backed tool 必须通过真实 `ApplicationRuntime` dispatch 边界测试；直接调用 executor 或 pipeline
  不能证明 composition 注入和 session runtime 接线成立。
- Parser、stop condition 和 resume 逻辑必须断言组件间实际序列化 envelope；禁止按 executor 的中间 dict
  猜测 canonical message 形状。
- 工具返回中供模型继续决策的 ID、records、状态和错误细节必须进入 provider 实际序列化的 tool-result
  `content`；只写内部 `data`/metadata/event 不算模型可见。回归必须在下一次真实 transport 请求边界解析并
  断言这些字段，禁止用 executor result 或 runtime trace 代替。
- canonical `ToolExecutionResult` 穿过通用迁移 adapter 时必须保留原始模型投影，直到 application 统一生成
  `ToolResultMessage`；不得先压扁成 `output + metadata` 再让 provider 只发送 output。兼容用的扁平 metadata
  可以保留，但不能替代 canonical provider content；同时回归成功、非零返回码和媒体结果。
- 结构化记忆 mutation 必须以完整 typed record 为唯一真理源，展示 content 只由该 record 确定性生成；终态
  同时回读比较 content、完整 record、版本状态和 lineage，禁止正文更新但 `record_json` 沿用旧值。
- Runtime 能按 tenant/session/run/turn 和来源确定的 provenance 不得交给模型搬运 opaque ref；从公开 schema、
  provider messages 和 tool result 中移除该 ref，并在 executor 内按当前 scope 选择。回归必须负向扫描最终
  provider 请求，并用旧 run ref 无法影响新 mutation 的用例证明隔离。
- 协议确实要求模型下一轮回传的 opaque receipt/handle 必须进入具体 Provider transport 最终发送的
  tool-result `content`，并只披露格式严格验证过的最小 token；fake transport 直接读取
  `ToolResultMessage.data` 不算证据。测试必须从 content 解析该 token 完成一次真实后续 mutation，同时
  负向断言内部 objectId、containment、pose、hash 和 trace 未随 token 外泄。
- Package-data 功能必须构建并安装 wheel，再逐项枚举资源；源码 checkout 的 import/resource 测试不能
  证明发布包完整。
- 删除 package 或 package data 后，从空 `build/dist/egg-info` 构建并枚举 wheel ZIP；setuptools 增量构建
  不会自动清除旧 `build/lib`，源码已删除和构建成功都不能证明 wheel 未携带陈旧包。
- 默认 profile 的 wheel 门必须安装 wheel 声明的核心依赖，并在源码 checkout 外真实 import、构造 profile、
  逐项核对默认工具；只用 `--no-deps` 枚举 package data 会掩盖默认工具缺失运行依赖和 optional extra
  的 eager import。
- Spawned worker 必须通过 argv 或等价 typed contract 显式接收父应用 authoritative config path；只透传
  model、依赖 ambient cwd 或重新加载默认配置都不算配置复用。
- 上游兼容移植必须保留锁定 commit 的原始 fixture/test，并增加一条未经改写的真实上游格式黑盒门；
  Home 自造等价 fixture 与同源 parser 单测不能证明兼容。
- 检查/截图/read 工具只能提供模型确认信息，不能成为无关动作的 authorization、freshness 或 completion
  状态机。多模态 tool result 必须在实际 provider request 边界断言模型可见 block 的类型与数量；内部 trace
  或 image hash 不能替代该断言。
- monkeypatch、fake 和 callback 收到相对路径时，必须显式锚定 `tmp_path` 或 fixture root，禁止默认相对
  当前仓库目录写文件。测试 gate 后、final review 前检查新增 untracked 文件；pytest 通过不证明测试无
  工作区副作用。

## 设备连接与租约纪律

- Factory 创建 application-owned pool 后，必须用 factory-to-runtime 测试证明真实
  `RunRequest.environment` 在 provider/backend 前进入 pool；只断言空 pool 存在不算接线。
- 无声明 identity 的 borrowed backend 必须在首次 run 绑定 authoritative tenant；同一 physical backend
  的跨 tenant 重绑 fail closed。ACL/lease key 不得从每次调用者临时合成第二套 owner。
- disconnect、重复 disconnect、stop、uncertain 和 close 必须从唯一 lease owner 原子取得下一
  generation，并在 terminal transition 时逐 waiter 拒绝。不得从 immutable registration generation
  每次重算 `+1`。
- Lease future 获准后、进入 backend 前必须在 registry lock 内复核 active lease、generation 和 READY
  state；动作结束后的核对只能判定 `outcome_unknown`，不能阻止 stop 后新动作已经启动。
- 机器人 adapter 必须把 control 返回和独立状态查询分别规范化为内部 typed receipt，并保留两次
  return code。外部 SDK enum/字符串在真环境核对前标 `UNVERIFIED`，raw `success/stopped` 不得背书。

## Extension 权限与生命周期纪律

- Hook 的 event/matcher 选择与 principal authorization 必须分开。未授权 callback 不执行，但必须产生
  typed denied result；`block_on_failure` hook 的 capability 缺失必须阻断当前 run，禁止静默跳过。
- Plugin tool 必须声明非空 canonical `required_capabilities`。加载时核对 manifest requested 与 deployment
  grants，调用时逐项核对 run principal；Catalog 注册、profile enable 或 exact CLI/Gateway metadata 都
  不得替代任何一层授权。
- 可信 in-process callback timeout/cancel 只宣称 cooperative cancellation 与 result fencing，不宣称撤销
  任意副作用或 hostile-code sandbox。Hook 不得成为 permission、device safety、terminal、verifier 或
  scorer 的唯一 owner。
- 不要用 `asyncio.wait_for(callback())` 证明抗取消 callback 已停止；单独建 task，deadline 到点立即 fence
  result，task 实际结束前持续计入 active。Reload/stop/cleanup 必须以真实 active task 为门，close 先
  seal、cancel、join，再执行 stop hook 和 cleanup。
- Extension reload 只允许 hooks-only candidate；tool/provenance/capability/profile 任一变化都要求 restart。
  extension id/version/requested/granted capability 也属于 restart boundary。活动 callback 存在时拒绝 swap，
  candidate 全量验证通过前不得修改 Catalog 或当前 generation。
- Exact tool/hook token 只能表达目标选择，不能替代 plugin/hook 的 canonical `required_capabilities`。
  request override 用显式 sentinel 区分“未提供”与“空集合”，并在任何 lifecycle hook 前完成 subset 校验。
- 受批准文件的 containment 必须固定 root directory fd，并逐级 `openat`/`O_NOFOLLOW`；只检查 resolved path
  或最后一个文件分量挡不住父目录 symlink TOCTOU。Partial load、candidate rejection 和 Catalog collision
  都必须按逆序释放已经取得的 cleanup ownership。
- Extension content digest 必须覆盖所有可改变声明行为的 entrypoint/dependency bytes，并从这些已验证
  bytes 执行；真实 `__file__` 不得给动态 adjacent-file loader。显式 dependency 方案仍是 trusted-code
  内容锁定，不得包装成 hostile-code sandbox。
- Factory 返回 contributions 的那一刻就建立 rollback owner；async candidate failure 必须 await cleanup
  后再返回，composition 则持有 owner 直到 ApplicationRuntime 接管。禁止用 fire-and-forget cleanup 填补
  构建阶段之间的所有权窗口。
- Application/run lifecycle 分开计数：application start/stop 各一次，run start/end 每 run 各一次；失败、
  blocking 和 cancel 都必须进入 best-effort run end。Application stop 后、普通 resources 关闭前执行
  extension cleanup，并把 hook/cleanup 状态写入字段受限、文本精确的结构化 trace。

## Provider 外部门纪律

- 同一 update surface 覆盖文本与 Schema 记忆时，必须先读取旧 raw record 再按权威结构字段分流：结构字段
  不存在才允许文本原地更新，存在且合法则同步正文、完整结构 metadata、向量和图关系，存在但损坏必须
  fail closed。禁止用 content-only update 修改含结构副本的记录，也禁止为已经完整校验的 replacement record
  重跑提取型 Add pipeline。声称保留版本必须真实写入 lineage，并提供从图关系到 raw 终态的查询验收。

- 外部 schema pipeline 的 metadata 必须按真实 raw record 结构读取，不能假定请求 metadata 原样位于顶层。
  原生 pipeline 若会按语言或阶段重建 prompt，必须在真实调用点验证显式 prompt 仍生效；构造时注入成功不等于
  执行时使用。结构化写入验收必须从返回候选 ID 回读 raw memory，并逐个断言目标类型、完整 record 和 CRUD
  可复用 ID，episodic fallback、事件日志或成功返回码都不能替代。

- Anthropic流式响应的实时 delta只用于展示 text/thinking；可执行工具名与参数必须以 SDK最终消息中的
  `tool_use.name/input`为唯一真理源。不得同时维护第二套生产参数聚合器，也不得解析正文 XML样式工具标记执行。
- 流式失败后的重试门必须按 delta 的可见性与副作用语义判断，不能用 delta 列表是否非空代替。只有隐藏
  reasoning 且没有正文、工具、完成信息或 commit 时才可丢弃并重试冻结请求；异常文本为空时沿异常链提取，
  最后用具体异常类型名兜底。
- Provider已被真机证明会把声明为object的嵌套参数编码成 JSON字符串时，只能在typed输入边界解析为object，
  随后继续执行完整原始模型校验；禁止以兼容为名接受任意字符串、数组或跳过discriminator/字段约束。

- doctor/health check 的 PASS/WARN/FAIL 必须与真实故障域一致：可选子系统 unavailable 且调用边界已
  fail closed 时报告 WARN 和具体影响，不得用全局 FAIL 误杀仍可工作的顶层入口；同时保留真实 unavailable
  工具结果与独立健康诊断测试。

- 外部库声称 hybrid/多分支能力时，逐分支从真实运行时入口证明调用和终态贡献；源码存在 helper、collection
  存在 sparse slot、写入含 named vector 或一个聚合查询能命中，都不能证明公开 search 路径实际消费每个分支。
  为每个分支保留独立来源标签与对抗 fixture，禁止用笼统 `hybrid` 标签掩盖 dense-only 退化。

- 用真实顶层 consumer 的 pre-completion first-byte 黑盒门证明实时输出：让 fake
  provider 在首 delta 后阻塞，并断言 CLI 已输出且仍未退出；provider-level
  streaming 不能作为 UI streaming 的替代证据。

- Provider 接口从同步迁移为异步后，逐条审计所有 live gate 并直接 `await` 真实入口；验收必须拒绝
  coroutine/awaitable 逃逸和 `never awaited` warning。只看到测试启动或 fixture 成功不能证明请求已发出，
  必须同时断言真实响应终态和命令返回码。

## 配置合并纪律

- 把 file/env/CLI 值写入 Pydantic 配置后必须重新执行 `model_validate()`；不得用
  `model_copy(update=...)` 假定 validator 会重跑。对规范化、enum、URL 和认证类型各保留至少一条
  override 回归，并断言最终值与 provenance。
- 真实配置必须保持 gitignored mode-0600，同时提交字段完整、只含占位值的 `.example`。doctor、dry-run、
  config tool、异常、日志和事件继续使用各自 typed schema；schema 已选择的配置值与来源标签保持原值，
  包括 `SecretStr`、URL userinfo/query 和路径。
- 用户、模型或事件可见的配置展示不得绕过现有 registered tool permission/capability、事件 allowlist 或
  ownership 边界。通过这些边界后禁止对 authoritative config 的已选字段做内容脱敏或替换；回归必须
  分别检查工具返回、模型消息和实际 JSONL 落盘。
- dry-run/config 报告的运行预算必须显式接入 one-shot、interactive、Gateway 等每个实际入口的
  `RunPolicy`；入口测试必须断言最终 request 值，不能用配置解析正确替代执行接线。
- installed CLI 的默认配置路径必须可由显式部署值覆盖；doctor/report 不能假定配置位于源码
  `REPO_ROOT`。wheel 黑盒必须从空 cwd 使用外部配置路径运行，禁止借 pytest `pythonpath=src` 假绿。
- Cron、配置、MCP 管理和 task/agent/team 等管理工具必须声明并检查各自独立 capability；通用
  `tool.mutate`、Catalog 注册或 profile enable 不能替代 `scheduler.manage`、`config.mutate`、
  `mcp.manage` 或 `process.spawn`。
- data-only Plugin Skill 发现只能解析 manifest 与受 containment 保护的 `SKILL.md`，不得调用 executable
  plugin loader 或导入 Python/tools/hooks/MCP；project plugin 默认关闭，builtin 覆盖仍需精确授权。

## Coworker 外部编排纪律

- Coworker 必须显式区分 generic application `run_id` 与 Case02 environment domain `run_id`。所有发往
  `EnvironmentClient` 的 state/reserve/browser/terminal/decision/runtime-event 调用只可使用依赖中绑定的
  `coworker_domain_run_id`；回归 fixture 必须令两者不同，并逐项断言每次环境调用使用 domain ID。
- 构建 Coworker 候选环境时必须安装 `coworker` optional extra；service Python 与 runner Python 分别
  核对。Service preflight PASS 后，仍要用实际 runner Python import Playwright、启动
  `sync_playwright()` 并核对配置的 Chrome executable，禁止用另一个 venv 的可用性替代。
- 成功的 `task_planner` / `task_progress_check` 必须安全投影出合法 plan；无法投影时记录 presentation failure，禁止发布无 plan 的 succeeded 事件。独立 verifier 必须逐个成功 Planner 结果检查 plan 存在，不能只验证已有 plan 的归属。
- 真实 provider 验收必须按 iteration 逐实例核对：连续非负编号、每轮 request/response 各一次、集合一致、request 先于成功 response、工具选择不早于成功 response。至少一个 request/response 只能证明 provider 曾被调用，不能证明完整轨迹由真实模型执行。
- 修改 run 生命周期 helper、attempt manifest 或 CLI 异常传播后，必须用与真实入口完全相同的参数跑一次顶层 shell smoke，并断言 run root 实际创建、失败/成功路径都打印该路径；helper 单测不能替代入口验收，形参与落盘字段不得同名冲突。
- 正式成功只能在必需 artifact 全部登记后计算，并逐项验证存在、`complete=true` 和当前字节哈希；manifest 未列出的必需项也必须失败，禁止把 `artifact_failure` 写成常量或只遍历已有条目。
- 每个 action 消费、runtime/task/skill 写入和外部调用都必须先检查共享终态；decision 引用只能指向当前 run 已持久化的先前 evidence，伪造、跨 run 或终态后引用必须在写审计前拒绝。
- 配置中的 venv Python 必须保留 venv symlink 路径；只转成绝对路径，不得用 `Path.resolve()` 解引用到基础解释器。启动子服务后必须从该进程验证依赖 import 和 health，不能用父进程 import 成功代替。
- DAG 必需的 planner、progress、SOP decision 和 exact-job wait 必须在外部后继动作或审计写入前由环境端验证。模型叙述、TaskState 内部完成、最终业务状态或事后再次调用不能替代正确顺序，也不得合成或倒填轨迹节点。
- `browser_wait` 必须绑定当前 run 最新提交返回的准确 job ID；终端执行前要求 add/remove wait，normal progress 前要求五项 postcheck 与 business wait，rollback progress 前要求 remove wait 和 absence grep。

## 外部录制终态纪律

- 对停止、提交等超时后可能已经在外部完成的副作用请求，按操作最坏耗时设置专用客户端超时，并在服务端用锁与完成结果缓存提供幂等重试；不得在不查询或缓存外部终态时重复触发一次性进程输入。
- 命名事件帧必须证明画面语义对应 source event：取帧 offset 必须严格早于下一条 presentation event，并逐帧核对 exact tool/failure code/中文原因/恢复状态；source ID、offset 公式、非空像素和区域方差都不能单独作为语义正确门。
- 独立 bundle verifier 不得信任产品写入的 `verified` 或帧统计；必须重新核对 FFmpeg 退出与 first-packet 落盘证据，独立运行 ffprobe，并从交付视频重新解码首中末帧计算内容门。
- 把“编码器处理了帧”和“视频 packet 已写入外部文件”分开。first-packet 门必须同时看到 FFmpeg progress 的有效帧/字节状态，以及输出文件在非零 header 之后至少一次可观测的正向增长；仅有 `frame`、`total_size > 0`、进程存活或非空文件一律不得放行模型调用。
- 录制最终成功必须另行核对 FFmpeg 正常退出码、独立 ffprobe 的 codec/尺寸/pixel format/时长/帧数，以及首中末每一帧预期区域的非黑屏与内容变化。不得用正常收尾后的可播放结果倒推录制开始时 readiness 已成立。

## ALFWorld 外部执行纪律

- 把“已发出动作”和“外部世界已完成动作”分开。任何 THOR 功能都必须同时通过返回状态门和独立外部终态黑盒门；mock、内部 result、trace 或模型反馈不能代替外部终态。
- 导航成功必须由同一个返回 event 证明：外部返回成功、requested/actual pose 一致、准确 objectId 的 `metadata.visible=true`、准确 objectId 的正面积 bbox，以及交付图片与该 event 的 RGB 像素一致。
- Put 成功必须证明：外部返回成功、准确对象离开完整 inventory、`isPickedUp=false`、准确目标在对象 parent membership 中、准确对象在目标 child membership 中。返回与终态矛盾或读取缺失时立即停止为不确定，不得重试。
- 携带物移动成功时，只要求准确 held ID、完整 inventory 不变、准确对象仍存在、`isPickedUp=true` 和实际 pose 匹配。不得要求 `parentReceptacles` 或 `receptacleObjectIds` 不变；THOR 会在携带物经过 receptacle 时更新这些字段。
- 移动失败后，只有完整动作状态和实际 pose 都与动作前一致才可继续。Put 失败后，只有完整动作状态不变才可继续；完整状态至少包含 held ID、完整 inventory、准确对象 parent tuple、准确目标 child tuple、对象存在与 `isPickedUp`。
- 每个发给 THOR 的请求都计一个 backend action，包括 `GetReachablePositions` 等 query。请求前检查候选数、backend action 数和 wall-clock 三预算，预算到达后不得再发 N+1 请求。
- 从确定性 scene snapshot 只解析一次准确对象和目标；显式实例 miss 不得类型级 fallback。候选集合、顺序和 hash 在 context 创建时锁定，重试期间不得重新解析或重算目标。
- 真环境验收按 target/instance 独立 reset、独立断言，禁止用 best/any 或全局聚合掩盖失败。更换 ALFWorld、ai2thor 或 Unity 运行时版本后，重新执行 runtime contract 与逐实例 characterization。
- Helper 自测必须审计真实 CLI/handler 接线；新 validator 存在或 isolated fixture 通过不算接线完成。case/run verifier 必须逐 case 回读 raw setup artifacts 并独立重算，禁止只信 worker 自报计数。
- 共享 schema 迁移时，把同一份 committed payload 直接喂给每个真实 consumer；禁止在 synthetic fixture 中补回生产 payload 已删除的字段。目标 mutation 必须核对具体拒绝原因，不能把其他缺字段异常算 PASS。
- 跨 producer/consumer 的 synthetic shared-schema fixture 必须由真实 producer 生成，或从 producer 的实际 coverage rule 重算并逐 ID 审计；禁止手工补出真实生产路径不会发布的行或字段。
- 模型已持有的数据不要再暴露同义读取入口：当 profile、memory 或其他状态已经进入当前上下文时，模型工具 schema 不得同时提供普通召回用的 read/list 动作。禁止性描述不能抵消 enum 中实际存在的能力；保留底层程序读接口供快照构建、审计和写后终态核验，并用真实事件断言无需工具的问答首轮 `tool_calls=[]`。
- 第三方高层 API 若隐式组合多个外部分支，先沿锁定 wheel 的真实调用栈列出每个外部调用，再决定是否在应用层补分支。默认优先保留公开 API 并删除重复的应用层分支；验收逐分支计数，结果带多个来源标签不能证明每个外部检索只执行了一次。
- 外部 transaction 完成后的下游派生、序列化或汇总失败，仍必须保留已完成的 raw refs、逐动作 rows、返回码和真实 action count；不得退化成零计数最小错误。
- 在仓库外临时目录运行 Ruff 等项目工具时，显式传入仓库真配置；临时默认配置的 PASS 不得作为项目门。同步后必须在正式仓库路径复跑。
- 多命令验收脚本必须 fail-fast 或逐命令断言返回码；禁止用最后一条成功命令的 exit 0 掩盖前序 lint、format、测试或外部验证失败。
- Gate-only missing/sentinel ID 必须在任何真实 object/snapshot/oracle lookup 前分流：闭式验证其规范名称、冻结序号、真实集合中不存在和全部派生 binding；普通 ID 不得借此绕过真实 authority。
- 真实 Gate 失败修复后，先用修复字节离线重放该失败 run 的完整不可变 raw artifact；synthetic 自测转绿不能替代这条正交回归。
- 缺失错误和 terminal 分类必须基于最终锁定的 target/anchor/context 状态：先执行全部高优先级正常解析（如 direct snapshot 覆盖 parent anchor），再判断失败；禁止把中间候选缺失提前永久写入 issues。
- 不得用 containment/parent 关系或动作意图推导相机可见性。关系从动作前 raw state 独立重算，授权只取该动作精确返回 event 的 `visible=true` 与正 bbox；把所有允许 outcome 预先闭式冻结，再由返回 event 选择，independent verifier 必须用 raw artifacts 重新选择并覆盖每个 outcome。
