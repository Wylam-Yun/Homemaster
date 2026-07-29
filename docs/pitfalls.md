# Engineering Pitfalls

## 2026-07-29 - 记忆证据只在内部 message.data，真实 Provider 无法自主写入记忆

### 症状与根因

ApplicationRuntime 测试显示 observation 后产生了 `memory-evidence-*`，fake transport 也能从
`ToolResultMessage.data` 读取并调用 `add_memory`，看起来环境事实记忆链路已经完整。但真实 Anthropic
transport 只序列化 tool-result `content` 和图片，完全不发送 `data`；模型既看不到 evidence ref，又被
`add_memory` schema 要求必须提供 evidence ref，因此结构化记忆虽然“工具已注册”，实际无法写入。
原测试直接读取 Runtime 内部 message 对象的 `data`，绕过了真实 Provider 序列化边界，形成假阳性。

### 修法与教训

环境工具成功后仍由 evidence ledger 注册 run-bound opaque ref，但只把格式严格验证过的
`memory-evidence-<32 hex>` 加入实际模型可见 tool-result content；objectId、containment、pose、hash
和内部 trace 保持隐藏。回归从 tool-result content 解析 ref，再完成真实 `add_memory`，禁止从
message.data 取值。凡是要求模型在下一轮原样回传的 token、ID 或 evidence ref，都必须在具体 Provider
transport 的最终请求 payload 中断言，而不是依赖 Runtime 内部 message 形状。

### 参考

- `src/homemaster/application/tool_executor.py`
- `src/homemaster/providers/transports/anthropic.py`
- `tests/homemaster/application/test_application_runtime.py`

## 2026-07-29 - 飞书看到动作图片，但观察屏障没有真正建立

### 症状与根因

真实 ALFWorld 已完成导航、拿取和放置，飞书图片也显示外部状态变化，但 trace 中没有
`model_observation.barrier_set`。工具层把强类型 `AlfworldStepResult` 转成旧式
`ToolResultMessage` 时只保留模型可见反馈，丢失了 machine-only 的
`backend_attempted=true`；图片和动作成功制造了“功能已完成”的假象。同时 Gateway 把空内容公共事件
回退成 event type，导致用户看到 `usage.update`、`tool.call_completed` 等内部噪声。

### 修法与教训

在 canonical tool result 的 `data` 中保留 backend 尝试位，模型正文仍只含安全反馈；用真实动作后的
barrier/observe/下一次 Provider image 序列作为门。公共投影对每种用户可见事件显式生成语义内容，
空内容直接丢弃，绝不回退为内部事件名；动作后的 observe artifact 携带源 tool-call correlation。
同样不得把 `backend_attempted=false` 的 observation protocol correction 描述成外部“操作失败”；
它只说明模型提前请求了下一动作，实际环境没有执行该请求。
外部状态变化、图片可见和内部协议生效是三个独立判据，不能互相替代。

### 参考

- `src/homemaster/benchmarking/alfworld/tools.py`
- `src/homemaster/agent/generic_runtime.py`
- `src/homemaster/events/public_projection.py`
- `tests/homemaster/application/test_model_observation_barrier.py`

## 2026-07-28 - ALFWorld full extra 在 Python 3.11 隔离构建失败

### 症状与根因

临时项目解析 `alfworld[full]` 时耗时数分钟后失败在 `visdom==0.2.4` 的构建后端：
构建环境缺少 `pkg_resources`。ALFWorld 本体、AI2-THOR 和视觉运行依赖并未发生解析冲突；
根因是 `full` extra 把训练、可视化等 Gateway 不使用的依赖一起带入，并将一个旧版
visdom 的构建问题误扩散到运行时安装。

### 修法与教训

按 Gateway 的真实 import/执行边界锁定上游 ALFWorld commit、`ai2thor==2.1.0`、
OpenCV、Torch 和 TorchVision 的最小集合，并先在独立 Python 3.11 临时项目执行
`uv lock`。Oracle controller 不执行 CUDA 推理，源码环境从 PyTorch 官方 CPU index
解析 Torch/TorchVision，避免无用的 NVIDIA wheel；公开 `Requires-Dist` 仍使用标准
版本约束。外部项目的“全功能 extra”不是更可靠的默认值；应先列出产品实际运行路径，
对最小依赖集合做 resolver 和真环境 import 双重验证，不能用裸 `pip install -U` 绕开失败。

### 参考

- `pyproject.toml`
- `uv.lock`
- `plan/V2.2/alfworld-gateway-observe-verification-implementation-plan-zh.md`

## 2026-07-28 - 迁移 manifest 只验身份，doctor 只读声明仍启动 backend

### 症状与根因

迁移完成后删除已发布的 `files/`，coordinator 仍因 manifest 的 schema/root/status 正确而返回 ready，随后
`FileMemoryStore.start()` 会创建空记忆；复制旧目录期间发生写入也不会阻止旧快照发布。与此同时，doctor 在
ready 分支实际启动 `Mem0MemoryStore`，会创建 Qdrant/history 和 BM25 cache；通用 import 检查还会在 vendored
字节校验前执行 `mem0`。根因是把“内部 receipt 存在”误当外部数据仍有效，并让诊断命令复用了有副作用的启动
路径。

### 修法与教训

完成态 manifest 必须验证 component schema、锁定路径、publication/digest 形状，以及所有已发布目标的存在和
结构；发布时继续对 source/staging/target 做同值校验。活跃记忆会正常更新，发布摘要不能永久绑定其内容哈希，
否则正常写入会在下次启动被误报为损坏。旧文件复制持有真实 `.memory.lock` 并在 publish 前重读 source；SQLite
使用 backup snapshot。doctor 只做文件级 vendor 校验、配置与 migration `inspect()`，不 import mem0、不打开
backend、不物化 cache。回归必须分别删除已发布目标、在 copy 后改变源，并对 ready/cold-cache doctor 比较完整
文件树前后状态。

### 参考

- `src/homemaster/memory/migration.py`
- `src/homemaster/cli/doctor.py`
- `tests/homemaster/memory/test_migration.py`
- `tests/homemaster/test_cli_doctor.py`

## 2026-07-28 - 完整性校验提前 import mem0，遥测开关失效并争抢全局 Qdrant

### 症状与根因

单个 mem0 store 测试通过，连同 vendor integrity 测试运行时却在后续实例随机失败，报告
`~/.mem0/migrations_qdrant` 已被另一个 Qdrant client 占用。完整性校验为定位包目录执行了 `import mem0`；
上游在 import 时冻结 `MEM0_TELEMETRY`，而 HomeMaster 直到 store 启动才设置环境变量。于是测试顺序改变了
进程语义，遥测 migration store 被启用并与真实 Gateway 争用固定用户目录。

### 修法与教训

vendor 校验改用 `importlib.util.find_spec()` 做纯文件定位并断言不会把 `mem0` 放入 `sys.modules`；禁用遥测后
才允许业务边界 import。第三方库若在 import 时读取环境或创建全局资源，任何 preflight、doctor 和 hash
校验都不得为了“找路径”提前 import；回归必须在真实并发进程存在时运行组合测试，单测隔离 HOME 只能用于
根因对照，不能作为修复。

### 参考

- `src/homemaster/memory/vendor_integrity.py`
- `src/homemaster/memory/mem0_store.py`
- `tests/homemaster/memory/test_vendor_integrity.py`

## 2026-07-28 - uv source映射未进入wheel元数据，源码可装但发布包依赖无解

### 症状与根因

项目源码环境中 `uv sync`、锁文件检查和全部 memory 测试都通过，但从源码外安装刚构建的 HomeMaster wheel 时，
解析器报告不存在 `en-core-web-sm==3.8.0`。该模型 wheel 不发布在 PyPI；`pyproject.toml` 只把版本写进
`project.dependencies`，真实下载 URL 放在 `[tool.uv.sources]`。后者只供 uv 读取源码项目时使用，不会进入构建
产物的 `Requires-Dist`，所以发布 wheel 丢失了唯一可解析来源。

### 修法与教训

把模型依赖写成标准 PEP 508 direct URL，使构建元数据和 lock 同时保存准确来源；删除只在源码侧生效的 source
映射。回归从源码外建立空 venv，检查 wheel `METADATA` 后让安装器真实解析依赖并 import 包。源码 checkout 中
依赖齐全、lock 正确或构建成功，都不能证明 wheel 自身可安装；非 PyPI 依赖必须审计最终 `Requires-Dist`。

### 参考

- `pyproject.toml`
- `uv.lock`
- `tests/homemaster/skills/test_installed_package.py`

## 2026-07-28 - mem0公开search已内置hybrid，外层又跑BM25导致重复检索和spaCy告警

### 症状与根因

每次 `search_memories` 都打印缺少 spaCy lemma/full model 的两条警告，但召回仍成功。目标 venv 中实际安装的
`mem0ai==2.0.13` 会在公开 `Memory.search()` 内先做 lemma/entity 预处理，再执行 dense search、Qdrant BM25 和
融合；HomeMaster 又在返回后单独调用一次 `vector_store.keyword_search()`。此前只根据旧认知把公开 search 判断为
dense-only，没有沿目标 wheel 的真实调用栈核对，因此单测和分支命中虽然通过，实际每次召回却执行了两次 BM25。

### 修法与教训

HomeMaster 改为只调用一次公开 `Memory.search()`，再与 metadata exact 合并，删除外层第二次 BM25。项目依赖
显式包含 `mem0ai[nlp]` 和锁定的 `en_core_web_sm` wheel，避免首次搜索临时下载。回归同时计数公开 hybrid 与其
内部 BM25 各一次、断言搜索阶段没有 spaCy 告警，并用真实 Qdrant 结果核对召回。第三方公开 API 的语义会随
版本改变；必须对锁定 wheel 追调用栈并审计实际外部调用次数，不能用“结果里有两个 source”证明分支没有重复。

### 参考

- `src/homemaster/memory/mem0_store.py`
- `tests/homemaster/memory/test_mem0_store.py`

## 2026-07-28 - USER快照已注入，模型仍把它当文件并重复读取

### 症状与根因

真实 Mimo 会话已经在推理中识别出 USER 内容，却先后调用 `read_file(USER.md)`、文件搜索、结构化检索和
`memory(target=user, action=read)`。根因有两层：`# USER.md` 这类标题看起来像工作区路径；更关键的是，只要
模型 schema 仍暴露 `read` enum，能力存在本身就会压过“普通召回不要读”的描述。仅改标题和描述后，真实
Mimo 仍调用了 `read`，证明该修法不完整。

### 修法与教训

冻结快照改用 Assistant Identity、User Profile、Persistent Memory 语义标题。模型可见的文件记忆工具只保留
add/update/delete，与 Hermes 的写工具边界一致；底层 `FileMemoryStore.read()` 继续供快照构建和写后独立终态
核验使用。涉及模型工具选择时，必须检查 schema 实际暴露的能力，并用真实流式事件断言首轮
`tool_calls=[]`，不能把禁止性描述或最终答对当成零冗余调用的证据。

### 参考

- `src/homemaster/memory/context_service.py`
- `src/homemaster/tools/memory_tools.py`
- `tests/homemaster/memory/test_context_integration.py`
- `tests/homemaster/memory/test_memory_tools.py`

## 2026-07-28 - Anthropic流式工具参数重复组装，复杂record又被Mimo编码成字符串

### 症状与根因

