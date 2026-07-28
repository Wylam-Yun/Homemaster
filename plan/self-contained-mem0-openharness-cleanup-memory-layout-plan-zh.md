# HomeMaster 自包含 mem0、OpenHarness 清理与记忆目录统一实施计划

## 1. 状态与目标

- 状态：`COMPLETED`。代码、测试、外部 wheel/Qdrant 终态门、文档和计划规定的唯一一次最终只读代码评审均已
  完成；评审发现已整改并进入针对性复验，不再追加评审。
- 基线：`main@0e068672416b00970b398f96747eb489aa8212f8`。
- 目标一：删除仓库内不参与 HomeMaster 生产运行的 `src/openharness`，以及只验证该副本的测试和生成脚本。
- 目标二：把当前真环境验证过的 `mem0ai==2.0.13` Python 运行时源码完整纳入仓库和 HomeMaster wheel，安装
  HomeMaster 时不再解析或下载 `mem0ai` distribution。
- 目标三：持久记忆数据只存在于一个可配置外部根目录，默认 `~/.homemaster/memory`；代码、缓存和数据明确
  解耦。
- 目标四：现有 split layout 可无损迁移，迁移后旧 memory ID、payload、dense/BM25 vector、历史和 evidence
  继续可读。

## 2. 锁定决策

1. mem0 采用完整 vendoring，不裁剪 backend/LLM 文件，不在本阶段重写为原生 Qdrant backend。
2. vendoring 真理源是已验证 PyPI wheel
   `mem0ai-2.0.13-py3-none-any.whl`，SHA-256
   `dff29057329370243d88bfccd367deba41c2fb1652f63225a23068cbdd1bc066`；Git tag 不能替代该字节来源。
3. vendored 包保留顶层 Python import `mem0`，HomeMaster wheel 同时分发 `homemaster*` 与 `mem0*`；不维护
   第二套兼容 shim。
4. 删除 `mem0ai[nlp]` dependency；将其实际 core dependencies 与 `spacy` 提升为 HomeMaster 的显式锁定依赖。
   具体版本约束在目标空 venv 中核对前标为 `UNVERIFIED`。
5. 运行时只有一个结构化记忆 backend，不新增 external/vendored、old/new layout 等 mode。
6. 唯一持久数据根配置为 `memory.data_root`，默认 `~/.homemaster/memory`。路径确定性派生为：

   ```text
   <data_root>/files/SOUL.md
   <data_root>/files/USER.md
   <data_root>/files/MEMORY.md
   <data_root>/qdrant/
   <data_root>/history.sqlite3
   <data_root>/evidence.sqlite3
   ```

7. BM25/FastEmbed cache 是可重建缓存，不进入 data root；默认仍为项目 `.cache/homemaster/fastembed`，允许显式
   配置到其他可写缓存目录。
8. 旧配置只作为一次迁移输入，不形成长期运行 mode。新旧字段同时提供或目标/源数据冲突时 fail closed。
9. OpenHarness 名称、MIT attribution 和已经移植到 `homemaster` 的实现保留；删除的是 vendored package 和只运行
   它的测试，不做全仓字符串清洗。
10. 迁移按 `files/qdrant/history/evidence` 组件分别发布并用一个 journal 恢复；绝不 rename/替换已有
    `data_root` 祖先目录。当前默认 qdrant/history/evidence 已在新 data root 时只做原位验证，唯一实际移动组件
    是旧 `~/.homemaster/memories` 到 `<data_root>/files`。
11. OpenHarness provenance 采用单一路径：冻结并移入 archive，删除 live upstream generator；逐项保存
    upstream-test 到 HomeMaster-owned regression/test-gap 的映射。

## 3. 当前事实

- `src/homemaster` 对 `openharness` 的生产 Python import 数量为 0。
- setuptools 只包含 `homemaster*`；当前安装 wheel 已在源码外构造全部默认工具。
- `src/openharness` 有 139 个跟踪文件，约 829 KB；`tests/openharness_upstream` 有 17 个跟踪文件。
- 当前 mem0 安装包包含 209 个文件，运行时源码约 1.9 MB，许可证为 Apache-2.0。
- HomeMaster 使用 `Memory.from_config()` 以及 `add/get/get_all/search/update/delete`，并读取 Qdrant raw point；
  因此 vendored bytes 必须保持已验证 API 和内部 Qdrant 行为。
- 当前默认文件记忆位于 `~/.homemaster/memories`，Qdrant/history/evidence 位于
  `~/.homemaster/memory`；真实目标主要是把前者迁入后者的 `files/`。

## 4. 非目标

- 不把任何真实记忆、API key、真实 YAML、trace 或用户数据提交到 Git。
- 不 vendor Qdrant、spaCy、FastEmbed 等所有传递依赖源码；离线交付由 wheelhouse/OCI 制品另行负责。
- 不改变六个 memory 工具名、输入/输出 schema、permission、evidence 或 provider-visible content。
- 不改变 embedding model、4096 维 collection contract、BM25 revision 或当前 record serialization。
- 不在本阶段删除 OpenHarness-derived attribution、历史设计文档或 HomeMaster 已移植实现。

