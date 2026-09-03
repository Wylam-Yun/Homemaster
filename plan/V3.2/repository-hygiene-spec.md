# HomeMaster V3.2 仓库清理与环境复现 Spec

状态：待书面审阅
日期：2026-09-02
前置条件：仅在 `main` 上实施

## 1. 目标

本阶段的目标不是追求目录看起来整洁，而是建立一份可以执行、可以验收的环境复现契约：

1. 通用 HomeMaster 必装完整记忆系统。
2. 浏览器作为唯一可选的重量级能力独立安装。
3. 在线机器和无外部互联网机器都能从干净 checkout 重建环境。
4. 项目不依赖 hkust4、HPC2_Outside 或个人 Mac 的绝对路径。
5. 清除可重建垃圾、失效检查、凭据泄露和发布包污染。
6. 所有删除都有引用证据，不能凭文件名或年代猜测代码无用。

## 2. 当前证据

### 2.1 机器路径不是主要生产代码问题

当前明确的跟踪文件问题包括：

- `src/homemaster/cli/doctor.py` 含旧 Mac Python 路径建议。
- `scripts/v19_release/capture_environment_identity.py` 含 `hkust4`/`hpc2` 主机假设。
- `src/homemaster/experience/success_path.py` 使用固定的
  `/tmp/homemaster-explore-30c9-<phase>.invalid.json` 调试路径。

大量其他绝对路径来自运行态或历史记录，而不是生产源码：

- `.venv/bin/*` 的 shebang 和 `.venv/pyvenv.cfg`。
- `.runtime/*` 的本机软链。
- Playwright 的用户缓存目录。
- `plan/`、report、trace 和迁移 journal 中记录的历史事实。

清理必须区分“生产硬编码”和“运行证据中出现过绝对路径”，禁止全仓库盲目替换。

### 2.2 当前环境不可直接复制

- `.venv` 约 983 MB，解释器指向另一处 conda 环境，不能作为可搬迁环境。
- `.runtime` 将 Java、Neo4j 和 memory 指向本机目录，换服务器后会成为死链。
- 当前 Playwright Chromium 位于用户缓存，不在 Git checkout 中。
- 当前 JDK 是 Temurin 21.0.11，约 346 MB。
- 当前 Neo4j 是 2026.05.0；目录约 770 MB，其中历史数据库数据约 518 MB。
- Neo4j 2026.05.0 接受 Java 21 或 25。hkust4 系统默认 Java 11，若不显式设置
  `JAVA_HOME`，Neo4j 会拒绝启动。

### 2.3 当前发布污染

- `web/node_modules`、`build`、旧 `dist`、egg-info 和缓存均可重建。
- 当前 wheel 会包含完整 OpenCLI vendor/node_modules；运行时实际只需要生成的浏览器 JS
  和许可证/来源声明。
- HomeMaster Web 已有 `src/homemaster/web/static_dist`，运行服务器不需要
  `web/node_modules`。

### 2.4 当前安全问题

`homemaster doctor --json` 的 public summary 会输出 provider API key，现有测试甚至把密钥
出现视为正确行为。V3.2 必须先修复该问题，再运行任何新的环境采集或发布流程。

## 3. 锁定的产品边界

### 3.1 记忆是必选核心

标准 HomeMaster 始终包含：

```text
Python 3.11
  ├── HomeMaster
  ├── MindMemOS
  ├── Qdrant Local
  └── Neo4j Python Driver

Neo4j
  └── Java 21
```

- `memory.enabled` 允许省略或为 `true`。
- `memory.enabled: false` 在 V3.2 配置加载时必须明确拒绝，不得静默退化。
- Qdrant 使用进程内 local mode，不增加独立 Qdrant 服务。
- Neo4j 必须可用；不可用时 doctor 和正常启动均 fail closed。

### 3.2 Neo4j 的部署变体

记忆能力不可选，但 Neo4j 的位置可以不同：

