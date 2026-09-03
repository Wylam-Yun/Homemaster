# HomeMaster V3.2 阶段一（R0–R2）实施计划

## Summary

阶段一把 HomeMaster 收敛成可在干净 Ubuntu 22.04 x86_64 环境中在线或离线复现的通用 Agent。记忆始终启用，Neo4j 始终由 HomeMaster 本地管理，浏览器是唯一可选重量能力。

本阶段不创建 `ApplicationProviderRegistry`、不迁移 ALFWorld，也不修改 `/home/haodong2/weilin/red_bird/alfworld`。完成 R2 全部门禁后才能进入阶段二。

## 公开契约与锁定决策

- 唯一安装入口：
  - `./scripts/setup.sh`
  - `./scripts/setup.sh --with-browser`
  - `./scripts/setup.sh --offline /path/to/bundle`
  - `./scripts/setup.sh --offline /path/to/bundle --with-browser`
- Python 使用锁定的 uv `0.12.9` 管理精确 Python `3.11.15`；不使用系统 Python，不复用旧 `.venv`。
- Neo4j 只允许 `managed_local`：
  - 保留现有 `mode: managed_local` 字段以免升级旧配置；
  - schema 只接受该值，不存在 external 分支；
  - URI 仅允许 loopback，端口可配置；
  - HomeMaster 固定管理 Temurin 21.0.11 和 Neo4j 2026.05.0。
- `memory.enabled` 只能省略或为 `true`，`false` 在配置加载时拒绝。
- 用户级布局：
  - 可重建资产：`${XDG_CACHE_HOME:-~/.cache}/homemaster/assets/`
  - 正式数据：`${XDG_DATA_HOME:-~/.local/share}/homemaster/data/`
  - checkout `.venv`：仅保存项目 Python 依赖；
  - checkout `.runtime`：只保存指向本机资产和数据的绑定、setup 状态及环境 identity。
- setup 首次从 `config/homemaster.example.yaml` 原子生成 `config/homemaster.yaml`，权限 `0600`，生成随机 Neo4j 密码；已有配置绝不覆盖。
- `--with-browser` 是单向附加安装：安装过 browser 后，普通 setup 重跑不得卸载或切换 Chromium revision。
- 新增离线包生成入口：
  - `./scripts/build_runtime_bundle.sh --output PATH`
  - `./scripts/build_runtime_bundle.sh --output PATH --with-browser`

## Implementation

### R0：保护基线与修订规范

1. 在 `plan/V3.2` 中把 Neo4j 产品边界改为“仅 HomeMaster-managed local”，删除 external 模式的设计、命令和验收分支。
2. 记录当前 `main@cfd74fb6`、9 个用户删除的 `plan/*.md` 和当前 ignored runtime 状态；后续每次提交前逐项核对这些删除仍未被暂存或夹带。
3. 禁止使用 `git add -u`、`git add .`、无边界 `git clean`；所有提交显式列出文件。
4. 运行并保存现有非 live 基线、Ruff、compileall、cleanup guard 和 `git diff --check` 到 ignored 的 `var/v3.2/r0/`。
5. 更新 CHANGELOG，并以相同内容提交规范修订；这是 R0 唯一提交。

### R1：安全、配置和可复现安装

#### 1. Secret 与 doctor

- `ProviderProfileConfig.public_summary()` 删除 `api_keys`，改为：
  - `api_keys_configured: bool`
  - `api_key_count: int`
- doctor 的 provider、Feishu、MCP、Neo4j 输出只报告配置状态，不输出 secret 值。
- 删除旧 Mac Python 建议和已失效的 `bm25s` 检查；fastembed、spaCy、embedding 检查只在对应必选运行路径确实使用时保留。
- 测试使用多个唯一 sentinel secret，分别扫描 stdout、stderr 和 JSON payload，任何原值出现都失败。
- doctor 必须保持单一 machine-readable JSON stdout，诊断只进入 stderr，返回码与必选检查状态一致。

