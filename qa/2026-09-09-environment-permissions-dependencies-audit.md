# HomeMaster 环境、权限和多余依赖：问题说明与后续修改指南

审查日期：2026-09-09。服务器：`hkust4`。
项目：`/home/haodong2/weilin/red_bird/Homemaster`。

**阅读前先确认：这里只修改了问题文档，下面的问题尚未修复。** 文中的测试通过，指调查过程中做的有限验证，不代表已经可以发布修改。所有命令输出和代码行号都是审查当天的快照。

## 1. 明天先看这里

这次审查起因是：项目每次配环境、处理权限都很麻烦，换地方运行也不够稳定；同时怀疑安装了一些已经不用的依赖。

调查发现，这个感受主要对应三件事：

1. **安装过程没有完整记住你要哪些功能。** 重新同步依赖时，它可能把之前装好的浏览器或测试工具移除。
2. **项目能运行，依靠的不只是仓库里的文件。** 还依赖服务器已有的 Python 环境、Java、Node、外置 ALFWorld 数据和绝对路径；这些没有全部纳入重建流程。
3. **确有多余依赖候选，而且诊断本身也有过时要求。** 外部 `traced` 包没有被代码使用，却带进一串其他包；FastEmbed 在当前业务链中没有发现使用，但 doctor 仍要求安装它。

权限方面需要分开看：配置文件的 Linux 读写权限是一层，Agent 能不能执行某条命令是另一层。把 Agent 设置成 `full_auto`，不会改变文件的 Linux 权限，也不会覆盖命令白名单。

**建议明天的处理顺序：**

| 顺序 | 做什么 | 为什么先做 |
| --- | --- | --- |
| 1 | 按 DEP-01 清理三个高置信未使用包；在新环境验证 | 范围较小，能先减少不必要的依赖链 |
| 2 | 按 ENV-01 固定每次安装使用的完整功能集合 | 否则以后重跑 setup 仍可能把环境改坏 |
| 3 | 按 ENV-05、ENV-07 修好工具链和诊断 | 让“哪里不满足运行条件”能被准确报告 |
| 4 | 按 DEP-02 单独处理 FastEmbed；按 PERM-01 统一文件权限检查 | 这两项需要连同旧检查/测试一起调整 |
| 5 | 按 ENV-02/03/04/06 补齐迁移和离线安装 | 工作较大，需要正式实施计划 |
| 单独确认 | PERM-02 的命令白名单到底要服务什么用途 | 不能为了不再报错就把权限全部放开 |

优先级是本次建议，不是已经批准的架构改造方案。项目有其他未提交修改；开始修复前先检查 `git status --short`，不要覆盖其他工作。

## 2. 理解本文所需的最少背景

| 名词或文件 | 在这个项目里是什么意思 |
| --- | --- |
| `pyproject.toml` | 项目自己声明“需要安装哪些 Python 包”的清单 |
| `uv.lock` | 把这些包及它们所需的其他包固定到具体版本的清单；不自动锁定整台服务器 |
| 直接依赖 / 传递依赖 | 项目需要 A，A 又需要 B；A 是直接依赖，B 是传递依赖。项目没有 import B，不等于可以卸载 B |
| extra | 可选功能的一组依赖。例如 `browser` 包含浏览器相关包，`dev` 包含测试工具 |
| `.venv` | 项目专用的 Python 安装目录，包通常装在这里，避免污染全局 Python |
| `uv sync` | 把环境调整到本次选定的依赖集合；可能安装，也可能卸载，不是简单“再加几个包” |
| `--dry-run` | 只报告将发生什么，不实际安装或卸载。本次依赖移除数量来自这个预览 |
| `.runtime` | 当前机器的资源入口目录，里面许多项目实际是指向别处的软链接 |
| 软链接 | 类似文件系统中的快捷方式。复制快捷方式不等于复制它指向的环境或数据 |
| `scripts/setup.sh` | 准备依赖、Java/Neo4j 和本机配置的安装脚本，会修改环境 |
| `scripts/homemaster` | 项目启动脚本，选择 Python、配置路径和运行目录后启动应用 |
| `doctor` | 项目的体检命令。目前偏重配置和 import 检查，不代表所有功能已真实跑通 |
| wheel / bundle | wheel 是 Python 安装包；bundle 是用于分发的一组文件。仅有项目自己的 wheel，不等于有全部依赖 |
| Neo4j / Qdrant | 项目记忆系统使用的图数据库和向量存储；它们与大模型接口是不同的外部环节 |
| ALFWorld / THOR | 具身任务环境及相关模拟运行时，当前与主 Python 环境分开维护 |
| 退出码 | 命令结束时给出的状态，通常 0 是成功、非 0 是失败；仍需检查它实际做成了什么 |