## 5. 工作包

### WP0：RED 门与来源锁定

1. 增加失败测试，断言构建后的 HomeMaster wheel：
   - 包含 `mem0/__init__.py`、核心 memory、OpenAI embedder、Qdrant vector store 和许可证；
   - `METADATA` 不含 `Requires-Dist: mem0ai`；
   - 空 cwd/隔离 Python 中不存在 `mem0ai` distribution；
   - `import mem0` 后，HomeMaster 随包 manifest 对全部 vendored 文件和关键 selected Qdrant/core 文件做哈希
     校验，拒绝被另一个安装覆盖的字节。
2. 增加失败测试，扫描 `src/homemaster`、scripts 和 live tests 的 production import graph，拒绝
   `openharness` import。
3. 从锁定 wheel 提取文件 inventory、每文件 SHA-256、wheel URL/hash、版本和 Apache-2.0 LICENSE，生成可 review
   Markdown/JSON manifest；不从当前 site-packages 盲拷贝未登记文件。
4. 对比锁定 wheel 与 sdist 的 `mem0` runtime tree，记录差异。任何 selected Qdrant/core 文件差异先停止实现，
   不自行混合两个来源。
5. 在目标 venv 真构建一次最小实验 wheel，核对 setuptools 多 root discovery、license/package data、editable
   install 和普通 wheel install 行为；这些行为通过前均标 `UNVERIFIED`。
6. 用锁定 `qdrant-client==1.18.0` 对一份真实复制数据目录探测 collection 枚举、point count、scroll/retrieve、
   payload 与 named-vector 集合的可用调用和返回码；具体外部 API/符号在该探测前标 `UNVERIFIED`。

### WP1：完整 vendoring mem0

1. 新增 `third_party/mem0ai-2.0.13/`，包含锁定 wheel 的 `mem0/` Python/package-data bytes、LICENSE、来源说明
   和 SHA-256 manifest。
2. 配置 setuptools 从 `src` 和该 third-party root 发现包，include 仅为 `homemaster*` 与 `mem0*`；确保
   `src/openharness` 不会因多 root 配置重新进入 wheel。
3. 删除 `mem0ai[nlp]==2.0.13`，显式声明并锁定 mem0 core dependencies 与 spaCy model direct URL。构建 wheel
   后审计最终 `Requires-Dist`，禁止依赖只存在于 `[tool.uv.sources]`。
4. 保持 HomeMaster 的 `from mem0 import Memory` 边界不变；启动时断言 `mem0ai` distribution 不存在，并用
   HomeMaster 自带 manifest 校验实际 import 到的 vendored 文件。存在外部 distribution 或字节不符时 fail closed。
5. 更新 `THIRD_PARTY_NOTICES.md`，包含 mem0 Apache-2.0 notice、版本、wheel SHA 和本地修改说明；原始 vendored
   文件不做顺手格式化。

### WP2：删除 OpenHarness vendored package

1. 删除 `src/openharness/` 与 `tests/openharness_upstream/`。
2. 将 `scripts/export_service_tool_specs.py` 改为只从 HomeMaster registry 生成仍在分发的 spec；删除 V2 upstream
   manifest generator 和只为 vendored OpenHarness 服务的 fixture。
3. 将 `plan/V2.0/upstream-port-manifest.json` 移到 `plan/V2.0/archive/`，增加 Markdown 说明其为不可执行的历史
   provenance snapshot，不再进入任何 live gate。新增静态映射逐项记录 15 个被删 upstream test 文件对应的
   HomeMaster-owned regression，缺失项必须写明确 test gap 和风险 owner。
4. 调整 cleanup guard、release tests 和 Ruff exclusion。保留 `OpenHarness` provenance value、MIT notice、bundled
   Skill 内容及 HomeMaster 自己的行为回归。
5. 构建 wheel 并枚举 archive，断言不存在 `openharness/`，同时默认 39 工具、八份 bundled Skills 和 MCP/任务
   服务仍可构造。

### WP3：统一记忆数据根与兼容迁移

1. `MemoryConfig` 新增 `data_root` 和派生 path properties；`FileMemoryStore`、`MemoryEvidenceLedger`、
   `Mem0MemoryStore`、doctor 和 composition 只能消费这些派生路径。
2. 配置解析层从 raw mapping 记录字段是否显式出现，并识别旧字段：`memory.root`、
   `memory.mem0.qdrant_path`、`memory.mem0.history_db_path`。解析只生成一个 immutable migration specification，
   不在 validator 中执行 IO。即使整个 `memory` block 缺失或省略 `root`，只要 `<data_root>/files` 不存在，仍
   确定性探测唯一历史默认源 `~/.homemaster/memories`；只有显式新旧字段冲突才因配置歧义 fail closed。
3. 新增唯一 `MemoryMigrationCoordinator`，所有可能打开 memory store 的入口必须先调用它：
   - application/CLI/Gateway：`ensure_ready(auto_migrate=True)`，完成或恢复迁移后才构造 stores；
   - `homemaster memory migrate --config <path>`：显式执行同一 coordinator 并输出 typed manifest/返回码；
   - doctor/doctor-live：只调用 read-only `inspect()`；需要迁移时报告 `WARN migration_required`，不得创建目标 DB、
     staging 或抢先打开 Qdrant。