#### 2. 本地记忆契约

- `MemoryNeo4jConfig.mode` 收窄为固定 `managed_local`；非 loopback URI和任何 `external` 值明确拒绝。
- `ManagedNeo4jRuntime` 无条件参与正常 application composition，不再存在“mode 判断后跳过启动”的分支。
- Neo4j 程序目录保持不可变；可写 conf、data、logs、runtime 和 owner/lease 文件放入用户数据根。
- 启动时显式传递 `JAVA_HOME`、`NEO4J_HOME` 和独立 `NEO4J_CONF`，绝不读取系统 Java。
- 已存在且属于当前 HomeMaster 的进程允许幂等复用；未知进程占用 Bolt 端口时 fail closed，不杀进程。
- 配置和测试模板默认 `managed_local`；现有 hkust4 配置无需迁移或重写。

#### 3. Setup 组件

新增小型、单一职责组件：

- `scripts/setup.sh`：平台检查、锁定 uv bootstrap、参数解析、调用 Python orchestrator。
- `scripts/runtime_setup/assets.py`：锁文件读取、下载、SHA256、原子解包和内容寻址缓存。
- `scripts/runtime_setup/configuration.py`：配置原子创建、权限、随机密码、已有配置验证。
- `scripts/runtime_setup/runtime.py`：`.venv`、`.runtime` 绑定、Neo4j 初始化、幂等状态。
- `scripts/runtime_setup/probes.py`：Java、Neo4j、Qdrant、browser 和 identity 黑盒探针。
- `config/runtime-assets.lock.json`：平台、uv、Python、Temurin、Neo4j、Playwright/Chromium 的版本、官方不可变 URL、SHA256 和许可证身份。

执行顺序固定为：

1. 验证 Linux/x86_64/glibc、磁盘、目录权限和端口。
2. 获取并校验 uv 0.12.9。
3. 使用 uv managed Python 3.11.15 创建 checkout `.venv`。
4. 执行 `uv sync --frozen`；browser 仅在显式请求或已有 browser 状态时加入。
5. 原子创建或验证真实配置。
6. 下载并校验 JDK、Neo4j；创建用户级数据目录和 checkout bindings。
7. 首次数据库初始化并设置随机密码；已有 data 绝不重置。
8. 启动 Neo4j并验证 Bolt、认证、数据库和服务 identity。
9. 如带 browser，安装锁定 Chromium并真实渲染本地 fixture。
10. doctor 全绿后才原子发布 `.runtime/environment-identity.json` 和成功状态。

所有 setup 决策写入 secret-safe JSONL；任一步失败不得留下成功 identity。并发 setup 使用明确 lock，同目标重复运行保持配置、密码、数据路径和 asset digest 不变。

#### 4. Browser 可选能力

- 基础 sync 不安装 Playwright，不生成 Chromium 资产目录。
- 未安装 browser 时 Web Console仍能启动；browser 工具返回结构化 `capability_unavailable`，不得触发隐式下载。
- `--with-browser` 安装锁定 Python extra 和 Chromium revision，启动真实 Chromium读取本地确定文本后关闭。
- browser 状态写入 setup state；后续普通 setup 保留已安装能力。

#### 5. 硬编码、垃圾和发布物

- environment identity 接收调用者提供的 host label，不判断 hkust4/HPC2。
- `success_path.py` 的失败证据写入 mode-0700、run-unique 目录，正常路径不依赖该文件。
- 对 `build/`、旧 `dist/`、egg-info、缓存和 `web/node_modules/` 逐个记录路径及体积后清理；不触碰 `var/`、trace、历史计划和用户数据。
- wheel 采用明确 allowlist：
  - 保留 Web `static_dist`、生成的 OpenCLI runtime JS、许可证和来源证明；
  - 排除完整 OpenCLI `node_modules`、测试 fixture、plan、trace、`.runtime`、`.venv`、ALFWorld 和现有数据。
