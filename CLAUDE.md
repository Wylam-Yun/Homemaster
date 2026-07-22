# HomeMaster Agent Rules

## Gateway 远程边界纪律

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
- 所有 progress、final、error、cancel 文本必须经过同一 public projection；自由文本也要脱敏配置 secret、
  credential assignment、URL query 和宿主路径，不能只依赖 metadata key redaction。

## Tenant 与外部资源边界纪律

- ACL、quota、artifact 和 connection ownership 必须使用 typed `tenant_id`，不得用 principal
  `subject_id` 代替；回归 fixture 必须让两个值不同，并从真实 tenant partition 验证读写。
- 外部 resource URI 必须分别审计 discovery、read payload、model preview 和 audit。ACL raw artifact
  可以保真，模型只见 opaque resource id，audit 只见不可逆 hash；query token、userinfo 和本地路径
  不得进入 preview、事件或 audit。
- Audit/trace sink 是可观测旁路，其失败必须 typed 留存并与业务生命周期隔离。用多实例 cleanup 测试
  逐个断言关闭，禁止 sink 异常改变连接状态、留下 connection 或中止后续清理。
- Authoritative device event 必须先提交到控制面 store，再 best-effort 镜像到 audit sink；审计失败不得
  从 append 抛出。lease acquire/release 与 emergency-stop 用例必须注入 sink failure，并分别断言 lease
  归零、后端 stop 实际调用和 typed audit failure。
- 外部工具没有经真环境核对的 read-only contract 时一律按 mutating fail closed。连接后已经尝试的
  timeout/call failure 若无法证明外部未变更，必须返回 `outcome_unknown` 且禁止自动重试；外部
  annotation 存在但未验证时继续标 `UNVERIFIED`。

## 测试工作区纪律

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
  extension cleanup，并把 hook/cleanup 状态写入脱敏结构化 trace。

## Provider 外部门纪律

- Provider 接口从同步迁移为异步后，逐条审计所有 live gate 并直接 `await` 真实入口；验收必须拒绝
  coroutine/awaitable 逃逸和 `never awaited` warning。只看到测试启动或 fixture 成功不能证明请求已发出，
  必须同时断言真实响应终态和命令返回码。

## 配置合并纪律

- 把 file/env/CLI 值写入 Pydantic 配置后必须重新执行 `model_validate()`；不得用
  `model_copy(update=...)` 假定 validator 会重跑。对规范化、enum、URL 和认证类型各保留至少一条
  override 回归，并断言最终值与 provenance。
- 真实配置必须保持 gitignored mode-0600，同时提交字段完整、只含占位值的 `.example`。doctor、
  dry-run、异常、日志和事件只能输出递归脱敏后的值与 `default/file/env/cli` 来源标签。

## Coworker 外部编排纪律

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
- 外部 transaction 完成后的下游派生、序列化或汇总失败，仍必须保留已完成的 raw refs、逐动作 rows、返回码和真实 action count；不得退化成零计数最小错误。
- 在仓库外临时目录运行 Ruff 等项目工具时，显式传入仓库真配置；临时默认配置的 PASS 不得作为项目门。同步后必须在正式仓库路径复跑。
- 多命令验收脚本必须 fail-fast 或逐命令断言返回码；禁止用最后一条成功命令的 exit 0 掩盖前序 lint、format、测试或外部验证失败。
- Gate-only missing/sentinel ID 必须在任何真实 object/snapshot/oracle lookup 前分流：闭式验证其规范名称、冻结序号、真实集合中不存在和全部派生 binding；普通 ID 不得借此绕过真实 authority。
- 真实 Gate 失败修复后，先用修复字节离线重放该失败 run 的完整不可变 raw artifact；synthetic 自测转绿不能替代这条正交回归。
- 缺失错误和 terminal 分类必须基于最终锁定的 target/anchor/context 状态：先执行全部高优先级正常解析（如 direct snapshot 覆盖 parent anchor），再判断失败；禁止把中间候选缺失提前永久写入 issues。
- 不得用 containment/parent 关系或动作意图推导相机可见性。关系从动作前 raw state 独立重算，授权只取该动作精确返回 event 的 `visible=true` 与正 bbox；把所有允许 outcome 预先闭式冻结，再由返回 event 选择，independent verifier 必须用 raw artifacts 重新选择并覆盖每个 outcome。