## 3. ENV-01：重跑安装，可能把之前可用的功能依赖卸掉

**影响：高。证据：已执行真实 uv 的预览，没有实际卸载。**

### 你会遇到什么

可能先配置好浏览器，再按另一个功能的文档同步依赖，随后浏览器模块找不到了；也可能重跑 setup 后 pytest 不能用了。表面看像环境偶尔损坏，实际是安装命令每次选了不同的依赖集合。

### 我们看到了什么

当前 `.runtime/setup-state.json` 记录 `browser=false`，但实际环境有 Playwright。执行：

```bash
/home/haodong2/.local/bin/uv sync --frozen --dry-run --offline
```

退出 0，报告 `Would uninstall 17 packages`，包含 Playwright、pytest、pytest-asyncio、ruff、TextWorld。加 `--extra browser` 后仍报告移除 15 个包。单独选择 mcp 也会移除 Playwright。

这些数量是当前环境的预览，不代表每台机器都会移除相同数量。也不是说这 17 个都该保留：要保留哪些，应由项目实际需要的功能决定。

### 原因在哪里

`scripts/setup.sh:27-30,48-51` 只记住 browser 布尔值，没有维护完整功能清单。README 分散提供多条 `uv sync --extra ...` 命令，容易让使用者误以为功能会逐次累加。

### 应怎样修改

1. 将这台部署需要的完整功能集合存到统一配置，例如“browser + gateway + dev”。这个例子只是解释格式，不代表本机已经决定要启用全部功能。
2. setup 和文档都从同一份集合生成 sync 命令，不再让不同入口各自拼一部分。
3. 同步前明确显示哪些功能和包会被移除；功能选择改变应能被解释。
4. 更新 README 的安装顺序，说明 sync 是环境对齐操作。

**验收：** 在隔离环境连续运行同一安装配置两次；逐个确认所选功能的包仍在、实际入口仍能执行。不能只看 uv 返回 0。

## 4. DEP-01：三个高置信未使用包仍在安装清单里

**影响：中，优先清理。证据：源码扫描、依赖关系核对和有限运行测试。**

### 哪些包，为什么怀疑

| 包 | 发现 | 修改范围 |
| --- | --- | --- |
| `posthog` | 活跃源码没有导入/调用，核心锁图没有其他包需要它 | 根 pyproject 的声明及重新生成的锁文件 |
| 外部包 `traced` | 没有被导入；代码里的同名函数不是它提供的 | 根 pyproject；vendored MindMemOS 子项目 pyproject 也有残留声明 |
| `sqlalchemy` | 活跃代码没有导入，锁图中只有外部 traced 需要它 | 与 traced 一起评估删除 |

最容易误判的是 `traced`：代码里确实很多 `@traced(...)`，但它们导入的是 `mindmemos.logging` 中项目自己写的函数。具体定义在 `third_party/MindMemOS/src/mindmemos/mindmemos/logging/tracing.py:244`。仅看名字一样，不能证明要安装 PyPI 上同名的包。

外部 traced 还会拉入 SQLAlchemy 和 AWS SDK 相关包。仅计算核心依赖图，移除这三条根声明后，以下 **15 个包不再从核心项目可达**：

```text
aioboto3 aiobotocore aiofiles aioitertools backoff botocore
 gitdb gitpython greenlet jmespath posthog smmap sqlalchemy termcolor traced
```

这表示依赖关系上可能不再需要它们，不是实际卸载结果；其他 extras 或外部 ALFWorld 环境仍可能有自己的需求。

### 已经验证到哪一步

在一个独立 Python 进程里禁止导入这三个包，运行记忆运行时、CLI 帮助和 provider transport 三组测试：**23 passed，退出 0**。测试包含真实本地 Qdrant 创建、关闭、重开后读取 collection 列表。