Mimo有时在正文输出 XML 风格 `<tool_call>`，同时原生工具调用出现空参数；HomeMaster当时自行聚合
`input_json_delta`，与 Anthropic SDK 的最终消息组装形成两套参数来源，无法仅凭运行 trace 排除 Harness 丢参。
改为 SDK 终态后，`memory(target=user)` 首次原生调用正常；真实 `add_memory` 又证明 Mimo能理解完整 FactRecord
字段，却稳定把嵌套 `record` 输出成 JSON 字符串。仅内联 `$ref`仍复现，根因不是后端 Pydantic校验。

### 修法与教训

Anthropic实时流只负责 text/thinking，工具调用统一读取 `get_final_message().content[].tool_use.input`；正文标记
永不执行。六个 memory输入契约改为 Pydantic单一真理源，Provider schema只做确定性本地引用内联。对已由
真实 Mimo证明的 nested-record字符串化，在 Pydantic before-validator中只解析 JSON object，再执行完整判别联合
校验，禁止接受任意文本或数组。真机验收必须分别断言首次工具参数、磁盘/DB终态和新session召回。

### 参考

- `src/homemaster/providers/llm_client.py`
- `src/homemaster/tools/memory_tools.py`
- `tests/homemaster/test_llm_client.py`
- `tests/homemaster/memory/test_memory_tools.py`

## 2026-07-28 - FastEmbed 默认将离线 BM25 工件写入易清理的 `/tmp`

### 症状与根因

HomeMaster 启动时 FastEmbed 报 `Could not find the model tar.gz file at /tmp/fastembed_cache/bm25 and
local_files_only=True`。代码正确要求 BM25 离线运行，但没有显式设置 FastEmbed cache path，于是依赖的默认值
落在系统临时目录。`/tmp` 被清理、换服务器或冷启动时，锁定的 `Qdrant/bm25` 工件不存在；FastEmbed 先尝试
HuggingFace cache 失败后回退到旧 GCS tarball 布局，因此错误信息还误导为缺少 tar.gz。

### 修法与教训

把锁定的 18 个 BM25 工件作为 HomeMaster package data 分发，首次启动原子 materialize 到
`memory.mem0.fastembed_cache_path`（默认项目 `.cache/homemaster/fastembed`），并让预检与 mem0/Qdrant
共用该缓存和 offline 环境。每次 materialize 后仍校验原始 commit、文件集合、SHA-256 和中文 sparse 编码；
缓存损坏可由随包工件重建，工件损坏或目录不可写则 fail closed。回归必须在空缓存、离线代理下测试源码和
已安装 wheel 两种形态，不能以已有 `/tmp` 缓存作为通过证据。

### 参考

- `src/homemaster/memory/bm25_preflight.py`
- `src/homemaster/memory/mem0_store.py`
- `tests/homemaster/memory/test_bm25_preflight.py`
- `tests/homemaster/memory/test_mem0_store.py`

## 2026-07-28 - 记忆后端召回成功，但完整记录只进 data，模型只看到 succeeded

### 症状与根因

真实 HomeMaster 已把 fact 写进 embedded Qdrant，新的 session 也由 semantic/BM25 命中准确记录；runtime event
中的 `data.records`、memory ID 和 value 全部正确，但模型连续认为“搜索成功却没有具体记录”。
`ApplicationToolExecutor._message()` 把 `ToolResult.metadata` 放进内部 `ToolResultMessage.data`，provider transport
却只序列化 `content`；而记忆 executor 的 `output` 只有 `memory search succeeded`。因此内部 trace 证明了后端
成功，却没有证明模型接收到了结果。

### 修法与教训

六个记忆工具在 ApplicationRuntime 消息边界把完整结构化 result data 序列化为 JSON `content`，同时保留
原有 `data` 供事件和程序消费。回归使用真实 ApplicationRuntime：模型先调用 `search_memories`，第二次 transport
请求必须从 `role=tool` 的 `content` 解析出准确 ID 和位置后才能回答；另断言 `add_memory` 的 ID 同样模型可见。
任何供模型决策的工具字段都必须在实际 provider request 的模型可见 content 中断言，内部 metadata/event 正确
不能替代这一门。

### 参考

- `src/homemaster/application/tool_executor.py`
- `tests/homemaster/application/test_application_runtime.py`

## 2026-07-27 - 可选 memory backend 的 doctor FAIL 误杀整个交互 shell

### 症状与根因

V2.1 加入真实 mem0/Qdrant doctor probe 后，两个 tmux 交互黑盒在出现 `homemaster>` 前直接退出。example
配置的 embedding key 是占位符，配置层会正确规范化为空；probe 因此报告 backend unavailable。但 doctor
把这个局部故障标为全局 `FAIL`，既有 interactive shell 遇任意 FAIL 会拒绝启动，于是“结构化记忆不可用、
文件记忆和其余系统可用”的设计被错误提升成整个应用不可用。

### 修法与教训

doctor 继续真实启动并报告 `memory_backend` 原因，但不可用状态标为 `WARN`；五个 mem0 工具仍在调用边界
fail closed 返回 `memory_backend_unavailable`，文件记忆与 shell 正常工作。回归同时覆盖真实锁冲突、文件读取
和两个 tmux 宽度。健康检查的严重度必须与故障域一致；可选子系统 fail closed 不等于顶层进程 fail stop。

### 参考

- `src/homemaster/cli/doctor.py`
- `src/homemaster/cli/interactive_shell.py`
- `tests/homemaster/test_cli_doctor.py`
- `tests/homemaster/test_cli_streaming_blackbox.py`

## 2026-07-27 - 误判 mem0 公开 search 为 dense-only，分支测试没有审计真实调用栈

### 症状与根因

V2.1 初版只观察到底层 Qdrant `search()` 是 dense 查询，便把上层 `Memory.search()` 误判成 dense-only，随后在
HomeMaster 外层补了一次 `keyword_search()`。目标 venv 的 `mem0ai==2.0.13` 实际会在 `Memory.search()` 中先调用
底层 dense search，再调用 BM25 并融合；只检查一个底层方法，没有沿完整公开入口追调用栈，导致 BM25 执行两次。

### 修法与教训

HomeMaster 不修改 site-packages，只调用一次公开 `Memory.search()`，再与 metadata exact 候选合并；公开融合结果
统一标记 `hybrid`，不虚构无法从返回值区分的 semantic/BM25 来源。启动时仍离线校验锁定的
`Qdrant/bm25` commit、全部文件 SHA-256 和中文 sparse 编码；缺缓存、checksum 错或 keyword probe 失败均显式
unavailable，不静默 dense-only。回归直接断言 raw point 含 named `bm25` vector，并计数公开 hybrid 一次及其
内部 BM25 一次。不能用一个“混合搜索有结果”断言证明没有重复调用。

### 参考

- `src/homemaster/memory/mem0_store.py`
- `src/homemaster/memory/bm25_preflight.py`
- `tests/homemaster/memory/test_mem0_store.py`
- `tests/homemaster/memory/test_bm25_preflight.py`

## 2026-07-27 - `SIGTERM` 退出 Gateway 主进程却遗留飞书 WebSocket worker

### 症状与根因

为切换运行中的 Gateway 向 CLI 主进程发送 `SIGTERM` 后，主进程立刻消失，但 `multiprocessing` 生成的
`lark.ws.Client` worker 仍持有到飞书的 TLS 连接。`run_gateway()` 只调用 `asyncio.run()`；默认 SIGTERM
直接终止解释器，因而不会进入 `serve_gateway()` 的 `finally` 或 `GatewayRuntime.aclose()`，已实现的
worker terminate/join deadline 根本没有机会执行。

### 修法与教训

在 Gateway CLI 的 event loop 中注册 `SIGINT`/`SIGTERM`，将信号转换为 service task 取消。`GatewayRuntime.serve()`
捕获该取消并走既有 absolute-deadline `aclose()`，随后才释放 application 资源。回归分别断言信号已进入
shutdown event、service task 被取消；真实进程验收还必须在 TERM 后逐项检查主进程、spawn worker 和 TLS socket
均已消失。主进程 PID 消失不是远程 Gateway 已停止的证据。

### 参考

- `src/homemaster/cli/gateway_command.py`
- `src/homemaster/gateway/runtime.py`
- `tests/homemaster/gateway/test_runtime.py`
## 2026-07-26 - Generic application run ID 被误用为 Coworker 环境 run ID

### 症状与根因

真实 Coworker 任务已创建 `coworker-...` 环境并能打开正确网页，但浏览器工具结果携带 generic runtime
生成的 `run-...`。随后 SOP 决策、终端验证和任务进度镜像把该 ID 发给 Case02 服务，服务只认识环境 ID，
因而持续返回 `unknown_run`。单测一直让两层 ID 相同或只直接调用局部 executor，未覆盖通用运行时生成
独立 run ID 的组合边界。

### 修法与教训

在 Coworker `RunRequest.dependencies` 中显式绑定 `coworker_domain_run_id`，并由所有外部环境工具统一读取；
generic runtime ID 继续只承担框架级 trace、provider 和 session 生命周期。回归故意令两个 ID 不同，逐项断言
browser、terminal、SOP 与 planner 镜像的真实 `EnvironmentClient` 入参都是 domain ID。

### 参考

- `src/homemaster/benchmarking/coworker_demo/correlation.py`
- `src/homemaster/benchmarking/coworker_demo/turn.py`
- `tests/homemaster/benchmarking/coworker_demo/test_domain_run_routing.py`

## 2026-07-25 - `skill` 与 `skill_view` 重复暴露同一加载能力

### 症状与根因

Home profile 同时向模型暴露 `skill(name)` 和 `skill_view(skill_name)`，两者最终执行同一个 Skill Registry
读取逻辑；Coworker 又只暴露旧名称。Available Skills 要求模型在两个近义入口之间选择，工具名也没有明确
表达“把完整说明加载进上下文”的动作。

### 修法与教训

所有 Profile 统一为唯一的 `load_skill(name)`，删除模型可见的旧入口；上下文继续只预载 Skill 名称和简介，
完整 `SKILL.md` 仅在调用后作为工具结果进入会话。一个模型能力只保留一个能表达动作的公开工具名；兼容
需求不得通过继续暴露同义模型工具来解决。

### 参考

- `src/homemaster/domain/tools.py`
- `src/homemaster/adapters/profiles.py`
- `src/homemaster/agent/context.py`

## 2026-07-25 - 原始 Pydantic 错误掩盖工具是否执行并诱发重复调用

### 症状与根因

模型连续调用 `grep` 时，工具边界只返回带 Pydantic 内部格式和文档 URL 的
`invalid tool arguments` 文本。反馈没有工具名、实际参数键、缺失字段或 backend 是否启动，模型无法从
结果区分输入校验失败和执行失败，最终连续五次相同错误后由 loop guard 停止。

### 修法与教训

输入校验失败统一返回与 metadata 同源的结构化 JSON，只陈述工具边界可观测事实：稳定错误码、工具名、
收到的参数键、缺失必填字段、逐项校验问题和 `backend_attempted=false`。不得在工具错误里猜测 Provider、
解析模型文本、推荐替代工具或注入重试提示；上游来源问题由对应层自行处理。

### 参考

- `src/homemaster/tools/executor.py`
- `tests/homemaster/tools/test_universal_executor.py`

## 2026-07-25 - POSIX 判断把 macOS 误当成支持 GNU `script` 参数

### 症状与根因

Home profile 的每条 `bash` 命令，包括 `echo hello`，都在执行前报
`/usr/bin/script: illegal option -- f`。移植后的 HomeMaster Bash 只判断 `os.name == "posix"`，因此在
Linux 和 macOS 都使用 `script -qefc`；但 macOS 提供的是参数不兼容的 BSD `script`。OpenHarness 的
平台感知实现已经排除了 macOS，独立移植版本却遗漏该分支，Linux 测试因此无法暴露问题。