1. `managed_local` 是默认复现路径。HomeMaster 准备并管理自己的 Neo4j 2026.05.0，
   始终使用 `.runtime/java` 指向的已验证 Java 21。
2. `external` 供已有 Neo4j 服务的环境使用。setup 不复制本地 JDK/Neo4j，但必须验证
   Bolt 连接、认证、目标数据库和一次真实读写。

本阶段固定使用 `managed_local`；不提供 external 远程部署分支。记忆协议、数据模型和 Agent 行为不因部署位置变化。

### 3.3 浏览器是可选能力

- 基础安装不安装 `playwright` extra，不下载 Chromium。
- 只有 `--with-browser` 才安装 Playwright Python 包、准备锁定 revision 的 Chromium 并运行
  真实渲染验证。
- Web Console 与浏览器工具不是同一概念。基础安装仍可启动 Web Console；未安装浏览器
  extra 时，浏览器工具必须返回明确的 capability-unavailable 错误。

## 4. 唯一复现入口

### 4.1 命令契约

在线环境：

```bash
./scripts/setup.sh
./scripts/setup.sh --with-browser
```

离线环境：

```bash
./scripts/setup.sh --offline /path/to/homemaster-runtime-bundle
./scripts/setup.sh --offline /path/to/homemaster-runtime-bundle --with-browser
```

不引入 `--profile core`、`--profile memory` 或 `--profile browser`。记忆没有 optional flag；
`--with-browser` 只控制浏览器依赖和浏览器二进制。

### 4.2 setup 职责

setup 必须按确定顺序执行：

1. 检查平台、CPU、glibc、磁盘空间、写权限和目标端口。
2. 准备项目自己的 Python 3.11 环境并根据锁文件安装依赖。
3. 首次运行时从去敏模板原子创建 `config/homemaster.yaml` 并设置权限 `0600`；为默认
   `managed_local` Neo4j 生成随机初始密码，只写入该真实配置；若文件已存在则绝不覆盖。
4. 根据配置的 Neo4j mode 准备资源：`managed_local` 准备 JDK 21 和干净 Neo4j；
   `external` 只验证用户提供的目标服务。
5. 对 `managed_local` 初始化新的 Neo4j 数据目录，不复制开发机已有数据库。
6. 创建或更新本机 `.runtime` 绑定。
7. 若带 `--with-browser`，准备 Chromium 并验证 revision。
8. 生成不含密钥的环境 identity 报告。
9. 执行离线 doctor；需要外部 API 的 live check 单独标记，不把网络不可达误报成安装失败。

setup 必须幂等：同样的输入重复执行不得重置 Neo4j、覆盖配置、切换 Chromium revision 或
改变已经锁定的目标路径。

### 4.3 配置文件契约

仓库保留两种不同责任的文件，但用户只操作一个真实配置：

- `config/homemaster.example.yaml`：Git 跟踪的完整去敏模板。
- `config/homemaster.yaml`：setup 自动创建、Git ignore、用户填写 API 和部署值。

README 只说明 setup 后需要填写 `config/homemaster.yaml`，不包含 `cp`、`vim` 或特定编辑器
命令。缺失配置时必须报告准确字段路径；任何输出均不得回显 SecretStr 的实际值。

## 5. 在线和离线资产

### 5.1 在线安装

在线 setup 允许从已锁定来源下载运行资产，但必须把最终版本和 SHA256 写入仓库内的资产
锁文件。禁止使用 floating latest URL。

### 5.2 离线 bundle

离线 bundle 至少包含：

- HomeMaster checkout 或与指定 commit 对应的源码归档。
- 与目标平台匹配的 Python 3.11 运行时。
- `uv.lock` 对应的完整 Python 包缓存/wheelhouse。
- 已锁定的 Temurin JDK 21。
- 干净的 Neo4j 2026.05.0 发行目录，不含开发机 data/logs。
- setup 脚本、去敏配置模板、许可证和 SHA256 清单。
- 仅在浏览器 bundle 中包含 Playwright Chromium 及其运行库清单。