这没有验证真实 Neo4j 或大模型调用，也没有真的从新环境卸载它们。现有安装的包描述信息仍然存在。因此目前可以作为清理依据，但还不是删除后的完整验收。

### 应怎样修改

1. 在隔离 checkout/虚拟环境操作，保留当前可用环境。
2. 删除上述已确认多余的声明，同步根项目和受影响的 vendor 清单；不要手工删 uv.lock 中几段文本，而要用项目工具重新生成锁。
3. 检查锁文件差异，避免顺手升级无关依赖；解释哪些包因失去消费者而退出。
4. 从新锁安装全新环境，读取安装列表确认候选包确实没有装上。
5. 执行现有测试、安装包入口检查及真实记忆写入/检索；向量存储与 Neo4j 各自读回结果。

**验收：** 不含候选包的新环境能运行所承诺功能；数据库里的记录真实存在且正确，进程关闭后清理完成。文档与 CHANGELOG 同步记录原因和影响。

## 5. DEP-02：FastEmbed 的业务用途疑似已经消失，体检还要求它存在

**影响：中。证据：强候选，尚未完成干净环境和真实全链路验证。**

### 现象与原因

当前 HomeMaster 中仅发现 `src/homemaster/cli/doctor.py:101` 强制 import fastembed，以及旧测试要求依赖声明中固定它的版本。当前 MindMemOS 的稀疏向量由 `components/text/sparse.py` 自己编码，普通语义向量走模型客户端。

历史上 FastEmbed 用在旧 mem0/Qdrant 的 BM25 模型链路，`docs/pitfalls.md:1299` 起有相关缓存问题记录。历史上需要它，不代表替换记忆实现后仍需要它。

这里形成一个循环：业务可能已经不用，但 doctor 仍检查它；删包后 doctor 报错，又会让人误以为业务必须安装它。

### 已做验证

进一步禁止导入 fastembed，再运行 DEP-01 同样的三组测试：**23 passed，退出 0**。Qdrant 支持可选 FastEmbed，不意味着所有 Qdrant 操作都要使用它。

### 应怎样修改

1. 再核对当前所有公开记忆功能是否使用本地 FastEmbed 推理，尤其注意动态加载。
2. 若确认无业务消费者，一起移除包声明、doctor 的无条件要求和只验证旧依赖名字的测试。
3. 用当前记忆功能的行为测试替代“清单里必须有 fastembed”的旧断言。相关文件包括 `tests/homemaster/memory/test_memory_config.py`。
4. 在没有 fastembed 的全新环境运行 doctor 和真实记忆检索。不要只把报错检查删掉，却不验证业务。

**验收：** 中英文文本处理、稀疏向量、语义向量和查询结果均按当前功能要求验证；doctor 不再因一个不用的包缺失而误报。

### 几个不能一起误删的包

| 包 | 保留理由 |
| --- | --- |
| pytz | Neo4j 的传递依赖；顶层声明是否重复可另查，但不能当完全无用包卸载 |
| protobuf | Qdrant、OpenTelemetry proto 等需要；版本范围可能还承担兼容约束 |
| en-core-web-sm | spaCy 通过配置里的模型名动态加载，搜不到 import 也在使用 |
| aiokafka | vendor 的 Kafka 模块和反馈流程仍有引用；拆成可选部署属于另一个设计任务 |
| OpenTelemetry API/SDK/HTTP exporter | `infra/telemetry.py` 分别直接使用，不能因 traced 包可删就一起删 |

## 6. ENV-05：SSH 和前端使用的工具没有被项目可靠固定

**影响：中；前端命令当前确实失败。**

### 实测现象

- 默认 SSH shell 执行 setup 退出 2，提示找不到 uv。
- uv 实际在 `/home/haodong2/.local/bin/uv`，版本 0.11.19，满足脚本范围；是命令搜索路径 PATH 没包含它，不是没有安装。
- 默认 Node 是 `v10.19.0`。运行 `node web/node_modules/typescript/bin/tsc --version` 就退出 1，报 `SyntaxError: Unexpected token ?`，尚未进入项目编译。

Node 用来运行前端构建工具；版本太旧，连工具本身的语法都不能理解。已安装包自己的版本要求为：TypeScript >=14.17，Vite ^18/^20/>=22，Vitest ^20/^22/>=24，jsdom ^20.19/^22.13/>=24。