- 解包 wheel 后逐项检查文件名、类型、总大小、最大文件、依赖 metadata 和 ALFWorld 缺失状态。
- 每个独立改动都先更新 CHANGELOG，再用相同内容提交；建议拆为 secret/config、setup、browser、package-hygiene 四个提交。

### R2：在线、离线和双机验收

#### 1. 离线 bundle

- bundle 从确定 commit 构建，包含源码归档、锁定 uv、uv managed Python artifact、完整 frozen Python cache、JDK、Neo4j、许可证及 SHA256 manifest。
- browser bundle额外包含锁定 Chromium和运行库清单。
- bundle 禁止包含真实配置、secret、`.venv`、`.runtime`、memory/Neo4j data、ALFWorld、AI2-THOR、dataset 或 trace。
- setup 使用 offline bundle 时只访问 bundle；缺失、额外或 digest 不符均 fail closed并列出准确 artifact。
- 网络隔离验收必须使用真实 network namespace或等价防联网手段，不能只设置空代理冒充离线。

#### 2. 每台机器的验收矩阵

在 hkust4 和 HPC2_Outside 上分别使用四个全新临时 checkout/data/cache root：

| Gate | 安装方式 | Browser |
|---|---|---|
| A | 在线 | 无 |
| B | 在线 | 有 |
| C | 离线、禁网 | 无 |
| D | 离线、禁网 | 有 |

每个 gate 单独断言：

- setup 返回码为 0，stderr 无 traceback。
- Python 3.11.15、HomeMaster import、CLI help、lock hash和 identity一致。
- Java 21.0.11 启动 Neo4j 2026.05.0，Bolt返回成功。
- Neo4j 唯一节点写入、独立查询、删除和删除后查询逐步成功。
- Qdrant唯一 collection 写入向量、独立查询和清理逐步成功。
- 使用主机本地、mode-0600 的凭据注入完成一次真实 memory写入，并在下一 Session召回准确内容；凭据不进入 artifact。
- Web Console监听动态 loopback端口并返回页面。
- 基础 gate 不存在 Chromium资产，通用 Agent仍可运行。
- browser gate真实启动 Chromium、读取 fixture，并由 HomeMaster browser工具完成导航和 DOM终态读回。
- 每个 gate结束后分别核对 Neo4j进程、监听端口、browser进程和 setup lock；不得用全局 any/best判定。
- 两台机器的结果分别出具 PASS/FAIL；任何一台失败都阻止 R2完成。

HPC2_Outside 当前从 hkust4 无法解析，因此 R2在连接恢复并完成同等黑盒门前必须保持未完成，不能用 hkust4结果替代。

#### 3. 最终回归命令

- 基础环境：
  - focused config/doctor/setup/package tests；
  - 全部 non-live、non-stress 测试；
  - Ruff、compileall、cleanup guard、`git diff --check`。
- Browser 环境：
  - browser focused tests；
  -真实 Playwright/Chromium集成门。
- 构建环境：
  - wheel构建、解包 allowlist、源码外空 venv安装与 import。
- 安全门：
  - Git tracked files、wheel、bundle、doctor stdout/stderr和 identity 中扫描所有 sentinel secret及禁止路径。
- 所有正式测试文件必须记录 pytest collected集合，不能以相邻测试或汇总计数替代。

## 文档与完成条件

同步更新 README、环境用户指南、memory架构、Web/browser指南、CHANGELOG、`docs/session-handoff.md`。若实施中发现“单测绿但真实 setup/外部终态失败”的新坑，在 `docs/pitfalls.md` 顶部记录症状、根因、修法和引用，并向 `CLAUDE.md` 提炼正向规则。

阶段一仅在以下条件同时满足时完成：

- R0用户改动零污染；
- 所有 secret、安全、配置、安装和发布门通过；
- hkust4与HPC2_Outside的 A–D 四组 gate逐实例通过；
- 最终文档与代码一致；
- HomeMaster基础及离线 bundle均不包含 ALFWorld；
- 没有开始任何 Application Registry或 ALFWorld迁移工作。