### 修法与教训

Shell argv 按明确平台选择：Linux 在 `script` 可用时保留 PTY 包装，macOS 固定使用 `bash -lc`。用注入的
平台名分别锁定两套 argv，并在 macOS 真机通过 HomeMaster Registry 执行成功、非零返回码、超时和进程组
清理。不得用 `os.name == "posix"` 推导 GNU 用户空间工具的参数兼容性；移植跨平台工具时同步上游的平台
分支和测试，而不是只复制主执行路径。

### 参考

- `src/homemaster/tools/bash.py`
- `src/openharness/utils/shell.py`
- `tests/homemaster/tools/test_v20_openharness_bash_tool.py`

## 2026-07-24 - installed CLI 把配置路径硬绑源码根，非交互与 PTY 在 provider 前退出

### 症状与根因

源码 first-byte/Rich 黑盒全绿，clean wheel 的 CLI 却报 `provider 'Mimo' ... not found`；增加外部配置后
interactive 又因 `HOMEMASTER_CONFIG_PATH.relative_to(REPO_ROOT)` 抛 `ValueError`。默认配置常量只指向
源码 checkout，doctor 又假定任何合法配置都在该根下；原 doctor fixture 直接 monkeypatch 掉
`_config_source()`，掩盖了真实路径分支。

### 修法与教训

允许 `HOMEMASTER_CONFIG_PATH` 显式选择部署配置，repo 内路径报告相对值，外部路径报告绝对值。installed
黑盒从空 cwd、禁用仓库 pytest config、使用 wheel interpreter 和外部占位配置重跑 text/JSON/
stream-JSON 与宽窄 PTY。源码可运行不等于 wheel 可部署；测试不得绕过负责路径格式化的真实函数。

### 参考

- `src/homemaster/config/config.py`
- `src/homemaster/cli/doctor.py`
- `tests/homemaster/test_config_resolution.py`
- `tests/homemaster/test_cli_streaming_blackbox.py`

## 2026-07-24 - canonical immutable nested data 在下一模型轮 deep copy 崩溃

### 症状与根因

真实 HomeMaster 的两个 `web_fetch` 都已返回 typed 结果，下一模型轮却报
`cannot pickle 'mappingproxy' object`。canonical `ToolExecutionResult.data` 只在顶层转成普通 dict，嵌套
`MappingProxyType` 继续进入 Pydantic session message；`model_copy(deep=True)` 因而在工具成功后才崩溃。

### 修法与教训

canonical result 进入 legacy/session/provider message 前递归 thaw 全部 Mapping/tuple 容器，并用嵌套 data
做真实 deep-copy 回归。不可变 canonical 合同不能直接跨进要求可深拷贝 JSON 容器的边界。

### 参考

- `src/homemaster/tools/base.py`
- `tests/homemaster/tools/test_execution_result.py`

## 2026-07-24 - 配置报告无限工具预算，但 CLI 实际仍在第 12 轮终止

### 症状与根因

真实 HomeMaster 已下载并发布 Superpowers 的 14 个目录，却在处理第二个 URL 前以
`max_tool_iterations_exceeded` 退出。配置中的 `runtime.max_tool_iterations=None` 正确，但 one-shot 和
interactive CLI 构造 `RunRequest` 时没有把该值传入 `RunPolicy`，运行时静默使用默认 12。

### 修法与教训

所有 CLI 入口从同一 validated config 显式构造 `RunPolicy`，并在入口回归中断言请求携带真实预算。dry-run
或配置输出声称的预算必须接入每个实际执行入口；报告正确不等于运行生效。

### 参考

- `src/homemaster/cli/run_command.py`
- `src/homemaster/cli/interactive_shell.py`
- `tests/homemaster/test_cli_run.py`
- `tests/homemaster/test_cli_interactive.py`

## 2026-07-24 - `/tmp` 顶层文件遮蔽 wheel 依赖，制造隔离安装假失败

### 症状与根因

wheel 在新虚拟环境中连同全部声明依赖安装成功，但从 `/tmp` 作为当前目录导入 Registry 时，`attrs` 最终
加载了无关的 `/tmp/attr.py`，并因该文件访问另一用户的受限路径而报 `PermissionError`。Python 把当前目录
放在依赖解析前面；“位于 checkout 外”并不等于导入环境干净。

### 修法与教训

不修改外部 `/tmp/attr.py`，而是在专用的新建空目录中重跑同一 installed-wheel 门。CLI、58 个唯一普通名
工具、旧模块不可导入及 Bash 外部文件终态随后全部通过。installed-wheel 验证必须同时使用独立虚拟环境和
空工作目录，不能直接把共享 `/tmp` 顶层当作 cwd。

### 参考

- `plan/V2.0/homemaster-skill-identity-raw-output-remediation-handoff.md`

## 2026-07-24 - 飞书真实私聊 `p2p` 被内部 `private` 校验静默拒绝

### 症状与根因

真实用户消息在飞书后台显示 `im.message.receive_v1 SUCCESS`，WebSocket 连接也保持 `ESTABLISHED`，但没有
HomeMaster session/trace 或回复。SDK dispatcher 已把消息放入 IPC；下一层只接受内部
`chat_type in {private, group}`，而飞书真实私聊字段是 `p2p`，normalize 又原样透传，消息因此在 Runtime 前
返回 `False`。旧测试全部从 normalize 下游手造 `private`，所以 49 条通道/Gateway 测试全绿仍未覆盖真实边界。

### 修法与教训

在唯一 SDK normalize 边界把准确外部值 `p2p` 确定性映射为 canonical `private`，`group` 保持不变，未知值
继续拒绝。回归直接把锁定 `lark-oapi==1.7.1` 的真实 JSON payload 依次送过 dispatcher、normalize、
`accept_event()` 和 inbound bus，并断言最终 reply route 使用 `open_id`。平台消息 ACK 不是应用处理终态；还要
核对新 session/trace、provider 返回和飞书出站回执。

### 参考

- `src/homemaster/channels/impl/feishu.py`
- `tests/homemaster/channels/test_feishu.py`

## 2026-07-24 - 飞书消息成功但未注册的访问/已读事件持续返回 500

### 症状与根因

飞书后台显示 `im.message.receive_v1` 已 `SUCCESS`，但同一时段
`im.chat.access_event.bot_p2p_chat_entered_v1` 按原始投递和两次重试持续 `FAIL`。Gateway dispatcher 只注册
了消息事件；锁定的 `lark-oapi==1.7.1` 对未注册事件抛 `processor not found`，WebSocket 层随后向平台回写
500。修复访问事件后，真实回复又触发了同样未注册的 `im.message.message_read_v1` 并重复失败。消息 ACK 只
证明该 handler 返回，既不能代表其他订阅，也不能证明 IPC、Runtime 或回复发送完成。

### 修法与教训

为用户进入机器人单聊和消息已读事件分别注册显式 no-op ACK，不把非业务事件伪装成用户消息，也不写 IPC
队列。用真实 SDK 格式在同一 dispatcher 上分别断言：两个 no-op 事件成功且零 packet，消息事件成功且准确
产生一个 packet；再以真实租户核对 endpoint 业务码、WebSocket handshake/close 和生产子进程 deadline
stop。最终后台事件 `SUCCESS` 仍必须由新的真实事件确认，不能用 SDK payload 自验替代。

### 参考

- `src/homemaster/channels/impl/feishu.py`
- `tests/homemaster/channels/test_feishu.py`

## 2026-07-23 - installed wheel 缺少默认工具运行依赖

### 症状与根因

源码与 installed-wheel Markdown 测试均通过，但从 wheel 安装核心依赖后实例化 Home profile 先后因
Pillow 和 MCP SDK 缺失而失败。`observe` 已成为默认 Home 工具，Pillow 却仍只声明在 Coworker extra；
同时上游工具包在没有 MCP manager 时仍 eager import MCP-only adapters。旧 wheel 门使用 `--no-deps`
且只枚举资源，因此从未执行默认 profile import。

### 修法与教训

把 Pillow 声明为核心依赖，并把 MCP-only imports 移到 manager 存在的分支。隔离 wheel 门安装声明依赖，
再从源码 checkout 外实例化 Home profile 并逐项断言 39 个默认工具。Package-data 枚举只能证明文件入包，
不能证明安装后的默认入口可导入和构造。

### 参考

- `pyproject.toml`
- `src/openharness/tools/__init__.py`
- `tests/homemaster/skills/test_installed_package.py`

## 2026-07-23 - config show 直接序列化真实配置会向模型泄露凭证

> 历史说明：本条记录当时采用的脱敏策略。2026-07-24 owner 已锁定 candidate 2，当前规则改为通过
> permission/schema/ownership 后保持已选运行时文本原值；Git 占位、认证失败非回显和 binary/ACL 边界不变。

### 症状与根因

最终评审发现 application-owned `config(action="show")` 直接调用完整配置的 JSON serializer。Provider
API key、任意名称的 MCP env/header credential 和 URL userinfo 因而进入工具结果，并可继续进入模型消息
与 JSONL trace。原测试只覆盖配置持久化，没有把“管理工具可调用”和“公开结果可展示”分开验证。

### 修法与教训

配置展示先做结构化 public projection：递归遮盖 secret-shaped key 和 URL userinfo，再替换所有已配置的
敏感字面值；同一回归分别检查工具结果、模型消息和真实 JSONL 文件。任何 user/model/event-visible 配置
都必须走 public summary，禁止直接序列化 authoritative config。

### 参考

- `src/homemaster/tools/openharness_runtime.py`
- `tests/homemaster/tools/test_v20_openharness_service_tools.py`

## 2026-07-23 - Gateway artifact 投影在模型前删除多模态工具结果

### 症状与根因

启用 Gateway/飞书后，`ArtifactPublisher` 在 `ToolExecutionResult.to_message()` 前把 image/attachment
写入 store，并用无媒体的结果替换 canonical result。`observe` 已成功截图，却因 `IMAGE_ONLY` 结果变成
零图片而抛错；其他需要模型读取媒体的工具也会静默退化为 artifact handle。原测试分别验证 Publisher
脱敏和 observe 图片，却没有在启用 Publisher 的真实 ApplicationRuntime 调用链中同时断言两个出口。

### 修法与教训

把模型投影与 Gateway 公共投影分开：canonical result 原样生成 provider-facing content；Publisher 只
返回 tenant/session/run-bound refs，refs 进入内部事件数据并由公共投影发往 Gateway。任何 artifact、日志、
审计或 channel 旁路都不得改写模型消息；同一集成测试必须同时断言 provider 收到的媒体和 Gateway 可回读
handle。

### 参考

- `src/homemaster/artifacts/publisher.py`
- `src/homemaster/application/runtime.py`
- `tests/homemaster/application/test_application_runtime.py`

## 2026-07-23 - 截图 freshness 状态机让浏览器动作全部被拒绝

### 症状与根因

真实 Coworker 已正常启动浏览器、页面和 provider，但 observation ledger/provider binding 把一次截图变成
后续业务动作的 freshness 前置条件。截图不是当前画面的普通模型输入，而成为 benchmark 专属授权状态；因此
所有业务 DOM 动作都可能在实际执行前被拒绝。旧的 unit test 只验证 service 内部状态转换，无法证明浏览器动作
在不截图、截图后或 provider 请求之间都仍可执行。

### 修法与教训