### 原因和修改

仓库未跟踪 `.node-version` 或 `.nvmrc`，`web/package.json` 没有 engines/packageManager，setup 也不准备 Node。每次运行使用哪个工具，取决于人的 shell 设置。

应固定满足全部前端工具要求的 Node 和包管理器版本；启动前明确解析 uv 的路径，不能仅靠用户手工 export。将检查放在安装前，明确报告“没找到工具”还是“版本不支持”。不要用升级服务器全局 Node 代替项目级配置。

**验收：** 新的非交互 SSH 会话无需手工准备 PATH；执行 typecheck、前端测试、build，检查真实生成的前端文件。具体版本选择在实施时锁定，不用本文当时的版本快照代替选择。

## 7. PERM-01：同一配置文件，运行时允许读，安装时却拒绝

**影响：中。当前真实文件没有确认存在错误权限；入口不一致已复现。**

### 先解释 0600

`0600` 表示只有文件所有者可读写，其他用户不能读。配置含 API key 或密码，所以要求这个权限有合理目的。`0664` 则允许同组读写、其他用户读取。

当前两个真实 YAML 都是 0600。默认 umask 为 0002，新建/复制文件时容易产生 0664，因此换机器或重建配置可能重新碰到该问题。

### 我们怎样复现

使用无凭据临时 YAML，设置成 0664：

- 应用 `load_config` 成功读取。
- setup 的 `_write_yaml` 拒绝，报 `private config must be mode 0600`。

位置：`scripts/setup.sh:65-86`、`scripts/setup_memory_runtime.py:145-159`、`src/homemaster/config/config.py:707`。setup 甚至先同步依赖和准备资产，再检查已有配置权限，所以失败前环境可能已经改变。

### 应怎样修改

统一创建、加载、setup 和 doctor 的权限规则。新配置用一次性安全写入保证 0600；已有配置先检查所有者、权限和父目录是否可写，再进行任何安装变更。提供明确且范围受控的修复操作，告诉人要改哪个文件、为什么，而不是反复要求猜 chmod。

**验收：** 在临时目录分别测试正确权限、0664、所有者不符、父目录不可写。各入口对相同文件给出一致解释；失败前不修改环境；成功后读取实际 owner/mode。不要靠 chmod 777 解决。

## 8. PERM-02：full_auto 下，示例任务白名单仍会拒绝其他命令

**影响：需要确认用途。已确认配置和判断顺序，没有取得用户当时的原始报错。**

`config/homemaster.browser.yaml` 里有一条允许执行的精确 terminal 命令，指向 ant-design-pro 示例任务的绝对路径。精确白名单的意思是：只有完整字符串匹配的命令才允许；不是“这类命令都允许”。

两个实际配置都写了 full_auto，但 `permissions/policy.py:65-147` 先检查路径、命令白名单、工具拒绝规则和用户能力，再判断 full_auto。因此换路径、换命令或改变空格，都可能无法命中那一条允许规则。

这解释了为什么“自动执行”不等于“任何命令都有权限”，但尚不能断言这就是用户每次遇到权限问题的唯一原因。

**修改方向：** 先明确该部署是专用演示还是通用工作助手，确认应允许什么，再调整策略；增加诊断，直接告诉人本次用了哪个配置、被哪条规则拒绝、应到哪里修改。不要未经用途确认删除白名单。

**验收：** 从真实 Web/Gateway 入口检查最终生效配置；选一条应该允许、一条应该拒绝的无害命令，分别检查结果和外部副作用。不能只测试策略函数。

## 9. ENV-04：锁文件写了 Java 版本，已有安装却用另一个版本

**影响：中高，已证实不一致，但未证明现有 Java 无法运行。**

Java 是运行 Neo4j 所需的工具。`config/runtime-assets.lock.json` 声明 `21.0.12.1+1`，实际 `.runtime/java/bin/java -version` 返回 `21.0.11+10`，退出 0。

原因：`scripts/setup.sh:53-62` 和下载代码发现可执行文件已存在就跳过，不核验它是不是锁定的版本。因此新机器按锁下载，旧机器继续用旧版本，两者不会自动一致。

**修改方向：** 对新安装和已有安装都记录并校验版本、来源和安装标识。发现不一致时明确说明，再通过独立迁移步骤更新；不直接覆盖正在运行的共享目录。