离线 bundle 明确不包含：

- `.venv`。
- `.runtime` 软链。
- 真实 `config/homemaster.yaml`。
- API key、飞书 secret 或 Neo4j 现有密码。
- 现有 memory 数据、Neo4j data、trace、artifact 或 session。
- ALFWorld、AI2-THOR、ALFWorld dataset 或 Hugging Face 大缓存。

### 5.3 wheel 的地位

wheel 不是用户启动 HomeMaster 的入口，也不代表完整环境。V3.2 的主要复现方式是干净
checkout、锁文件、运行资产清单和 setup。若发布流程构建 wheel，则 wheel 必须通过文件
allowlist，不能包含完整 OpenCLI node_modules、历史计划、测试、trace 或机器配置。

## 6. 系统限制

参考支持基线是 Ubuntu 22.04、x86_64、glibc 2.35。其他平台必须使用各自匹配的 Python、
JDK 和 Chromium 资产，不能复用 Linux x86_64 bundle。

本地记忆部署还必须满足：

- Neo4j Bolt 端口默认 `127.0.0.1:7687` 可用，或配置一个确定的替代端口。
- Neo4j data 目录支持 POSIX 文件锁并具有稳定写入语义。
- HPC 环境优先把 Neo4j data 放在节点本地 ext4/XFS；不把高负载 GPFS/NFS 作为默认生产
  数据目录。
- 同一 data 目录同一时刻只能由一个 Neo4j 进程拥有。
- setup 和运行入口必须显式传递 `JAVA_HOME`，不读取系统默认 Java。
- 服务协议不能假定所有消费者与 Neo4j 位于同一进程，但 Neo4j URI 必须是本机 loopback 地址。

## 7. 低风险清理清单

### 7.1 可以直接清理的可重建产物

- `build/`
- 旧 `dist/`
- `*.egg-info/`
- `__pycache__/`、`.pytest_cache/`、`.ruff_cache/` 等缓存
- `web/node_modules/`

清理前后必须记录实际目标和体积。禁止运行无边界的 `git clean -dX`。

### 7.2 硬编码和安装状态

- 删除 doctor 中旧 Mac 解释器路径。
- 让环境 identity 接收调用者提供的标签，不硬编码主机名。
- `.runtime` 由 setup 在目标机器生成并校验，不进入发布归档。
- Playwright 路径通过 setup 生成的本机环境或标准环境变量解析。
- `success_path.py` 的失败 payload 使用权限受限、run 唯一的诊断目录；正常返回和结构化
  trace 不依赖该文件。

### 7.3 doctor 和依赖审计

- 删除不再属于当前实现的 `bm25s` 检查。
- 逐项核对 `fastembed`、spaCy 和 embedding 路径是否真实运行；存在于 lockfile 不等于
  doctor 应强制要求。
- doctor 的每项检查必须对应一个当前公开能力或必选运行资源。
- public summary 对所有 secret 字段统一输出 redacted/配置状态，不输出值。

### 7.4 发布内容清理

- HomeMaster Web 运行只依赖 `static_dist`，不依赖 Node/npm。
- OpenCLI 运行只携带生成 JS、运行所需最小文件和许可证证据。
- 构建后解包审计文件名、类型、总大小和最大文件，不能只看压缩包大小。

### 7.5 旧代码删除门

文件只有同时满足以下条件才能删除：

1. `rg` 和 Python import graph 均无生产引用。
2. CLI/Web/Gateway/配置没有公开入口。
3. package-data、运行资产和 setup 没有依赖。
4. 真实 smoke test 不加载该文件。
5. 对应职责已有明确替代或已经正式退休。

`thread_owned_sync.py`、残余 V1.9 helper 等候选必须逐一出具引用审计。仍被
`adapters/profiles.py` 导入的 `legacy_adapter.py` 不满足删除条件。

## 8. 错误处理