截图统一为 `core.observe.v1`：只返回一张有效 PNG image block，不产生模型可见文本/DOM/状态元数据，也不接入
权限、动作、completion 或 provider-binding 状态机。Coworker 保持 DOM 工具，ALFWorld 保持动作工具；分别在
工具消息、provider request 和后续动作终态验证它们独立。不要把 inspection/read 工具当作无关动作的授权。

### 参考

- `src/homemaster/tools/observe.py`
- `src/homemaster/tools/contracts.py`
- `src/homemaster/application/runtime.py`

## 2026-07-22 - 飞书失败尝试提前占用 dedup 导致重投消息被吞一小时

### 症状与根因

飞书消息在正文解析、附件下载和 inbound bus publish 前就写入一小时 dedup 表。首次下载 503、落盘失败
或 bus 拒绝后函数返回 False，但 claim 没有释放；平台重投相同 message ID 会被判 duplicate，临时故障
变成永久无响应。原测试只覆盖“成功后重投被拒绝”，没有覆盖失败后重投。

### 修法与教训

dedup 使用 reserve/commit/rollback 语义：并发处理前先占位，只有 inbound publish 成功才保留 completed
记录；解析、下载、落盘、reaction、publish 失败或取消都释放占位，并删除尚未交付的本轮附件。分别用
首次下载失败和首次 bus reject 证明同 ID 第二次可成功，成功后的第三次仍被拒绝。

### 参考

- `src/homemaster/channels/impl/feishu.py`
- `tests/homemaster/channels/test_feishu.py`

## 2026-07-22 - HPC 冷缓存下 lark-oapi 导入超时会伪装成飞书连接失败

### 症状与根因

一次性 SDK probe 在 20/30 秒内无输出，看起来像 endpoint 或 WebSocket 卡住。faulthandler 证明进程尚在
`import lark_oapi`：SDK 顶层导入会扫描大量自动生成的 API model，HPC 文件系统冷缓存下本次耗时约
48.2 秒，网络 POST 尚未开始。短超时因此同时误杀了导入和后续连接证据。

### 修法与教训

外部探测必须在配置加载、SDK import、endpoint POST、业务返回码、WebSocket handshake 和 socket close
分别立即输出无敏感信息的阶段状态，并给冷导入独立预算。只有 endpoint HTTP 200/业务码 0 与 WebSocket
connected 才证明飞书可连；“进程超时”不能直接归因于网络或凭证。

### 参考

- `plan/feishu-trusted-entry-policy-change-plan.md`
- `src/homemaster/channels/impl/feishu.py`

## 2026-07-22 - SDK 能建连不代表 Gateway 能在 deadline 内停止

### 症状与根因

`lark-oapi` 1.7.1 的 WebSocket `Client.start()` 是阻塞入口，已安装对象没有可依赖的 public
`stop/close/shutdown/disconnect`。仅在线程外层设置 `_running=false` 不会中断 SDK 内部连接，测试中的
mock worker 能结束也不能证明真实 SDK worker 可 join。另一个易漏点是 Python logger 的 filter 不会在
子 logger 向父 logger propagation 时自动运行；只给 `lark_oapi` 父 logger 加 filter，子 logger 仍可能
把 Authorization 或带 query 的 URL 交给 root handler。

### 修法与教训

把 WebSocket client 隔离到 spawn 子进程，通过 typed queue 只传规范化事件和 fatal/completion；stop
在同一个 absolute deadline 内 terminate/join，必要时 kill/join，并把残留 worker 视为关闭失败。SDK
logger 设为 WARNING，同时在 LogRecord factory 层对已知依赖 logger 做统一脱敏，确保现有和后续 handler
都只能收到清理后的 record。import、builder 存在、mock stop 和 helper success 都不是外部终态证据；
真实租户仍须分别证明建连、停止、发送返回码和客户端/REST 独立回读。

### 参考

- `plan/feishu-single-channel-openharness-migration-plan.md`
- `src/homemaster/channels/impl/feishu.py`
- `tests/homemaster/channels/test_feishu.py`

## 2026-07-22 - Provider 流式测试全绿但 CLI 仍在完成后批量输出

### 症状与根因

Provider transport 持续 yield delta，相关单测也全绿，但文本和 `stream-json` 在
`RunResult` 形成前没有任何字节。统一运行时的 `_consume_stream()` 只聚合 delta，
没有把安全文本转发到 EventBus；旧 Mimo 专用路径被统一 LLMClient 替代时丢失了
`transport.delta` 发布，而测试只验证 provider 与最终聚合值。

### 修法与教训

由通用运行时统一发布逐 delta 事件，并用每 run/assistant-turn 独立的增量脱敏器
保留不稳定后缀；七事件投影和实时 sink 消费同一 EventBus，最终聚合与持久化保持
不变。每个实时入口必须有黑盒门禁：假 provider 发首 delta 后保持阻塞，断言真实
CLI 在 provider 完成前已经产生首字节且进程仍运行。Provider 层“能流式”不能替代
UI 层 pre-completion first-byte 证明。

### 参考

- `plan/realtime-rich-streaming-cli-implementation-plan.md`
- `tests/homemaster/test_cli_streaming_blackbox.py`
- `tests/homemaster/test_generic_agent_runtime.py`

最新记录放在最上方。

## 2026-07-23 - Skills V2.0 跨边界验证的四个假绿点

### 症状与根因

- service tool 直接 pipeline 测试通过，但真实 ApplicationRuntime dispatch 崩溃：composition 从不存在的
  `SessionRuntime.settings` 取 application services，测试绕过了这个边界。
- executor 已返回 `waiting_user`，远程 run 却没有停止：canonical `ToolResultMessage` 把 marker 序列化在
  `data["data"]["waiting_user"]`，stop condition 按中间 dict 形状读取。
- 源码 checkout 中八份 bundled Skill 测试全绿，但安装 wheel 缺 Markdown：package-data 只列了 Python，
  测试也没有枚举安装产物。
- child agent 单测声称复用父配置，真实子进程却总加载仓库默认配置：argv 只透传 model，没有透传父应用
  config path。

### 修法与教训

service-backed 工具必须通过真实 ApplicationRuntime dispatch 复验；跨组件停止条件以实际序列化 envelope
写回归；资源型功能必须安装 wheel 后逐文件枚举；spawn worker argv 显式携带 authoritative config path。
本次分别增加 application/Gateway 恢复历史测试、installed-wheel 八文件断言和 loopback provider 的真实
child-worker 进程门。

### 参考

- `src/homemaster/application/runtime.py`
- `src/homemaster/gateway/runtime.py`
- `src/homemaster/cli/child_worker.py`
- `tests/homemaster/skills/test_installed_package.py`

## 2026-07-22 - 临时候选只同步 dev/mcp extra，Coworker 到浏览器创建才缺 Playwright

严重程度：中。V1.9 临时候选通过了 Python import、Provider、MCP 和 Coworker preflight，但正式 run 在
service、observer 和录屏启动后才以 `ModuleNotFoundError` 退出；preflight 没有核对执行 turn 所需的
Playwright Python 模块。

### 症状与根因

- 临时环境只执行 `uv sync --extra dev --extra mcp`，漏掉 `pyproject.toml` 已声明的 `coworker` extra。
- `config/coworker_demo.yaml` 的 `service_python` 指向 canonical Coworker venv，所以 service/preflight
  正常；真正创建 `PlaywrightBrowserDriver` 的却是 candidate venv，两个 Python 边界不同。
- 失败 wrapper 只保留 `ModuleNotFoundError` 类型；通过 run 阶段、缺失 `agent/` 目录和两个 venv 的
  `find_spec("playwright")` 对比，才定位到 candidate 缺包。

### 修法与教训

- 按将要执行的入口同步全部 optional extra；Coworker candidate 必须包含 `coworker`，不能用 service
  venv 的 preflight PASS 推断 runner venv 完整。
- preflight 后再从实际 runner Python 启动 `sync_playwright()`，并核对配置的 Chrome executable；同时
  保留首次失败 attempt，不覆盖 ledger。
- 本次临时候选按锁文件补齐 Playwright 1.61.0、greenlet 3.5.3 和 pyee 13.0.1，未升级共享依赖。

### 参考

- `pyproject.toml`
- `src/homemaster/benchmarking/coworker_demo/turn.py`
- `scripts/coworker_demo/preflight.py`
- `coworker-20260722-101417-86a6e389`

## 2026-07-21 - V1.9 final review 暴露跨阶段所有权与外部失败语义假绿

严重程度：高。CL-18～CL-21 的阶段 review、`1285 passed` non-live 和静态门都完成后，唯一整体 final
review 仍发现 6 个可让控制面挂死、权限 fail-open、旧进度串代或扩展行为逃离 digest 的问题。

### 症状与根因

- Device event 先写内存，但 audit sink 异常仍从 `append()` 抛出；acquired audit 可跳过 lease `finally`，
  emergency-stop requested audit 可阻止后端 stop。
- Discovered MCP tool 没有 state effect，被 PLAN/default 当只读；连接后 timeout/call failure 又落成
  confirmed failure，外部可能已变更却允许上层按普通失败处理。SDK annotation 尚未真环境核对。
- Gateway 的 deadline 只覆盖 bus drain，active worker cancel/join、channel stop 和 service join 都可无界；
  public-event backlog 在消费时读取 current generation，把旧 run progress 重新标成新代际。
- Extension factory 已返回 cleanup 后，loader validation、reload candidate 与 composition 后续构建之间没有
  连续 owner；async rollback 还是 fire-and-forget。
- content digest 只覆盖 entrypoint；真实 `__file__` 与普通 absolute import 可读取未纳入 hash 的 adjacent
  helper，helper 改变时 approval SHA 不变。

### 修法与教训

- Authoritative event 与 audit mirror 分层；sink failure typed 留存，分别黑盒断言 lease 释放和 stop 调用。
- 未证明只读的外部 tool 按 mutating fail closed；已尝试且外部终态不明返回 `outcome_unknown`。
- 用一个 absolute deadline 和 `asyncio.wait` 覆盖完整 shutdown；generation 在 event 生产时固化。
- factory success 立即建立 rollback owner，async failure 必须 await cleanup；composition owner 持续到
  ApplicationRuntime 接管。
- manifest 显式列 dependency files，digest 和 same-bytes importer 覆盖它们并隐藏真实 `__file__`；同时
  明确 trusted code 仍可硬编码任意外部路径，不能把内容锁定宣传成 sandbox。

### 参考

- `src/homemaster/devices/contracts.py`
- `src/homemaster/mcp/adapter.py`
- `src/homemaster/gateway/runtime.py`
- `src/homemaster/application/runtime.py`
- `src/homemaster/extensions/{contracts,loader,reloader}.py`
- `src/homemaster/cli/composition.py`
- `tests/homemaster/{devices,mcp,gateway,extensions}`

## 2026-07-21 - CL-21 同源绿灯漏掉 timeout、reload 与 cleanup 绕过

严重程度：高。CL-21 首轮 targeted 与完整 non-live 都通过，但 stage review 证明抗取消 callback 可让
`asyncio.wait_for` 越过 deadline、hook-only reload 可漂移 version/grant、exact token 可绕过 canonical
capability，失败 candidate 和 active-close 还会泄漏或提前清理资源。

### 症状与根因

- timeout 测试只用了会正常响应 cancellation 的 `asyncio.sleep`；`wait_for` 会等待抗取消 coroutine，既
  不按 deadline 返回，也可能把迟到结果发布为成功。