4. coordinator 持有 `<data_root parent>/.memory-migration.lock`，使用 `<data_root>/migration-journal.json` 锁定首次
   规划好的 source/target；重启只恢复同一计划，不重新选择路径。
5. 迁移规则按组件执行：
   - source 与 target 是同一规范路径：不复制，只做原位完整性验证并记入 journal；
   - source 不同且 target 组件不存在：在 `<data_root>/.staging/<component>-<migration-id>` 复制、逐文件
     fsync/hash并验证，然后仅对该组件执行同文件系统 atomic publish；
   - target 组件已存在：只有其 manifest/hash 与 source 完全一致才记为完成，否则 fail closed，禁止合并；
   - SQLite 分别执行 `PRAGMA integrity_check`；
   - Qdrant staging 使用 WP0 真环境核对后的锁定 API，核对 collection、point count 和逐 point
     ID/payload/named-vector 集合，任何非成功返回码失败；
   - 三个 Markdown 文件逐字节核对；
   - 所有组件完成后写 `migration-manifest.json`，运行时才可打开 stores；
   - 原 source 默认保留，不自动删除；异常、取消和崩溃后 journal 可幂等恢复，未完成 staging 永不作为数据源。
6. 当前默认布局的 qdrant/history/evidence 走“同路径原位验证”，旧 `~/.homemaster/memories` 只发布到新增
   `files/` 子目录；data root 非空本身不是冲突，禁止替换 data root 目录。
7. 成功迁移后运行时只读新 data root。旧字段打印一次 typed deprecation diagnostic；真实字段值按既有精确
   文本纪律保留。
8. 第二次启动读取 manifest 幂等跳过，不重新计算目标或产生第二份数据。

### WP4：验证、文档与交付

1. 单元/集成门：配置字段 presence、无 config/省略 memory/省略 root 三种历史默认发现、路径派生、组件同路径、
   目标非空、冲突、取消/崩溃恢复、权限、跨文件系统 source、SQLite 损坏、Qdrant lock、journal/manifest 幂等、
   旧字段诊断，以及 doctor 零写入。
2. 黑盒数据门：创建两个独立旧 layout，每个写入不同 fact/procedure、文件记忆和 evidence；关闭旧 owner 后启动
   顶层 HomeMaster，逐实例断言原 ID、内容、exact/hybrid recall、更新、删除、重启终态，禁止用任一实例通过
   代表全部通过。
3. wheel 门：源码外空 venv 只安装 HomeMaster wheel 及其声明依赖，确认没有 `mem0ai` distribution，vendored
   `mem0` import、真实 embedded Qdrant CRUD/BM25、六工具 surface、OpenHarness package absence 全部通过。
4. 外部返回码门：构建、安装、doctor、顶层 CLI 和迁移命令返回码均为 0；故意冲突/损坏用例必须非 0 且旧数据
   未变。
5. 全套 non-live tests、Ruff、format、`uv lock --check`、`git diff --check`、secret scan。
6. 同步 README、记忆用户指南、架构、配置 example、第三方 notice、pitfalls、CLAUDE 正向规则、progress 和
   CHANGELOG。真实配置继续 Git ignored、mode 0600。

## 6. 实施顺序与提交边界

1. WP0 RED tests/source manifest。
2. WP1 mem0 vendoring并先通过 wheel/真实 Qdrant 门。
3. WP2 删除 OpenHarness；在删除前保存必要 provenance，不从已删目录恢复实现。
4. WP3 data-root 配置和迁移。
5. WP4 全部验证和文档。
6. 完成外部终态门和文档后启动一次最终只读代码评审；逐条处理后仅做针对性复验。
7. commit 前确保 CHANGELOG 条目与 commit message 同源，再推送功能分支并合入 main。

## 7. 回滚边界

- mem0 vendoring 回滚只改变代码/依赖，不改记忆数据格式；旧 `mem0ai==2.0.13` wheel 可重新安装读取同一数据。
- layout 迁移不删除旧源，回滚可把配置指回旧路径；未生成完整 manifest 的 staging 一律不作为可用数据。
- OpenHarness 删除不影响已安装 HomeMaster wheel；如兼容门发现缺口，应补 HomeMaster-owned regression，而不是
  把整个 `src/openharness` 临时恢复成生产依赖。

## 8. 完成定义

- Git 和 HomeMaster wheel 都包含锁定 mem0 runtime 源码及许可证，不依赖 `mem0ai` distribution。
- Git、wheel、生产 import graph 均不包含 OpenHarness package；HomeMaster 默认能力与来源声明仍完整。
- 所有持久记忆只从一个 data root 读取；旧 layout 真实迁移后每实例终态通过。
- 代码 checkout 可替换，数据目录可独立复制；任一方都不隐式包含另一方。
- 全部内部门与正交外部终态门通过，无未说明的 `UNVERIFIED` 外部符号。