**验收：** 故意给错版本时检查不通过；正确安装实际执行 `java -version` 与清单一致。再验证 Neo4j 启动和查询，不能只验证压缩包 SHA256。

## 10. ENV-06：本机软链接能找到环境，但不能在新机器重建环境

**影响：中，迁移时明显。**

`.runtime/alfworld-venv` 指向 `/data0/yuqiao/envs/hm_alfworld`。路径包含另一个名字，但实测所有者 UID 与当前用户相同，不能据名字认定属于他人。Java、Neo4j、memory 也绑定外部目录。

`pyproject.toml` 的 alfworld extra 只有 pyyaml，没有提供 ALFWorld/THOR 的完整安装配方。启动脚本把两个环境的包搜索路径组合起来，所以当前能找到代码，不意味着新机器能从仓库重新装出同样环境。

还有一个恢复问题：`setup_memory_runtime.py:242-270` 先建目录和链接，最后才检查并写配置；中途失败可能留下部分完成状态。重新跑时又可能遇到 binding conflict。

**修改方向：** 保留 ALFWorld 与主环境分开的理由，不急着合并；分别给出版本锁、安装步骤和数据资产说明。先检查所有输入，再一次提交绑定结果；失败可回滚，已有链接要有明确的迁移入口。

**验收：** 新 checkout 不借用旧 worktree 的路径即可按文档准备环境；在最终执行 ALFWorld 的那个 Python 里检查完整依赖，实际执行隔离任务并读取环境结果。注入 setup 失败后检查没有半完成绑定。

## 11. ENV-02：现在的离线包没有包含离线安装真正需要的全部东西

**影响：高（对离线部署）；源码缺失已确认，新机断网安装未执行。**

`build_runtime_bundle.sh:21-44` 构建的是 HomeMaster 自己的 wheel，未收集全部第三方依赖 wheels、构建依赖、Python 和 Chromium。`--with-browser` 只是记了一个标志，没有把浏览器本体装进包。

setup 使用这个 bundle 时虽然设置 NO_INDEX/FIND_LINKS，仍不是完整禁止网络的安装流程，并会执行 `playwright install chromium`。旧机器有缓存时可能碰巧成功，新机器没有缓存就可能需要联网或失败。

**修改方向：** 先说清楚“离线包保证什么”：哪些系统工具要求提前装好，哪些由包提供。按实际所选功能收集完整依赖和浏览器二进制，安装严格遵守离线约束，缺东西直接报告缺项。

**验收：** 在没有历史缓存的新目录、网络不可用的条件下安装；对包承诺的每项功能实际启动并核对结果。文件齐全和 SHA256 正确只是完整性检查，不是安装成功证据。

## 12. ENV-03：同一个分发包里的源码和安装包可能不是同一份代码

**影响：中高；静态确认构建来源不同，未解包认证实际发布包。**

`build_runtime_bundle.sh:21-24` 用 `git archive HEAD` 生成源码包，却从当前工作区构建 wheel，并复制当前 lock。HEAD 是最后一次提交的状态；工作区可能还有没提交的修改。当前项目确实存在未提交修改。

结果可能是：别人看源码包研究问题，实际安装运行的是包含新改动的 wheel，无法对应。包内每个文件的哈希都对，也不能证明它们来自同一版本。

**修改方向：** 让源码、wheel 和 lock 从同一个固定快照生成；有未提交修改时明确拒绝正式打包，或采用明确的一致快照规则。选择哪种由发布流程决定，不把当前工作区静默当正式版本。

**验收：** 从干净和有未提交修改的目录各测一次；解包比对源码身份、打包模块与锁文件。确认行为符合所选规则。

## 13. ENV-07：doctor 返回成功，并不代表你接下来要用的功能可运行

**影响：中；会误导排查方向。**

实测 `./scripts/homemaster doctor --json` 返回 0、stderr 为空。记忆检查原文是：`memory configuration and migration state are ready; backend was not opened`，意思是“配置和迁移状态看起来可用，但没有打开后端”。它没有证明数据库能启动、能读写；也没有发现默认 Node 无法运行 TypeScript。源码中 --live 主要追加 provider 请求，不补足所有运行条件。