- reload digest 只覆盖 tool snapshot；无工具 extension 的 id/version/requested/granted capability 不在门内。
- permission 循环把 exact tool token 当成每一项 required capability 的替代，破坏三方 capability 交集。
- `enabled_tool_ids or profile_ids` 混淆“显式空集合”和“未提供”，且 subset 校验晚于 RUN_START hook。
- `O_NOFOLLOW` 只放在 entrypoint 最后一个分量，中间目录 symlink 仍存在 TOCTOU；失败加载批次丢弃已返回
  的 async cleanup，close 也没有先 join active callback。
- dir-fd 改造后，same-bytes monkeypatch 收到的 path 从绝对路径变成相对路径；测试仍直接 `Path(path)`
  写替换内容，因而在仓库根生成了 `extension.py`。测试全绿但工作区被污染，直到 final-review preflight
  的 untracked audit 才发现。

### 修法与教训

- timeout 测试必须包含捕获 `CancelledError` 后继续运行的 callback；host 立即 fence result，并保持 task
  active，直到它真实退出。Close 必须 seal/quiesce 后才运行 stop hook 和 cleanup。
- reload boundary 独立覆盖 extension identity、version、requested/granted capability 与 tool plane；只有
  hook bytes 可热换。
- canonical capability 永远逐项核对；exact token 不替代 plugin/hook required capability。空 override 用
  `None` sentinel，与空 tuple 分开，并在 hook 前拒绝越界请求。
- 文件从 pinned root dir-fd 逐级无 symlink 打开。每个 partial/candidate/catalog failure 测试都断言 cleanup
  的真实外部终态，而不是只看异常或 diagnostic。
- monkeypatch/fake 回调若接收相对路径，必须显式锚定 `tmp_path`/fixture root；测试后审计新增 untracked
  文件，不能把“pytest exit 0”当作工作区无副作用证明。

### 参考

- `src/homemaster/extensions/{loader,hook_runner,reloader}.py`
- `src/homemaster/application/{contracts,runtime}.py`
- `src/homemaster/permissions/policy.py`
- `tests/homemaster/extensions/test_extensions.py`
- `tests/homemaster/application/test_application_runtime.py`

## 2026-07-21 - 缺 capability 的 blocking hook 被静默跳过

严重程度：高。CL-21 runner 最初把 principal capability 检查写进 hook 选择过滤器；callback 确实没有
执行，但一个 `block_on_failure=true` 的安全 hook 也不会产生 denied result，run 会继续进入 provider。

### 症状与根因

- 测试只断言未授权 callback 调用次数为零，把“拒绝执行 hook”误当成“run 已 fail closed”。
- matcher/event 选择与 authorization 被合并成一个过滤条件，授权失败没有可观察结果，也无法应用
  blocking policy。
- plugin tool 若允许空 `required_capabilities`，同样会让 deployment grant 停留在加载期，运行时只剩
  generic `tool.read/tool.mutate`，三方 capability 交集没有真正闭合。

### 修法与教训

- 先按 event/matcher/priority 选择 hook，再单独做 principal authorization；未授权必须产生 typed denied
  result，并按该 hook 的 blocking policy 决定是否停止当前 run。
- plugin tool 强制声明至少一个 canonical `required_capabilities`，loader 核对 requested 与 deployment
  grants，permission policy 再逐项核对 run principal。
- 权限测试同时断言 callback 次数、denied reason、blocked aggregate 和 provider 调用次数，不能只验其中一项。

### 参考

- `src/homemaster/extensions/hook_runner.py`
- `src/homemaster/extensions/loader.py`
- `src/homemaster/permissions/policy.py`
- `tests/homemaster/extensions/test_extensions.py`
- `tests/homemaster/application/test_application_runtime.py`

## 2026-07-21 - Gateway queue/lifecycle boundary 让阶段测试假绿

严重程度：高。CL-20 的 focused tests 都通过，但只覆盖了“消息入队时 generation 正确”和“正常
channel stop”；旧 generation 已入队消息、未认证附件、重复 final、后台 task 异常和 shutdown drain
没有被黑盒验证。

### 症状与根因

- egress 只在 producer 入队前检查 generation，旧 final 在 reconnect 后仍会发送。
- Telegram handler 先下载附件再做 exact sender mapping，未授权 sender 可触发网络和磁盘副作用。
- `assistant.reply` 与 `RunResult.final_reply` 都被发送，投影测试没有断言远程 outbound 的完整序列。
- `serve()` 只等待 channel task；后台 ingress/egress/projection 失败不会 fail-fast，清理还会取消 egress
  后再尝试 drain，导致关键消息丢失或 channel 未停止。
- projection 的 key-based redaction 不能保护自由文本，bridge terminal reply 又绕过了 projection。

### 修法与教训

- 每条 outbound 在 egress 读取后重新核对 session generation 和 authoritative identity；队列中的旧消息也必须丢弃。
- 认证必须先于任何 attachment `get_file`、下载或落盘；测试要断言未授权路径的下载调用次数为零。
- Gateway 规定 terminal `RunResult` 是唯一 final；公共事件只补充非 terminal progress，并逐序列断言一次 final。
- supervisor 必须观察全部 service tasks；shutdown 先拒绝新输入、保留 egress 排空 outbound，再停止 channel，
  并对 drain deadline 和主动取消区别处理。
- 自由文本和 terminal outbound 必须经过同一 projection；配置 secret、credential assignment、URL query
  和宿主路径都要有独立脱敏断言。

### 参考

- `src/homemaster/gateway/runtime.py`
- `src/homemaster/channels/impl/telegram.py`
- `src/homemaster/events/public_projection.py`
- `tests/homemaster/gateway/test_runtime.py`

## 2026-07-21 - 空 connection pool 让设备租约只在 isolated tests 生效

严重程度：高。Factory 正确创建并拥有 connection pool，pool/lease 的单测也全绿，但 production
`RunRequest.environment` 从未注册进去；真实执行仍直接使用 borrowed backend。

### 症状与根因

- 测试只断言 factory settings 里“有一个空 pool”，没有让真实 `ApplicationRuntime.run()` 穿过它。
- 无声明 identity 的 backend 按每次调用者 tenant 临时合成 identity，同一物理对象可形成两个 tenant
  lease slot 并发执行。
- disconnect 从冻结的 registration generation 每次都计算 `+1`；stop 后会回退或 stale。close 和
  同步 `mark_disconnected` 完全不通知 lease manager。
- FIFO future 被 grant 后没有进入 backend 前的二次锁内核对，stop 可在该窗口抢先但 waiter 仍执行。

### 修法与教训

- 写一条 factory-to-runtime 接线测试，要求同一 borrowed backend 在 provider 前绑定首个 tenant，跨
  tenant 重绑 fail closed，application close 不关闭 borrowed resource。
- 所有 terminal transition 从 lease owner 原子取得下一 generation；不得从 registration 快照重算。
- future grant 不是 backend 授权终态；yield 前必须在同一 registry lock 内复核 active lease、generation
  和 READY state。
- 外部 stop 返回与状态查询分别规范化成内部 typed receipt，保留两次 return code；raw 字符串不背书。

### 参考

- `src/homemaster/application/runtime.py`
- `src/homemaster/devices/connection_pool.py`
- `src/homemaster/devices/lease_manager.py`
- `tests/homemaster/application/test_factory.py`


## 2026-07-21 - principal/tenant 混淆与 resource URI 让 MCP 安全测试假绿

严重程度：高。MCP 原始输出看似按 tenant 分区、resource list 也只暴露 opaque id，但实际写 artifact
时使用了 principal id，resource read payload 和 audit 又把 URI 原样带出。

### 症状与根因

- 测试把 `subject_id` 和预期 `tenant_id` 都写成 `tenant-a`，掩盖了 ACL/quota domain 取错字段。
- `mcp_list_resources` 隐藏 URI 不代表 `read_resource` 返回的 `contents[*].uri` 也被隐藏；普通递归
  credential redaction 不会把 URI 本身视为 secret。
- audit 被当成旁路日志，既记录完整 URI，又允许 sink 异常改变连接状态或中断 cleanup。

### 修法与教训

- ACL 测试必须使用不同 principal/tenant 值，并从真实 tenant partition 回读 raw bytes。
- 对 resource discovery、read payload、model preview、audit 分别断言 URI 边界；raw artifact 可在 ACL 内
  保真，模型与 audit 只能看到 opaque id/hash。
- 可观测 sink 故障必须 typed 留存且与被观测生命周期隔离；用多连接 close 测试证明每个实例都清理。

### 参考

- `src/homemaster/mcp/adapter.py`
- `src/homemaster/mcp/client.py`
- `tests/homemaster/mcp/test_adapter.py`
- `tests/homemaster/mcp/test_client.py`

## 2026-07-21 - live_api 未 await 异步 Provider，正式请求根本没有发出

严重程度：高。V1.9 release 恢复时，最小真实 Provider gate 在读取响应前直接拿到了 coroutine，
因此测试失败且没有产生任何外部请求；另外一条上下文 live gate 存在同样漂移。

### 症状与根因

- `LLMClient.complete()` 已迁移为 `async def`，但 `test_e2e_real_api.py` 的两条 live 用例仍按同步接口调用。
- non-live 默认排除 `live_api` marker，所以全量 `1155 passed` 无法发现这两条验收入口已经失效。
- fixture 构造成功和 coroutine 对象存在都不是 Provider 外部返回证据。

### 修法与教训

- 将两条用例改为 pytest async test，并直接 `await transport.complete(...)` 后断言真实响应内容。
- Provider sync/async 契约变化时，审计全部 live consumer；把 `coroutine was never awaited` 视为 gate
  失败，不得用手工请求替代失效的正式测试后继续发布。
- live 门同时保留命令非零/零返回码与真实响应终态，避免“测试运行了”被误算成“请求发生了”。

### 参考

- `src/homemaster/providers/llm_client.py`
- `tests/homemaster/test_e2e_real_api.py`

## 2026-07-21 - Pydantic model_copy 让 env override 绕过字段校验

严重程度：中。CL-17 初版的 file 配置会经过 typed validator，但 env/CLI 覆盖使用
`model_copy(update=...)`，同一字段在不同来源下实际遵循了不同契约。

### 症状与根因

- `HOMEMASTER_MIMO_BASE_URL=https://env.example/v1/` 合并后保留末尾 `/`，而 YAML 中同值会被
  `base_url` validator 规范化。
- `model_copy(update=...)` 不重新运行 Pydantic validator，因此非法 `auth_type` 也可能进入已标为
  typed 的 provider 对象；只用 fake client 断言 kwargs 无法发现该问题。
- 原测试只覆盖 override 优先级，没有用一个必须经过规范化或 enum 校验的值证明 validator 真执行。

### 修法与教训

- 把 provider dump 与 override 合并后重新调用 `ProviderProfileConfig.model_validate()`。
- 对 base URL 规范化、认证 enum、model 和 provenance 分别断言，保证 file/env/CLI 使用同一 schema。
- 外部 `auth_token` 另在当前安装的 Anthropic SDK 0.116.0 上核对构造器签名和实例化/关闭，不以 fake
  factory 代替真环境符号验证。

### 参考

- `src/homemaster/config/config.py`
- `tests/homemaster/test_config_resolution.py`
- `tests/homemaster/test_llm_client.py`

## 2026-07-20 - 聚合存在性检查让缺失 Planner 和部分真实模型调用仍可通过验收

严重程度：高。最终评审发现两个假阳性窗口：成功 Planner 结果可以静默丢弃不安全/超限 plan；provider 门只要求至少一个 request 和 response，不能证明完整实时推演都由真实模型响应驱动。