- 平台或 glibc 不匹配：在下载/解包前失败并打印检测值和支持值。
- 锁文件与离线缓存不一致：列出缺失 artifact 和 checksum，不允许联网补洞。
- Java 版本不匹配：打印实际 `JAVA_HOME` 和 Java major，不回退系统 Java。
- Neo4j 端口占用：打印进程可见信息和解决路径，不杀死未知进程。
- 配置已存在：保留原文件，只验证 schema 和权限。
- browser 未安装而调用 browser 工具：返回结构化 capability-unavailable，不触发下载。
- setup 中途失败：已经完成的步骤可幂等重试，不能留下看似成功的 identity 报告。

## 9. 验收标准

### 9.1 静态门

- 生产源码、setup 和去敏配置模板不含 hkust4、HPC2 或个人 Mac 路径。
- Git 跟踪文件和发布内容不含真实凭据。
- `memory.enabled: false` 被配置 schema 拒绝。
- 基础依赖不包含 Playwright；browser extra 包含且仅包含浏览器 Python 依赖。
- 发布归档不含 `.venv`、`.runtime`、node_modules、现有数据和 ALFWorld。

### 9.2 基础安装黑盒门

在 hkust4 和 HPC2_Outside 各自创建全新临时 checkout，并逐机器断言：

1. setup 返回码为 0。
2. Python 解释器、HomeMaster import 和 CLI 帮助真实可用。
3. `doctor --json` 中所有必选离线检查成功且不存在 secret 值。
4. Neo4j 由锁定的 Java 21 启动，Bolt 返回成功。
5. 对一个唯一测试节点执行写入、独立查询和清理，逐步核对返回码和外部图状态。
6. Qdrant local 创建一个唯一 collection，写入向量、独立查询并清理。
7. HomeMaster 完成一次包含记忆写入和下一 Session 召回的真实运行。
8. Web Console 真实监听非硬编码端口并返回页面。
9. 环境 identity 的 commit、Python、lock hash、Java、Neo4j 与实际命令输出一致。

验收不能读取开发机原 `.venv`、`.runtime` 或现有 memory data。

### 9.3 浏览器附加黑盒门

对每台验收机器分别断言：

1. 基础安装不存在 Chromium 下载目录且通用 Agent 可运行。
2. `--with-browser` 返回码为 0，并安装锁定 Playwright/Chromium revision。
3. Python Playwright 启动真实 Chromium，打开本地 fixture，读取确定文本并正常关闭。
4. HomeMaster browser 工具通过同一 Runtime 完成一次导航和 DOM 终态断言。

### 9.4 离线门

- 在禁止外部包下载并使用空 uv 缓存的环境中完成基础 setup。
- browser bundle 在同样条件下完成 Chromium 黑盒门。
- 缺少任一资产时 fail closed，错误列出准确文件和 checksum。
- 外部 LLM/embedding 不可达与安装失败分开报告；离线安装成功不虚构在线 Agent 可用。

## 10. 文档同源

交付时同步更新：

- README：最短在线/离线复现命令，以及“setup 后填写 `config/homemaster.yaml`”说明。
- 环境用户指南：系统限制、managed_local Neo4j、浏览器附加安装和故障排查。
- 架构文档：必选记忆栈、资源 owner、路径解析和启动数据流。
- CHANGELOG：逐提交记录清理原因、影响和兼容性。
- `docs/pitfalls.md`：记录系统 Java 11 被 Neo4j 误选等非显而易见问题。

## 11. 非目标

- 不在本阶段迁移或重写 ALFWorld。
- 不迁移现有用户 memory 数据或 Neo4j 数据库。
- 不批量删除 `plan/`、`docs/reports/` 或 `story/` 历史证据。
- 不承诺 Linux x86_64 离线 bundle 可跨架构或跨 glibc 世代运行。
- 不把 Docker 设为唯一安装方式。
- 不通过复制现有 `.venv` 实现所谓“一键迁移”。