另一个误报是 Python：doctor 沿 Python 软链接找到最终真实文件，再检查路径里有没有 `.venv`。uv 的 Python 本体可以在共享安装目录，所以合法的项目虚拟环境也被 WARN。

**修改方向：** 把检查结果写清楚是“配置有效”“依赖能导入”还是“功能已实际验证”。根据用户所选功能检查对应条件；需要写库的真实检查使用隔离测试数据。虚拟环境身份根据 `sys.prefix` 与 `sys.base_prefix` 等解释器信息判断，而不是猜目录名字。

**验收：** 人为让某个被选中功能缺前置条件，doctor 应明确指出该功能不能用；准备齐全后，通过真实运行结果验证。未测的外部环节显示“未验证”，不混进整体 ready。

## 14. 总体修改路线与取舍

| 路线 | 具体做法 | 代价 |
| --- | --- | --- |
| ① 推荐先做：修现有入口 | 统一功能清单、工具路径、权限检查、doctor，清理死依赖 | 改动较小，但仍需说明操作系统前置条件 |
| ② 补齐项目运行时 | 锁定 Python/Node/Java/Neo4j，并提供 ALFWorld 重建配方 | 下载和存储增加，版本维护需要持续投入 |
| ③ 容器部署 | 把应用及服务环境放入容器镜像 | Web 比较适合；THOR/GPU、显示和持久卷权限仍需专门验证 |

建议先做①，逐步补②。当前没有证据要求立即容器化，也不建议为减少依赖直接拆掉不理解的模块。涉及权限语义、部署边界或运行时重组的修改，需要先形成正式方案。

## 15. 验证范围和交接说明

此次检查了根 pyproject 的 32 个核心依赖，包括主源码、vendored MindMemOS、脚本和测试的导入，以及动态加载、包名映射和锁图。没有审计全部前端传递依赖。

环境审查没有启动生产数据库或调用模型。依赖验证运行了两轮测试，其中创建并重开临时本地 Qdrant；Neo4j 和 provider 不能算真环境通过。没有安装或卸载服务器依赖，没有修改真实配置或权限。

接手时尚未完成：干净环境删除候选包后的验收、真实记忆写入/查询全链路、新机断网安装、原始权限报错定位。

本文件作为问题与验收依据；后续进度继续维护到项目已有 `docs/session-handoff.md`，避免这份审查快照和另一个活文档给出互相冲突的当前状态。完成某项后，在本文件标明修复引用和验收证据即可。


## 可复跑的只读诊断命令

以下命令以项目根目录为 cwd；sync 保留 --dry-run，不执行真实卸载。输出中的路径、版本与数量是审查时快照，未来可能变化。

```bash
/home/haodong2/.local/bin/uv --version
/home/haodong2/.local/bin/uv sync --frozen --dry-run --offline
/home/haodong2/.local/bin/uv sync --frozen --dry-run --offline --extra browser
node --version
node web/node_modules/typescript/bin/tsc --version
.runtime/java/bin/java -version
./scripts/homemaster doctor --json
stat -c '%a %U %n' config/homemaster.yaml config/homemaster.browser.yaml
ls -l .runtime
```

注意：不把 setup.sh 当成常规只读诊断重跑。此前其在找不到 uv 时提前退出；修好 PATH 后它会真实同步依赖和修改运行目录。

## 导入阻断测试的完整复跑脚本

此脚本不卸载依赖；临时文件由 pytest 创建。现有 package metadata 仍存在，不能替代空环境验证。对生产配置或共享数据不执行读写。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PYTEST'
import sys
import importlib.abc

blocked = {"posthog", "traced", "sqlalchemy", "fastembed"}

class BlockUnusedCandidates(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in blocked:
            raise ModuleNotFoundError("audit blocked " + fullname, name=fullname)

sys.meta_path.insert(0, BlockUnusedCandidates())
import pytest
raise SystemExit(pytest.main([
    "-q", "-p", "no:cacheprovider",
    "tests/homemaster/memory/test_mindmemos_runtime.py",
    "tests/homemaster/test_cli_help.py",
    "tests/homemaster/test_provider_transports.py",
    "-m", "not live_api and not live_alfworld and not live_mcp and not stress",
]))
PYTEST
```

第一次测试 blocked 集合不含 fastembed，第二次包含它。两次都返回 0、23 passed；同源测试重复不被计为两个独立外部验收门。