### 症状与根因

- `_safe_plan_snapshot()` 返回 `None` 后，投影仍发布 `tool.call_completed/succeeded`，导致页面保留旧计划或等待状态；verifier 又只检查“存在的 plan 是否属于 Planner”，没有检查“成功 Planner 是否必须有 plan”。
- provider verifier 分别检查 request 列表和 response 列表非空，但不核对 iteration 缺失、重复、集合不一致、顺序颠倒或工具在成功响应前启动。
- 两个门都把“至少有一个/已有的都合法”误当成“每个必需实例都合法”，属于聚合判据掩盖 per-instance 缺失。

### 修法与教训

- 成功 `task_planner` / `task_progress_check` 的安全快照无法生成时必须抛出投影错误，不能降级为无 plan 的成功事件。
- 独立 presentation verifier 逐事件要求每个成功 Planner/进度结果带合法 plan。
- provider verifier 要求每个 transport iteration 都是连续非负整数，request/response 各一次、集合相等、request 早于 response，且工具不能在成功 response 前启动。
- 对必需实例验收，写“每一个”的反例 mutation 测试；不要用非空、any 或只验证现存项替代完整配对。

### 参考

- `src/homemaster/benchmarking/coworker_demo/presentation.py`
- `scripts/coworker_demo/verify_run_bundle.py`
- `tests/homemaster/benchmarking/coworker_demo/test_presentation_projection.py`
- `tests/coworker_demo/test_verify_run_bundle_presentation.py`

## 2026-07-20 - 长视频停止已成功却因客户端超时触发重复非幂等停止

严重程度：高。真实 Mimo 已完成 24/24 轨迹、14/14 结果检查和 100 分，FFmpeg 也已正常退出并产出通过验证的 362 秒视频，但顶层 attempt 仍被标记失败。

### 症状与根因

- 通用环境请求超时固定为 20 秒，而长视频停止还要执行 ffprobe、首中末帧和多张命名事件帧解码，第一次 `recording/stop` 在服务端完成并返回 200 时，客户端已经超时。
- 调用方只有在完整 HTTP 响应返回后才设置 `recording_stopped=True`，因此 `finally` 把超时误判成“尚未停止”，再次调用同一副作用接口。
- 录制会话没有缓存完成结果，第二次停止继续向已正常退出的 FFmpeg stdin 写入 `q`，在 `flush()` 抛出 `BrokenPipeError` 并返回 500。
- 业务分数、视频 manifest 和第一次 200 都是成功证据；attempt 失败属于生命周期协议错误，不能反向把真实模型或视频判成失败。

### 修法与教训

- `recording/stop` 使用 180 秒专用验证超时，不继承短请求的通用 20 秒预算。
- 服务会话用互斥锁保护停止过程，并缓存 recorder/display 的完成结果；重复调用返回深拷贝缓存，不再触碰 FFmpeg 或 display。
- 对“请求超时但服务端可能已执行”的副作用接口，必须同时设计足够的操作级超时与服务端幂等语义；仅在客户端加重试会把已成功的外部终态破坏掉。
- 验收继续要求顶层 attempt 成功、外部返回码和独立 bundle verifier 同时通过，不能只因已存在可播放视频就接受失败 attempt。

### 参考

- `src/homemaster/benchmarking/coworker_demo/environment_client.py`
- `apps/case02_openenv/src/case02_openenv/api.py`
- `var/coworker-demo/coworker-20260720-022516-8c773877/`

## 2026-07-20 - 命名事件帧跨过下一事件却仍通过像素门

严重程度：高。manifest 的 source event、时间戳和像素统计都可以正确，但截图时刻已经进入下一条 presentation event，导致一张名为 `rollback_decision_required` 的帧实际显示 `progress_required`。

### 症状与根因

- 两个失败事件只相隔约 0.32 秒，recorder 固定使用 `source + 0.35s` 的 UI settle margin。
- 独立 verifier 只核对 offset 公式、source event 存在和 observer 区域非空；下一事件同样是非空有效 UI，因此全部门都假绿。
- controlled provider 连续返回工具调用，没有给 observer 留出稳定展示失败事件的窗口。

### 修法与教训

- 每个命名帧的 settle margin 取配置上限与“到下一 presentation event 间隔的一半”的较小值，保证取帧时刻严格早于下一事件。
- 独立 verifier 反向检查每个命名帧不得跨过 source 后的下一事件；controlled failure profile 另留固定观察窗口。
- 最终仍逐张人工判读 exact failure code、中文原因、失败工具和恢复折叠；source ID、非空像素和区域方差不能证明画面语义正确。

### 参考

- `apps/case02_openenv/src/case02_openenv/recording/recorder.py`
- `scripts/coworker_demo/verify_run_bundle.py`
- `var/coworker-demo/coworker-20260720-012043-f3f9680b/`（跨事件的反例）
- `var/coworker-demo/coworker-20260720-014757-c9a55e12/`（修复后 anomaly PASS）

## 2026-07-20 - attempt manifest 字段名与函数形参冲突让真实入口立即失败

严重程度：高。Task 5 的 286 项单测、静态检查和 verifier mutation 全绿，但第一次真实 shell 黑盒 run 在分配目录后立刻抛出 `_update_attempt_manifest() got multiple values for argument 'run_root'`。

### 症状与根因

- helper 的第一个位置形参叫 `run_root`，manifest 同时需要写入名为 `run_root` 的字段。
- 真实入口调用 `_update_attempt_manifest(run_root, run_root=str(run_root), ...)`，Python 在进入函数前就因重复绑定失败。
- 单测只覆盖了 status/error 更新，没有使用与真实入口完全相同的 `run_root` 字段，因此内部 helper 绿不能证明顶层入口可运行。

### 修法与教训

- 将内部路径形参改名为 `artifact_root`，保留外部 manifest 的 `run_root` 字段。
- 回归测试按真实入口参数形态创建 manifest，并由 normal/anomaly shell 黑盒 run 证明目录、视频和最终 bundle 都真实生成。
- 修改生命周期 helper、参数名或 attempt tracking 后，必须跑至少一次顶层 CLI smoke；不能把 helper 单测当作入口验收。

### 参考

- `src/homemaster/benchmarking/coworker_demo/turn.py`
- `tests/homemaster/benchmarking/coworker_demo/test_turn.py`
- `var/coworker-demo/observable-failure-gate/shell-normal-observable_failures.stdout.log`

## 2026-07-20 - 零失败轨迹无法证明异常原因在视频中可观察

严重程度：高。clean scripted 视频和单测都可以通过，但没有失败实例时，无法证明真实 LLM 被门禁拒绝后，具体原因会持续显示并在匹配恢复后折叠。

### 症状与根因

- 旧演示只有成功路径，observer 的 latest result 很快被后续动作覆盖。
- 展示投影若丢失稳定错误码，只剩通用失败文本，clean 轨迹仍不会暴露问题。
- 聚合检查只要存在一张好图就通过，会掩盖其他 incident 帧错误。

### 修法与教训

- 发布前运行仅用于展示验收的 `observable_failures` profile，normal 和 anomaly 分别逐实例触发并恢复门禁错误。
- 对全部稳定安全码分别验证投影、恢复规则和真实 Chrome open/resolved DOM；叙事黑盒 run 另外验证真实环境拒绝、连续视频和外部终态。
- scripted gate 只证明展示能力，绝不替代最终 Mimo `mimo-v2.5` 实时执行验收。

### 参考

- `scripts/coworker_demo/scripted_shell_gate.py`
- `tests/case02_openenv/test_observable_presentation.py`
- `tests/case02_openenv/test_pages.py`
- `var/coworker-demo/coworker-20260720-015325-2101b694/`（normal PASS）
- `var/coworker-demo/coworker-20260720-014757-c9a55e12/`（anomaly PASS）

## 2026-07-16 - 正式成功门自引用产品 artifact 与视频结论

严重程度：高。业务、轨迹和视频都可以真实成功，但若最终门把 `artifact_failure` 固定为 false、只校验 manifest 已列出的条目，或让独立 verifier 信任产品写入的帧结论，缺失、篡改和伪造证据仍可能被报告为正式成功。

### 症状与根因

- 评分器生成了 artifact registry，却在 `formal_success` 计算中把 `artifact_failure` 直接设为 false；registry 的 `verify()` 也不知道哪些路径是必需项。
- 离线 verifier 会验证 manifest 已列条目的哈希，但必需文件若完全未登记就不会进入循环。
- 视频只重新运行 ffprobe；首中末帧、FFmpeg exit 和 first-packet growth 仍取信产品 `video_manifest.verified`，形成证据自引用。
- action ledger 只在 reserve 时检查终态，预先 reserve 的 action 仍能在 terminal decision 后 consume；decision evidence 也未验证是否属于当前 run。

### 修法与教训

- 用显式核心 artifact 集合同时驱动产品 finalization 和独立 verifier；缺少 manifest entry、`complete=false`、文件缺失或哈希漂移都设置 `artifact_failure`，然后才重新计算正式成功。
- 所有 action 消费、runtime/task/skill 工具先查共享终态。decision evidence 只能引用当前 run 先前持久化的 event/evidence；environment、normalizer 和离线 verifier 分别拒绝未知引用。
- 离线视频门独立检查 FFmpeg 退出、first-packet 样本增长、视频 SHA-256 和 ffprobe，并直接从 MP4 解码 raw RGB 首中末帧计算非黑比例、方差和首末变化。
- “独立 verifier”必须从原始字节和外部进程重建结论，不能换一个函数再次读取产品布尔值。必需集合必须检查缺项，而不只是检查现存项。

### 参考

- `apps/case02_openenv/src/case02_openenv/artifacts.py`
- `apps/case02_openenv/src/case02_openenv/evaluation/scoring.py`
- `apps/case02_openenv/src/case02_openenv/episode_store.py`
- `scripts/coworker_demo/verify_run_bundle.py`
- `tests/case02_openenv/test_{artifacts,episode_store,scoring,independent_bundle_verifier}.py`

## 2026-07-16 - 最终业务成功掩盖必需轨迹节点错序或缺失

严重程度：高。真实模型可以把配置正确写入、业务验证成功并得到 result 100，同时跳过 planner、implementation gate、exact job wait，或在依赖完成前写 progress；若只验最终文件和模型自报，会把不可审计流程误当成正式成功。

### 症状

- 一个真实 normal run 达到 result 100，但因 `PLAN_CREATED` 晚于 prechecks，trajectory 只有 12.5。
- 另一个 run 在确认页面结果后没有调用 `browser_wait`；缺失 `ADD_WAIT` 让 `ADD_GREP` 及后续节点按 DAG 级联失配，trajectory 只有 45.8。
- 模型在只完成 post alarm 后过早写入 `NORMAL_PROGRESS`。即使后来补齐四项检查并再次调用 progress，首个错序有效动作仍不能被事后覆盖。

### 根因链

1. Prompt 能说明流程，但不能保证模型实际选择对应工具或顺序。
2. 业务状态机只限制最终 mutation，没有把 planner/progress/wait 当作后继动作的外部前置条件。
3. 允许错序 runtime event 先进入 append-only audit 后，后续正确事件无法合法改写历史。

### 修法与教训

- ticket 首次读取后，除只读 observe 外的操作必须先看到真实 `task_planner -> PLAN_CREATED`。
- precheck/implementation proceed 后，下一项浏览器动作必须先看到阶段对应 progress；add grep 后若缺 implementation decision，直接返回准确恢复指令。
- terminal 启动前要求最新 add/remove job 的 exact `browser_wait`；`NORMAL_PROGRESS` 写入前要求五项 postcheck 与最新 business wait；`ROLLBACK_PROGRESS` 写入前要求 rollback grep。
- 无效或错序节点在 append 前拒绝，不能由 evaluator 合成、重排、选择后一个候选或倒填。正式成功同时要求 trajectory/result 100、视频与 artifact 门，而不是只看业务终态。

### 参考

- `apps/case02_openenv/src/case02_openenv/episode_store.py`
- `apps/case02_openenv/src/case02_openenv/terminal/executor.py`
- `tests/case02_openenv/test_episode_store.py`
- `var/coworker-demo/coworker-20260716-154711-853f071d/`（修复后 normal PASS）
- `var/coworker-demo/coworker-20260716-160128-c4f0faa9/`（修复后 anomaly PASS）

## 2026-07-16 - 解引用 venv Python symlink 让子服务丢失依赖环境

严重程度：高。父进程测试和 import 全部通过，但启动的 FastAPI 子服务使用基础解释器，运行时才报依赖缺失，容易被误判为 lock 或安装损坏。

### 症状与根因

- 配置指向 `apps/case02_openenv/.venv/bin/python`，路径校验后却变成 uv 管理的基础 Python。
- `Path.resolve()` 跟随 venv 中的解释器 symlink；以解引用后的路径启动进程时，Python 不再发现该 venv 的 `site-packages`。

### 修法与教训

- 可执行路径只使用 `expanduser()` 和 `absolute()` 做定位，保留 venv symlink 身份；数据根等普通路径仍可用 `resolve()` 做 containment。
- preflight 同时验证配置权限、解释器文件与子服务 health。父进程能 import 不能证明子进程解释器正确。

### 参考

- `src/homemaster/benchmarking/coworker_demo/config.py`
- `src/homemaster/benchmarking/coworker_demo/environment_client.py`
- `scripts/coworker_demo/preflight.py`

## 2026-07-16 - FFmpeg 编码进度把未落盘视频误判为首 packet 就绪

严重程度：高。该假阳性会让 Agent 在录屏文件尚不可恢复时开始调用模型；若进程随后异常退出，内部已有几十帧 `frame` trace，但交付 MP4 仍可能只有 28-byte header。

### 症状

- 首次 x11grab linchpin 返回 `pass=true`，FFmpeg progress 已到 `frame=58`，最终 ffprobe 和三帧检查也通过。
- 逐采样复查却发现录制期间 `demo.mp4` 始终只有 28 bytes，直到发送 `q` 正常收尾后才一次性增长。
- 因此“编码器处理了帧”和“fragmented MP4 已在外部文件系统写入可增长 packet”并不是同一个终态。

### 根因链

1. first-packet gate 只要求 `frame >= 1`、`total_size > 0` 和文件非空；28-byte container header 也满足“非空”。
2. x264 默认 GOP 很长，fragmented MP4 只在后续 keyframe/收尾时刷出媒体 fragment。
3. 最终正常收尾让视频可播放，反过来掩盖了模型调用前的 readiness gate 实际未成立。

### 修法与教训

- RED-test 固定 `[0, 28, 28, 28]` 必须失败，只有观察到 header 后又出现更大的正向文件大小才可通过。
- 编码命令锁定 `-g 15 -keyint_min 15 -sc_threshold 0`，并同时要求 FFmpeg progress 的 `total_size > 28` 与宿主文件至少两个不同的正值大小。
- final gate 仍独立要求 FFmpeg exit 0、ffprobe H.264/1920x1080/yuv420p/时长/帧数，以及首中末每帧区域检查；first-packet 与 final-video 是两道不同的门。

### 参考

- `scripts/coworker_demo/linchpin_recording.py`
- `tests/case02_openenv/test_linchpin_helpers.py`
- `var/coworker-demo/linchpin/recording/run-001/video_manifest.json`（假阳性证据）
- `var/coworker-demo/linchpin/recording/run-002/video_manifest.json`（修复后真环境 PASS）
## 2026-07-16 - Synthetic fixture 手工补出了真实 producer 永远不会发布的容器 oracle

严重程度：高。三份 helper 的内部测试全部通过，却无法让同一 schema 的真实生产路径生成 post-Open case，直到第三次真实 discovery run 才暴露。

### 症状

- synthetic post-Open fixture 同时提供 hidden child oracle 和 closed-container oracle，因此 probe、controller、verifier 的 child-parent/snapshot binding 测试全部 GREEN。
- 真实 `discovery-run-006` 中两个 hidden CreditCard 有唯一 Drawer parent，Drawer snapshot 也为 `ok`，但 persisted visibility oracle 完全没有 Drawer 行，最终仍报告 `closed-child transition lacks its target-independent parent sequence`。
- 修复 child binding 后重放同一 run 仍失败，说明第一层修复必要但没有触及真正的 producer coverage 缺口。

### 根因

`_build_visibility_case_oracles()` 只按 trial requested physical types 产出行；target-independent post-Open sequence 还会使用该 trial 未请求的公共关闭容器。Synthetic fixture 手工补了这些容器行，相当于测试了一个真实 producer 无法生成的世界。三个 consumer 对同一手工数据达成一致只是内部回声。

### 修法与教训

- 统一真实覆盖规则为 trial-required physical exact IDs 与 frozen public closed-container exact IDs 的并集；probe 从 reset event 生成，controller/verifier 各自从 persisted raw reset event 重算。
- 从不可变 run-006 的 29 组 raw event/RGB/PNG artifact 重建 oracle，而不是复用旧 artifact。重建从 6 行增加到 15 行，产生 9 个 post-Open cases，controller 与完整 independent verifier 同时通过。
- 共享 schema 的 synthetic fixture 必须由真实 producer 生成，或至少用 producer 的实际 coverage rule 做逐 ID 审计。不得手工补出真实生产路径不会发布的行或字段。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-006`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-16 - 打开真实父容器被错误当成子物体必然进入当前画面

严重程度：高。这个假设同时进入 probe、controller 和 independent verifier，内部 schema/self-test 可以互相一致却共同接受错误的外部终态。

### 症状

- post-Open case 预先用 `opened_container_id == child_parent_id` 决定子物体会可见；打开真实父容器固定走 child lookup/navigation，打开其他容器固定走 `target_not_visible`。
- 真实引擎只保证 Open 的返回状态，不保证容器内物体进入当前相机画面。真实父容器打开后子物体仍可能无正 bbox；其他容器视角也可能碰巧已经看到子物体。
- controller 还把 container 授权 binding 与 child oracle 混在普通 requested-target 分支比较；verifier 从共享 manifest 假设校验结果，没有独立从 raw Open event 决定 outcome。

### 根因

把两个独立事实合并成一个推论：reset containment 回答“打开的容器与 child 是什么关系”，Open return event 才回答“child 此刻是否在模型看到的画面中”。关系正确不等于当前可见。三个 helper 复用了同一推论，同源验证只能形成内部回声。

### 修法与教训

- container relation 只决定 case kind，不能决定 child navigation 结果。公开三调用序列在读取 containment 前锁定，relation 后补且不得改变调用字节。
- manifest 同时冻结 `ok` 与 `target_not_visible` 两个 outcome envelope；worker 只用 Open 返回 event 的 exact `visible=true` 加正 bbox 选择一个。
- child 不可见时，child snapshot lookup、parent resolution、context creation 和 navigation 必须全为 0；可见时才允许用独立冻结的 unique-parent pose 执行一次 child navigation。
- independent verifier 分别从 reset raw containment 和 Open raw event 重算 relation/visibility，并要求真实 run 同时出现两种 outcome。不要用关系、动作意图或 worker 自报字段替代相机终态。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-15 - Direct snapshot 已选为正确 anchor，但旧 parent 错误先被追加

严重程度：高。`discovery-run-005` 的第二个真实 worker 完成 24 次 setup 且生成了正确 direct-pose分类，仍因一条过期 issue 退出 2。

### 症状

- 目标 Statue strict-visible，snapshot row 为 `ok/geometry`，visibility oracle 明确把 `execution_anchor_exact_id` 设为 Statue 自身。
- 它位于没有 Oracle row 的 Dresser 表面。最终 surface 与派生 inventory classification 的 `anchor_exact_id` 都正确等于 Statue。
- worker 仍保留 `strict-visible surface movable has no unique Oracle anchor`，result status FAIL；controller 在第 2/20 trial 停止。

### 根因

分类器先从 parent Dresser 计算 `anchor_id=None` 并立即追加错误，随后才执行“若 exact target 自身有 direct snapshot，则 anchor=exact target”。最终数据正确，但早先追加的 issue 没有撤销。错误判断观察的是中间值，不是最终锁定值。

### 修法与教训

- 按决策优先级先锁定 direct snapshot anchor，再对最终 anchor 做缺失判断。无 direct snapshot 的 surface/open-container 仍要求唯一 parent anchor，边界没有放宽。
- 增加含无 Oracle Dresser parent 的 strict-visible pickupable synthetic case，要求 direct anchor 且 issues 为空。
- 用 run-005 的真实 restored metadata、snapshot 与 visibility fixtures 离线重跑完整 `discover_cases()`；修复后得到 31 cases、空 issues、Statue 自身 anchor。
- 任何错误/terminal 判定都必须在所有更高优先级正常决策完成后基于最终锁定状态执行；不得把中间候选缺失永久写入结果。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-005`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`

## 2026-07-15 - Missing ordinal 合成 ID 被 controller 当成真实对象查 oracle

严重程度：高。全部 helper 自测和真实 THOR setup 都通过后，controller 在冻结 manifest 时拒绝第一个 trial，导致真实 Gate A 无法继续。

### 症状

- `discovery-run-004` 第一 worker 进程 exit 0、cleanup complete、status PASS，完成 27 次 setup、生成 23 cases，tested/model action 都是 0。
- controller 却报告 case `adb3e7cb... has no frozen visibility oracle`，没有生成 `exact-cases-v3.json`。
- 唯一缺 oracle 的 case 是 `grounding_ordinal_missing`：真实场景有两个 FloorLamp，测试 `FloorLamp 3` 使用 Gate-only sentinel 表达“序号不存在”。

### 根因

probe 和 independent verifier 都把 missing ordinal 定义为合成的不存在 ID，并用 `(trial_id, missing_exact_id, object_type, ordinal_index)` 闭式推导 pair/snapshot-not-applicable/freshness-not-applicable hash。controller 的真实 `validate_v3_discovery_result()` 却在任何 case-kind 分流前无条件执行 `visibility_by_id[requested_exact_id]`，要求合成 sentinel 拥有真实 oracle。controller self-test 只验 case schema，没有把 missing case 送进实际 discovery binding 循环。

### 修法与教训

- 在真实 controller 入口中先验证共同 snapshot binding，再让 missing ordinal 在任何 oracle lookup 前走独立闭式推导并 `continue`；普通 case 仍强制真实 oracle。
- 对 sentinel 同时要求：ordinal 恰为冻结集合长度、精确规范名称、在 snapshot/oracle 中都不存在、三个派生 hash 与 authorization binding 全部一致。
- 增加 actual-entrypoint 顺序门、正确缺失 PASS、binding 漂移 FAIL、sentinel 出现真实 oracle FAIL。
- 修复后先离线重放不可变 run-004 的完整 raw artifact 树；只有真实 23-case result 通过同一 validator，才启动新 THOR run。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-004`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-15 - 临时目录 Ruff 假绿且多命令 SSH 被末尾成功掩盖

严重程度：高。若未在正式仓库路径复跑，Gate A 会带着项目 lint/format 失败进入真实 THOR；组合命令还会错误报告进程 exit 0。

### 症状

- 三个 helper 在 `hkust4:/tmp` 下执行 Ruff 时报告 lint/format PASS，同一哈希复制到正式仓库后，项目 Ruff 报两个 `B023`、一个 `E501`，并要求格式化全部三文件。
- 正式 SSH 命令最后继续运行了三个成功的 self-test，整体返回码因此是 0，尽管前面的 Ruff 已明确失败。

### 根因

Ruff 从当前目录向上发现配置。隔离 `/tmp` 不在 HomeMaster 仓库树内，且命令没有显式 `--config`，所以使用了默认规则和默认 formatter，而非 `pyproject.toml` 的 100 列与 `E/F/I/UP/B` 规则。正式组合脚本又没有 `set -e`，只把最后一条命令的状态作为总返回码。

### 修法与教训

- 在仓库外 lint/format 时始终显式传 `--config /data1/haodong2/weilin/red_bird/Homemaster/pyproject.toml`；同步后仍在正式仓库路径复跑同一门。
- 多命令验收用 `set -e`，或逐条记录并断言每个返回码。不得用组合 shell 的最终 exit code代替每个子门的状态。
- 修复闭包变量绑定和超长行后，使用项目配置机械格式化三文件，并重新执行 compile、lint、format-check 和全部 self-test。

### 参考

- `pyproject.toml`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`

## 2026-07-15 - V2 自测全绿但真实 consumer 仍走 V1 路径或依赖已删除字段

严重程度：高。若直接同步运行，case worker 会绕过 fresh reset snapshot；独立 verifier 只核对 worker 自报计数，存在完整假阳性风险。

### 症状

- verifier 的 10 个 v2 mutation 全部正确拒绝，controller 的 12 项自测也通过。
- 但 AST 读取真实入口后发现：`case_main()` 仍调用 `oracle_lookup_twice()`，run CLI 没有 matrix 输入；`verify_case_bundle()` 没有回读每个 case 的 scan/restore/snapshot artifacts。
- 修完入口接线后，`discovery-run-002` 又证明共享 schema 仍是假绿：`matrix-v2.json` 已删除旧 `discovery_contract`，真实 `discover_cases()` 却继续读取它；transaction 已完成 27 次 setup action，generic failure result 仍把 controller 汇总计数降成 0。

### 根因

自测只覆盖新写的纯校验函数和 synthetic discovery fixture，没有证明 CLI/handler 实际调用这些函数。controller 又从 case result 读取三个计数字段，导致“新函数存在且自测绿”被误当成“真实 run 已接线”。

三个 helper 的 synthetic schema 还各自补出了 production payload 已删除的字段，没有把同一份 committed matrix 直接喂给真实 consumer。异常边界只保留最小错误，混淆了“外部 transaction 已完成”和“后续 case 派生失败”。

### 修法与教训

- 对 helper 增加真实入口 AST/call-graph audit：case handler 必须调用 fresh transaction 和 snapshot lookup，禁止旧 map lookup；run verifier 必须调用 per-case setup artifact verifier。
- 每个 case 独立重跑 reset/query/完整 scan/restore，从 verified restore event 开始测试动作；verifier 从 raw refs 重算 policy、plan、snapshot、restore、witness 和计数。
- 增加正反 artifact 门：完整 synthetic case 为零失败，篡改 snapshot 必须失败。isolated function self-test 不能替代 actual handler verification。
- v2 consumer 统一从 `public_semantic_vocabulary` 确定性派生固定 contract，并拒绝重新出现的 legacy 字段；mutation test 必须核对目标拒绝原因，不能因无关缺字段异常假绿。
- transaction 完成后的下游失败仍写出 policy/plan/snapshot/restore refs、逐动作 rows 和真实计数。`discovery-run-003` 证明同一 27-action transaction 不再被汇总成 0。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`
- `var/alfworld-evidence/20260713-v18-gate-a/run_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/verify_gate_a.py`
- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-002`
- `var/alfworld-evidence/20260713-v18-gate-a/discovery-run-003`

## 2026-07-15 - 真环境检测框通过动作门却在证据序列化阶段失败

严重程度：中。该问题让最小 Oracle smoke 的 `reset` 和 `TeleportFull` 都成功后仍无结果 JSON、进程退出 1；Player.log 恰好又在 teardown 记录异常，容易把根因错归给 Unity 移动。

### 症状

- 阶段日志显示 reset 成功，`TeleportFull.lastActionSuccess=true`，requested/actual pose 一致；但没有 `after_result` 标记和预期 JSON。
- Python stderr 最终为 `TypeError: Object of type ndarray is not JSON serializable`。
- Player.log 末尾同时出现 `ArgumentNullException(name)`，但其修改时间严格位于 `before_close` 与 `after_close` 之间。

### 根因

`event.instance_detections2D[exact_id]` 在当前 ALFWorld/ai2thor 真环境中是 NumPy ndarray。旧 smoke 的 bbox 面积函数只接受 list/tuple，所以先把有效检测框误判为不可用；随后又把原 ndarray 直接交给 `json.dumps()`，导致结果序列化失败。独立的 ai2thor 2.1.0 close 路径向 Unity 发送空 control payload，产生 teardown 日志，但不是 reset/move 失败。

### 修法与教训

- 在证据边界先把 ndarray/NumPy scalar 确定性投影为 JSON-safe list/number，再对同一投影检查长度、有限值和正面积；不能只给 JSON encoder 加兜底，否则几何门仍会假失败。
- 给真环境 probe 加阶段标记并同时保存 Python exit/stdout/stderr；按时间窗区分动作、结果构造和 teardown，不能从 Player.log 最后一条异常倒推动作失败。
- teardown anomaly 单独记录。只有外部动作返回码、准确终态、结果 artifact 和进程退出码分别通过时才接受 case；不得泛化忽略其他 Player.log 异常。

### 参考

- `var/alfworld-evidence/20260713-v18-gate-a/smoke-root-cause/gate_a_diag_stderr.log`
- `var/alfworld-evidence/20260713-v18-gate-a/smoke-root-cause/gate_a_diag_normalized_stdout.log`
- `var/alfworld-evidence/20260713-v18-gate-a/oracle_runtime_feasibility_probe.py`

## 2026-07-12 - ALFWorld Harness 把内部执行回声当成外部成功

严重程度：高。该问题曾让含 Harness 执行失败的 Episode 进入 Agent 评分，并让 `9/10` 的汇总结果无法直接解释为模型能力。

### 症状

- 模型正确选择并执行 `put(pencil 1, shelf 1)`，但 Harness 先把仅有 2D detection、准确对象 `metadata.visible=false` 的姿态报告成 `Reached shelf 1`，随后只尝试一次 `PutObject`。
- THOR 明确返回失败，Pencil 仍在 inventory，goal 仍为 `0/1`；模型却只收到 `{"success": false, "error": "action_failed"}`，无法判断对象是否仍被持有或失败属于模型、Harness 还是引擎。
- `robot_inspect_view` 重复返回同一图片。Episode 最终耗尽 50 个环境步骤并累计 37 次 invalid action，掩盖了最初的 Harness 失败。
- 修复期间又发现一个相反方向的假设：携带物体成功移动时，THOR 可能随空间重叠更新该物体的 `parentReceptacles` 和 Shelf 的 `receptacleObjectIds`。若要求完整父子集合不变，真实成功的移动会被误判为 `execution_state_uncertain`。

### 根因链

1. 导航把“画面中存在检测框”误当成“准确目标已达到严格观察/交互姿态”，允许 detection 覆盖准确对象的 `metadata.visible=false`。
2. 目标标签在工具层和 Adapter 层重复解析；显式实例 miss 还可能被去掉编号后退化成类型级匹配，导致锁定的语义目标漂移。
3. `put` 没有复用导航成功 event 创建的局部 `PoseContext`，只在当前姿态调用一次 THOR，也没有用准确 inventory 与父子归属证明终态。
4. 内部 trace 虽记录 inventory、THOR error 和 goal，模型投影却把信息压成 `action_failed`；无新观察的 inspect 又制造了“已经复查”的假象。
5. Runner 没有独立的 Harness terminal/score eligibility 控制面，于是低层执行失败被累计为模型 invalid action 并进入 Agent 分数。
6. 第一版移动门把“完整动作状态不变”同时用于成功和失败移动，没有先在真环境核对派生父子字段的移动语义；同源 mock 无法揭示这个假设错误。

### 为什么单测和 trace 会假绿

- mock event、分类器断言和设计文字可以共享同一个错误假设；三者一致只是内部自洽，不是独立证据。
- `lastActionSuccess`、`Reached ...`、`action_failed` 或一条 `put_result` 日志只证明代码走到某处，不能证明准确对象的外部终态发生了预期变化。
- 2D bbox 证明目标出现在渲染中，不证明准确对象 `metadata.visible=true`，更不证明后续 `PutObject` 可用。
- 内部 trace 中存在丰富状态，不代表模型实际收到了这些字段；历史 `model_trace.jsonl` 只保留了通用错误和旧图片。
- 按多个 Shelf 的 best/any 结果验收会让一个可成功实例遮住其他实例失败。候选预算和终态必须逐实例断言。

### 修法与教训

- 每个外部动作同时核对外部返回状态和独立读取的真实终态。导航成功要求同一 event 的 `TeleportFull` 成功、requested/actual pose 一致、准确对象 `visible=true`、准确对象正面积 bbox 和可保存且像素一致的 RGB frame。
- Put 成功要求 `PutObject.lastActionSuccess=true`、准确 Pencil 离开完整 inventory、`isPickedUp=false`、准确 Shelf 属于 Pencil parent membership、Pencil 属于准确 Shelf child membership；真环境验收再独立要求 goal `1/1`。任何返回/终态矛盾都立即停止为 `execution_state_uncertain`。
- 成功携物移动只锁定 held ID、完整 inventory、准确对象仍存在、`isPickedUp=true` 并核对实际 pose；不得要求 parent/child 集合不变。失败移动只有在完整动作状态和 pose 都不变时才能继续；失败 Put 只有在完整动作状态不变时才能换下一个候选。
- 准确 objectId、目标 objectId、候选集合、顺序和 hash 在一次调用开始时锁定。重试只幂等执行锁定候选，不重新解析实例、不重算漂移目标。
- 所有发给 THOR 的请求都计入 backend action，包括 `GetReachablePositions` 这类 query；每次请求前检查固定候选数、backend action 数和 wall-clock 三预算，禁止 N+1 请求。
- 用不 import 产品 resolver、候选生成器或分类器的真环境 probe 做正交黑盒门，并对每个 Shelf 独立 reset、执行和断言。`shelf-characterization-v3` 的 Shelf 1-6 均通过；产品 Harness 又对 Shelf 3/4/6 分别证明返回成功、准确外部放置终态和 goal `1/1`。

### 参考

- `docs/record/2026-07-10-alfworld-harness-execution-feedback-issue.md`
- `plan/V1.7/alfworld-navigation-local-pose-execution-spec.md`
- `plan/V1.7/alfworld-put-local-pose-feedback-evaluation-spec.md`
- `src/homemaster/benchmarking/alfworld/env_adapter.py`
- `src/homemaster/benchmarking/alfworld/execution.py`
- `var/alfworld-evidence/20260712-preimplementation/shelf-characterization-v3/summary.json`
- `var/alfworld-evidence/20260712-preimplementation/product-harness-v2/shelf-{3,4,6}/result.json`
