# HomeMaster V3.1 通用浏览器工具层 Spec

日期：2026-08-25
状态：调查后形成的架构与协议规格，等待实施
真理源：本文件定义 V3.1 通用浏览器工具的范围、公开工具、调用协议和验收标准。
基线：V2.1 通用 BrowserSession + Playwright；远程当前 OpenCLI 1.8.7 的可复用浏览器内核按本 Spec 原样 vendoring，但不引入其 daemon、Extension 或浏览器 owner 运行时。

## 1. 目标

V3.1 将 HomeMaster 从“受控 fixture 上的少量动作”提升为“能够现场理解并操作未见过的常规 DOM 网页”的通用浏览器执行层。目标不是承诺所有网页都可操作，而是覆盖遵循 HTML/ARIA 语义的后台系统、表单、表格、菜单、弹窗和常见复合控件。

必须达到：

1. 普通网页不需要站点名、路由名、`data-bid` 或页面专用分支即可发现目标。
2. 模型可以用语义目标直接发起动作，也可以先取得稳定引用再执行动作。
3. DOM 重渲染后，目标在唯一且身份一致时可被 `stable` 或 `reidentified` 恢复。
4. 零匹配、歧义、禁用、遮挡、不可编辑和终态不满足时返回结构化错误，不猜测、不默认选择第一个。
5. 每个写操作以真实 DOM、下载文件、弹窗状态或 URL 等外部终态作为成功依据。
6. Playwright BrowserSession 统一管理浏览器生命周期、页面、标签页、录屏、trace、权限和清理。
7. 动作范围覆盖 OpenCLI 常用操作，并保持 HomeMaster 的权限、日志和终态验证约束。

## 2. 调查依据

### 2.1 当前问题

- 当前工具包含 navigate、inspect、fill、select、check/uncheck、click、wait、截图回填和 observe。
- `browser_inspect` 主要收集固定交互选择器；accessible name、label、placeholder 和 visible text 被合并为一个优先级字段。
- `name/label/text` 默认是大小写不敏感 substring，没有 exact 语义。
- 空匹配仍是“成功 inspect”，没有 `target_not_found`、候选建议或终止信号。
- 动作后快照立即失效，`next_snapshot` 只能 review；旧 `SnapshotStore` 没有跨重渲染恢复。
- `browser_backfill` 把当前页面 PNG 粘贴到专用图片控件；它虽是特定图片回填工作流而不是通用网页动作，仍按用户决定保留为正式工具。

### 2.2 运行证据

- Run 27、28 多次出现 `browser.reference_protocol_rejected`，模型复用了旧 `snapshot_id/element_id`。
- Run 29 中页面可见秒数为 `07`，accessible name 为 `second 7`；请求 `second 07` 返回空，裸查询 `07` 又命中日期单元格，工具没有候选或近似匹配，模型进入长时间重复推理。
- Run 30/31 的主要阻塞是 Ant Design Pro 构建错误 `Can't resolve 'umi'`，不是浏览器动作能力证据。
- Run 32 在环境正常后成功完成同一变更单的三个 involved SOP、外部执行、独立 terminal 验证和前后取证，证明该 Ant Design Pro 任务是可执行的真实回归基线；V3.1 必须按 9.4 用新协议重跑同一业务任务。

### 2.3 OpenCLI 参考边界

OpenCLI 的通用性主要来自 DOM/AX snapshot、semantic find、fingerprint resolver、compound controls、结构化错误和丰富动作面。对其中技术上可独立复用的实现，HomeMaster 默认直接原样复制并调用，不再把“仅阅读后自行重写”作为首选；不运行 OpenCLI daemon、Chrome Extension、profile takeover 或 tab lease。

### 2.4 远程 OpenCLI 参考安装

V3.1 实施时优先参考 `hkust4` 上这份已安装源码，而不是凭记忆重写算法：

- 命令入口：`/home/haodong2/.nvm/versions/node/v22.22.1/bin/opencli`；
- 入口是软链，目标为：`../lib/node_modules/@jackwener/opencli/dist/src/main.js`；
- 解析后的入口：`/data1/haodong2/.nvm/versions/node/v22.22.1/lib/node_modules/@jackwener/opencli/dist/src/main.js`；
- 实际包目录：`/data1/haodong2/.nvm/versions/node/v22.22.1/lib/node_modules/@jackwener/opencli/`；
- 2026-08-25 实际核对版本：`1.8.7`；
- 运行时版本：Node `v22.22.1`；
- 许可证：Apache-2.0。

服务器默认 `node` 版本过旧。直接执行上述绝对 `opencli` 入口仍通过 `/usr/bin/env node` 解析解释器，因此参考或运行前必须显式把 Node 22 放在 `PATH` 首位：

```bash
export PATH=/home/haodong2/.nvm/versions/node/v22.22.1/bin:$PATH
opencli --version
```

当前验证输出为 `1.8.7`。版本可能继续升级；开始实施时必须重新执行版本核对并记录版本，不能把此前本机安装的 `1.8.6` 行为直接假定为远程当前行为。

### 2.5 OpenCLI 源码参考映射

| HomeMaster 能力 | 优先参考的 OpenCLI 文件 |
| --- | --- |
| DOM snapshot、可见性、Shadow DOM、iframe、表格、滚动区和 diff | `dist/src/browser/dom-snapshot.js` |
| role/name/label/text/testid 语义查找和结构化候选 | `dist/src/browser/find.js` |
| numeric ref、fingerprint、exact/stable/reidentified 和 CDP 点击测量 | `dist/src/browser/target-resolver.js` |
| date/time/datetime/select/file 等复合控件信息 | `dist/src/browser/compound.js` |
| Accessibility snapshot | `dist/src/browser/ax-snapshot.js` |
| click/type/hover/focus/drag/scroll/upload/wait/screenshot 等动作编排 | `dist/src/browser/base-page.js` |
| daemon/Extension page facade、tab/dialog/download 协作方式 | `dist/src/browser/page.js` |
| CDP 输入、页面与浏览器底层命令 | `dist/src/browser/cdp.js` |
| 目标失败码和错误 envelope | `dist/src/browser/target-errors.js` |
| title/URL/text/value/attributes/HTML tree 读取 | `dist/src/cli.js` 的 `get` commands、`dist/src/browser/html-tree.js` |
| 正文去噪、Markdown 和 cursor 分块 | `dist/src/browser/extract.js`、`article-extract.js` |
| console 时间窗和 cursor 观察 | `dist/src/cli.js` 的 `console` command、`dist/src/browser/base-page.js` |
| 页面反爬、渲染模式和 API 候选诊断 | `dist/src/browser/analyze.js` |
| 网络观察与稳定 key/cache | `dist/src/browser/network-interceptor.js`、`network-key.js`、`network-cache.js` |
| 下载管线 | `dist/src/download/` 和 `dist/src/pipeline/steps/download.js` |

“参考”包括优先原样复制并复用实现、测试和错误语义；只有无法从 HomeMaster Playwright owner 下直接执行的宿主集成层才另写 adapter。HomeMaster 应尽量保留 OpenCLI 的通用能力，但仍由 Playwright Session 实现并统一执行权限、录屏、日志、终态验证和清理。

### 2.6 OpenCLI 1.8.7 原样 vendoring 决策

V3.1 锁定采用“上游源码原样 vendoring + HomeMaster adapter”的实现路线。只要一个 OpenCLI 浏览器能力在 HomeMaster-owned Playwright session 中技术上可复用，就优先复制上游完整文件及其依赖闭包，不按函数摘抄、不凭记忆重写、不为了代码风格重新格式化。项目是否商用不改变此技术决策；当前上游明确声明 Apache-2.0，因此复制时仍必须保留许可证、版权、来源和修改说明，保证归属清晰。

#### 2.6.1 固定目录和文件纪律

```text
src/homemaster/browser/vendor/opencli_1_8_7/
  LICENSE
  UPSTREAM.md
  PATCHES.md
  SHA256SUMS
  package.json
  dist/src/
    errors.js
    utils.js
    snapshotFormatter.js
    browser/
      base-page.js
      dom-helpers.js
      dom-snapshot.js
      find.js
      target-resolver.js
      compound.js
      html-tree.js
      ax-snapshot.js
      extract.js
      article-extract.js
      analyze.js
      network-interceptor.js
      network-key.js
      network-cache.js
      shape.js
      shape-filter.js
      target-errors.js
      utils.js
      visual-refs.js
      <上述文件的完整可执行依赖闭包>
      <对应的 *.test.js、fixture 和 e2e fixture，保持上游相对路径>

src/homemaster/browser/generated/opencli_1_8_7/
  <由锁定源码确定性构建、供 Playwright page.evaluate 使用的 bundle/manifest>
```

vendor 目录镜像 OpenCLI 包内原始相对路径，保证 ESM import 和 test fixture 不需要为了 HomeMaster 改写。以上清单是最低集合，不是限制复制的 allowlist。实施时从实际 import/export 图计算完整依赖闭包；一个上游文件只要有被复用的 export，就整文件复制。禁止将同一文件裁成若干无来源片段，禁止直接修改 `vendor/opencli_1_8_7/` 下的上游文件。确需行为差异时，在 HomeMaster adapter 或独立 patch 文件中实现，并在 `PATCHES.md` 逐项记录原因、行为差异、测试和上游对应位置。generated bundle 是 HomeMaster 构建产物，因此与未修改的 vendor 源码分目录保存。

`UPSTREAM.md` 至少记录包名 `@jackwener/opencli`、版本 `1.8.7`、仓库 URL、服务器源路径、复制日期、Node 版本和所选文件；`SHA256SUMS` 对 LICENSE、package.json、每个源码、测试、fixture 和生成物逐文件锁定。当前调查锚点为：

```text
package.json  44d0eb2ea36788423ddfb079b2d089d326146aab78b792178a33f4a7e07b70ff
LICENSE       0210b8b66cf00358242cb921ba2be3a46dfe0190159b1b952388a3880ce1ff54
dist/src/browser/base-page.js           75ece611cdcd8b814edace141681939ecf3d6a8a048254f5b6483dd22fc218c0
dist/src/browser/dom-helpers.js         5c6825c63d268b04c49373f4293eecf675aa4f97f16ddb92bfd4f9897f23a2f9
dist/src/browser/dom-snapshot.js        9b9dbd40af4a2c669879669dde2256c2fb22418298740e4391e3d001e060418b
dist/src/browser/find.js                df559b17d214cc2e7b39168ccdab1ee17cb93f46ba4010557a5eb3946b022f16
dist/src/browser/target-resolver.js     5f35210690782eec2a738f6a61a916b355c249fcfb797409a31f8f676234bc02
dist/src/browser/compound.js            f13d79454e58d72769b913a1e5e50a3b481901464548951f94fbf4cbef8b1f35
dist/src/browser/html-tree.js           d8e8d165233d303609da4f9dfd9c4f76318d48ade2df5299545866cd3fa90fe2
dist/src/browser/ax-snapshot.js         40d7d84c171dd6499a368a0dfa9f198a958b5da8a37467e2c95922518d29f536
dist/src/browser/extract.js             3eb407df596cb447c49b2ef4162d185ebf354b511a2eae40817d96ebb89d1481
dist/src/browser/article-extract.js     05138c1ef4be8422ef7e4d922122c6e44375bd51554c2bd038c4dce49532dc33
dist/src/browser/network-interceptor.js 6b87cf193c9f3fa6dc7ff7318f5d6ad7cbd4da7267c22c19c8e55b4c1c0ea8d4
dist/src/browser/network-key.js         5a85af09fcf56fcf90fc0fa289730c53cd0313f0155a119971485d40cac5d6c6
dist/src/browser/network-cache.js       1dd707f472f855988dd75b2ed739704b4ad45f1ad2411ccb9ee4f7f1f7e7b7b4
dist/src/browser/shape.js               2eb449d51996f5dbf271cedbc950d8590c029fac6e56ee569d0be429ecf5c58f
dist/src/errors.js                      7cd58c26c575443d72c597f9a1a7e48228033c10a7356f3e6020a5891e34a539
dist/src/utils.js                       d2b2b7e5a1f447757fd029e34d6e5484fe6948227ec099d0a059f8593cfb534d
dist/src/snapshotFormatter.js           e6f19d1330104292b777f0e3560ab0fb29811976a1655a7e2ac0878644746f53
```

正式复制时必须从源目录重新计算所有 hash，并先断言包版本仍为 `1.8.7`；若版本或任一预期 hash 已变化，停止复制、更新 capability diff 和版本目录，不能把两个上游版本混在 `opencli_1_8_7` 中。

#### 2.6.2 Adapter、loader 和 owner 边界

```text
HomeMaster typed input
  -> opencli_adapter.py: origin/frame/policy/permission validation
  -> opencli_scripts.py: 从 package data 加载锁定 bundle 和 manifest
  -> Playwright page.evaluate / locator / input API
  -> vendored snapshot/find/resolver/compound/extract 算法
  -> HomeMaster normalize result/error envelope
  -> 独立 DOM/file/network/download 外部终态读回
  -> HomeMaster JSONL evidence
```

HomeMaster 新增的集成代码固定放在：

```text
src/homemaster/browser/opencli_scripts.py
src/homemaster/browser/opencli_adapter.py
scripts/build_opencli_browser_vendor.mjs
```

构建脚本只负责把未修改的 ESM 源码及 HomeMaster 自有入口确定性打包为可由 Python Playwright 注入的 page-side bundle，不得悄悄改写上游语义。Node 22 只允许作为 vendor bundle 的构建/校验工具；HomeMaster 生产运行时不能要求启动 OpenCLI CLI、daemon、Bridge 或第二个 Node 浏览器会话。Node-only 的 cache、Readability 或 Markdown helper 若不能进入 page bundle，原始文件仍原样保留，实际宿主 I/O 由 adapter 接到 HomeMaster artifact/cache 抽象，不能继续写 `~/.opencli`。

Playwright BrowserSession 始终是唯一页面、context、tab、dialog、download、network listener 和浏览器生命周期 owner。vendored 代码只提供纯 snapshot、查找、恢复、序列化、提取和可复用动作算法；它无权自行启动 Chrome、创建第二个 session、绕过 policy、直接向模型暴露 CSS/CDP/eval，或自行判定业务成功。

#### 2.6.3 不接入运行时的 OpenCLI 部分

以下模块可以作为上游源码证据阅读，混合文件也可因依赖闭包而原样归档，但不得被 HomeMaster runtime import、启动、注册为工具或取得浏览器所有权：

- daemon lifecycle/transport/client 和 Node process lifecycle；
- Chrome Extension、Bridge、bind/unbind/close tab lease；
- existing Chrome/profile/login-state takeover；
- OpenCLI CLI parser、adapter `init/verify` 和 adapter registry；
- OpenCLI `Page`/session owner 实现；
- raw CDP endpoint、cookie/session lease 或任意宿主路径作为模型可见 API。

这不是对 OpenCLI 通用网页能力的裁剪：用户已接受 HomeMaster 自己启动 Chromium 并由用户登录，以上部分解决的是“接管用户已有浏览器”，不是页面理解和操作。若某个动作算法当前与 `base-page.js`、`page.js` 或 `cdp.js` 耦合，先整文件复制用于可追溯复用，再通过 adapter 隔离算法 export；不得因此把第二套 page owner 带入运行时。

#### 2.6.4 上游测试、打包和验收门

1. 每个复制模块必须同时复制对应上游 test/fixture；先在未修改源码上跑上游测试，建立与 OpenCLI 1.8.7 相同的行为基线。
2. HomeMaster adapter 测试必须把同一 fixture 分别送入上游基线和 HomeMaster Playwright 路径，逐实例比较 snapshot、候选、ref/fingerprint、恢复等级、compound 信息和结构化错误；不允许只比较一个最优样本。
3. 上游测试通过只证明复制没有损坏，不能替代 9.2 的真实外部终态黑盒门、返回码和 Run 32 回归。
4. 增加 license/provenance/hash audit：任何 vendored 文件缺少 manifest、hash 不符、上游文件被直接修改或依赖闭包缺失时构建失败。
5. 构建 wheel/sdist，安装到源码 checkout 外的空环境，断言 `LICENSE`、`UPSTREAM.md`、`PATCHES.md`、`SHA256SUMS`、源码、测试 fixture 和 generated bundle 均存在且 hash 正确；随后在该安装环境启动真实 Playwright 页面，至少完成一次 snapshot、find、click 和 DOM 终态读回。
6. wheel 安装态不得依赖远程 OpenCLI 安装目录、全局 npm package、`~/.opencli` 或开发机源码路径；卸载/移走全局 OpenCLI 后同一安装态黑盒仍须 PASS。

### 2.7 OpenCLI 通用能力保留纪律

V3.1 的目标不是只实现本 Spec 当前列出的十几个工具，而是尽可能保留已安装 OpenCLI 的通用浏览器能力。实施前必须以远程当前版本的 CLI help、browser 源码和测试为依据生成完整 capability matrix，并对每一项标记：

- `implemented`：HomeMaster 已有等价能力和外部终态黑盒证据；
- `planned`：纳入 V3.1 的明确 Phase、接口和验收；
- `bridge_only`：只服务 OpenCLI daemon/Extension/接管已有 Chrome，HomeMaster-owned 浏览器不需要；
- `unsupported`：存在安全或技术边界，附具体证据、替代路径和用户确认的 trade-off。

禁止将未盘点能力静默遗漏，禁止仅因当前演示页面没有使用就删除某项通用能力。任何从 OpenCLI 通用能力面的主动裁剪都属于用户主导的结构性决策，实施者必须先列出代价并取得确认。

能力矩阵必须同时覆盖：

1. 页面表征和增量 snapshot；
2. semantic/CSS find 中可安全移植的查找语义；
3. target ref、fingerprint 和恢复；
4. compound form controls；
5. 鼠标、键盘、表单、滚动、拖拽、文件和 clipboard image backfill 动作；
6. iframe、Shadow DOM、tab、popup 和 dialog；
7. page property、form state、HTML tree、正文提取和分块；
8. screenshot、AX、console、network、download 和 wait；
9. history、页面诊断和受 policy 控制的 JavaScript escape hatch；
10. 错误 envelope、候选、歧义和动作后验证。

OpenCLI 能力平价的判据是行为和外部终态，不是函数名数量相同。HomeMaster 可以使用不同公开工具划分，但不能用一个空壳工具宣称覆盖多个未验证能力。

### 2.8 V3.1 必须交付的 OpenCLI 通用能力基线

以下能力全部是 V3.1 DoD，不是可选增强。V3.1 完成时，它们在 capability matrix 中必须为 `implemented` 且具有逐能力外部终态黑盒证据；不得标成 `planned`、`bridge_only` 或 `unsupported` 后仍宣称完成。若发现平台限制，V3.1 状态应为 blocked，除非用户明确修改范围。

- DOM snapshot 和 Accessibility snapshot；
- role、name、label、text、testid 语义查找；
- exact、contains、候选返回和歧义拒绝；
- numeric/stable target ref 和 fingerprint；
- exact、stable、reidentified 目标恢复；
- Shadow DOM、policy 允许的 iframe、表格和滚动容器；
- date、time、datetime、select、file 等 compound controls；
- click、type、fill、hover、focus、double click；
- checkbox、radio、switch 的幂等状态动作；
- 单键和组合键快捷键；
- scroll 和 scroll into view；
- bounded auto-scroll；
- upload 和 drag and drop；
- 当前页面 PNG 的 clipboard image backfill，并核对 source/rendered SHA-256；
- wait text、语义/CSS selector、time、XHR/response、DOM stable 和 download；
- screenshot、full-page screenshot、annotated refs 和独立 AX snapshot；
- tab 管理和 dialog 处理；
- back、forward 和 reload；
- title、URL、text、value、attributes、HTML tree 和 form state 读取；
- 正文去噪、Markdown 转换和长页面 cursor 分块；
- console、network 和 download 观察；
- 页面反爬/渲染/API 候选诊断；
- policy 显式授权、全量审计的 page-context JavaScript escape hatch。

同一能力可以由不同于 OpenCLI 的工具名承载，例如 double click 由 `browser_click(click_count=2)` 提供；但行为、失败语义和外部终态必须等价且可验证。一个模糊工具名不得与一个明确工具名并列承载同一模型能力；V3.1 将截图能力统一命名为 `browser_screenshot`，不同时暴露 `observe`。

### 2.9 OpenCLI 1.8.7 browser 命令映射

以下映射来自 2026-08-25 对远程 `opencli browser <session> help <command>`、公开 `IPage` 和关键源码的逐项审计，不是按命令名推测：

| OpenCLI 命令 | OpenCLI 原始作用摘要 | V3.1 对应 | 决策 |
| --- | --- | --- | --- |
| `analyze` | classify anti-bot、API candidates、pattern、next step | `browser_analyze` | 保留通用诊断，删除 adapter 推荐 |
| `back` | go back in browser history | `browser_history(action=back)` | 覆盖并增加 forward/reload |
| `bind` | bind existing Chrome tab/window | 无 | `bridge_only`，用户已决定不接管已有 Chrome |
| `check` | ensure checkable control is checked | `browser_check` | 覆盖 |
| `click` | click numeric ref、CSS 或 semantic target | `browser_click` | 覆盖；写动作不直达 CSS |
| `close` | release Extension tab lease | 无 | `bridge_only` |
| `console` | read recent console messages | `browser_console` | 覆盖，cursor 取代无限 follow |
| `dblclick` | double-click target | `browser_click(click_count=2)` | 等价覆盖 |
| `dialog` | accept/dismiss JavaScript dialog | `browser_dialog` | 覆盖并要求 listener 先于 trigger |
| `drag` | drag one target to another | `browser_drag` | 覆盖 |
| `eval` | execute JavaScript in page/frame context | `browser_eval` | gated 覆盖，独立高权限 capability |
| `extract` | extract Markdown in paragraph-aware chunks | `browser_extract` | 覆盖 |
| `fill` | set exact editable value and verify | `browser_fill` | 覆盖 |
| `find` | CSS/semantic find with structured matches | `browser_find` | 覆盖并增加 exact/ambiguity 纪律 |
| `focus` | focus target | `browser_focus` | 覆盖 |
| `frames` | list cross-origin iframe targets | `browser_inspect(view=frames)` | 覆盖 |
| `get` | title/URL/text/value/HTML/attributes | `browser_read` | 覆盖并增加 form state |
| `hover` | move mouse over target | `browser_hover` | 覆盖 |
| `init` | generate adapter scaffold | 无 | `adapter_dev_only`，不是网页运行时能力 |
| `keys` | press key or shortcut | `browser_press` | 覆盖 |
| `network` | shape preview、stable key、detail body | `browser_network` | 覆盖，bounded cursor 取代无限 follow |
| `open` | open URL in session | `browser_navigate` | 覆盖 |
| `screenshot` | viewport/full-page/annotated screenshot | `browser_screenshot` | 覆盖 |
| `scroll` | scroll page up/down | `browser_scroll(mode=by)` | 覆盖并增加 container/into-view/auto |
| `select` | select dropdown option | `browser_select` | 覆盖；native 与 ARIA 分开验收 |
| `state` | DOM/AX page state with numeric refs | `browser_inspect` | 覆盖 |
| `tab` | list/new/select/close tabs | `browser_tabs` | 覆盖 run-owned tabs |
| `type` | select current content then type with browser input | `browser_type(mode=replace)` | 等价覆盖并增加 append mode |
| `unbind` | detach Extension session | 无 | `bridge_only` |
| `uncheck` | ensure control is unchecked | `browser_uncheck` | 覆盖 |
| `upload` | attach local files to file input | `browser_upload` | 覆盖，路径改为受控 artifact ref |
| `verify` | execute/validate an adapter fixture | 无 | `adapter_dev_only` |
| `wait` | selector/text/time/XHR/download wait | `browser_wait` | 覆盖并增加 DOM stable、URL、state、popup/dialog |

OpenCLI `IPage` 中未单列为 CLI command 的 `getFormState`、`autoScroll` 和 `annotatedScreenshot` 分别映射到 `browser_read(kind=form_state)`、`browser_scroll(mode=auto)` 和 `browser_screenshot(annotate_refs=true)`。`getCookies`、Node-side `fetchJson`、raw `cdp` 和 Extension lease API 不进入模型浏览器工具面；其中 CDP 可作为 Playwright owner 内部实现细节。

## 3. 锁定架构

### 3.1 单一浏览器 owner

`PlaywrightBrowserSession` 是唯一浏览器 owner，负责：

- 启动/关闭 Chromium、context、page、tabs；
- 执行 DOM、输入、鼠标、键盘、滚动、上传、下载和弹窗动作；
- 持有页面、登录态、下载目录、视频和 trace；
- 执行 origin/frame/popup policy、动作串行化、超时 fence 和清理；
- 将动作入参、解析等级、外部读回、失败码和 evidence refs 写入既有 JSONL。

OpenCLI 不是第二个 owner。必要时 Playwright 后端内部可使用 CDP session 发送真实鼠标/键盘事件，但模型不可见 CDP endpoint、任意 CSS 写执行或终端。raw JavaScript 只通过默认不注册、policy 显式授权的 `browser_eval` 暴露，绝不作为普通动作的隐式 fallback。

### 3.2 统一执行管线

```text
语义目标或 target_ref
  -> DOM/AX snapshot 获取候选
  -> exact/stable/reidentified resolver
  -> 可见、可用、控件类型、origin/frame、歧义检查
  -> Playwright 原子动作
  -> 外部终态读回
  -> structured receipt/error + JSONL evidence
```

不得按网站、框架或路由增加分支；Ant Design、MUI、Radix 只能通过共同的 ARIA/DOM 行为适配。

## 4. 目标协议

### 4.1 语义目标

写工具可以直接接收语义目标，不再强制模型先单独 inspect：

```json
{"target":{"role":"button","name":"确认执行","match":"exact"}}
```

支持字段：

| 字段 | 作用 |
| --- | --- |
| `role` | ARIA role 或推导出的 native role |
| `name` | accessible name，独立于 visible text |
| `label` | label/labelled control 文本 |
| `text` | 元素可见文本 |
| `testid` | allowlist 保护的 test id，不能任意 CSS |
| `match` | `exact`（写操作默认）、`contains`、`regex`（仅读操作） |
| `nth` | 明确的候选序号；写操作歧义时必须显式提供 |
| `frame_ref` | 已验证的 frame ref；默认主 frame |
| `target_ref` | snapshot/find/screenshot annotation 返回的稳定引用，与语义字段二选一 |

写操作默认只允许唯一 exact 目标。contains、regex 和多候选返回 ambiguity，除非显式提供 nth 且身份/安全检查仍通过。

### 4.2 引用恢复等级

snapshot 为每个目标返回 `target_ref` 和 fingerprint，至少包含 tag、role、accessible name、visible text 摘要、label、id/testid、frame 身份和控件类型。

- `exact`：引用和当前 DOM 节点完全一致；
- `stable`：重渲染后 tag、frame、强身份字段一致；
- `reidentified`：原节点卸载，但当前 DOM 有唯一 fingerprint 等价目标；
- `stale_ref`：身份冲突或恢复不唯一，动作不执行；
- `target_not_found`：无匹配，返回有界候选和重试建议；
- `target_ambiguous`：多匹配，返回候选差异，不默认点第一个。

导航、跨 frame、origin 变化或身份冲突使旧引用失效；普通轻量重渲染不应机械失效全部引用。

### 4.3 结构化失败

```json
{
  "ok": false,
  "error": {
    "code": "target_not_found",
    "requested": {"role": "option", "name": "second 07", "match": "exact"},
    "candidates": [
      {"target_ref": "target-42", "name": "second 7", "text": "07", "reason": "visible_text_exact"}
    ],
    "retry": "确认候选后使用 target_ref target-42"
  }
}
```

空列表不能再作为成功 inspect；相同查询达到 runtime 阈值时必须中止循环并保留诊断证据。

## 5. 公开工具、模型描述和作用

工具名称、模型可见 description、参数 description、作用边界和成功终态都是 V3.1 协议的一部分。实现不能只复用本节工具名而另写一句更短、更模糊的 description。

### 5.1 description 编写纪律

每个模型可见工具描述固定回答五件事：

1. 工具实际执行什么动作；
2. 模型应在什么场景调用；
3. 它与最容易混淆的相邻工具有何区别；
4. 目标引用、frame/tab、权限或一次调用有哪些约束；
5. 成功 receipt 证明哪一个浏览器外部终态。

描述第一句优先保留 OpenCLI 1.8.7 对应命令的动作语义，例如 “Get page properties”、“Extract page content as markdown”、“Read recent browser console messages” 和 “Capture network requests as shape previews; retrieve full bodies by key”，再加入 HomeMaster 的 resolver、policy 和终态约束。参数 description 不能只写字段类型；必须说明值的来源、匹配语义、单位、上限、默认值和不适用场景。

同一模型能力只暴露一个公开名称。`browser_screenshot` 取代 `observe`，不能并列注册两个截图工具；double click 由 `browser_click(click_count=2)` 提供，不另设 `browser_dblclick`；check/uncheck 因最终目标状态不同继续保留两个明确工具。

### 5.2 页面、目标发现和读取

#### `browser_navigate`

模型可见 description：

> Open one policy-allowed absolute HTTP(S) URL in the current browser tab. Use this when the required page is not already open; use `browser_history` for back, forward, or reload and `browser_tabs` when a separate tab is required. Success reports the requested and final URL, title, response status when observable, redirects, active tab, page generation, and evidence; it does not by itself select an element for a later action.

主要输入为绝对 `url`、可选 `wait_until` 和 `timeout_ms`。相对 URL、`file:`、`chrome:`、extension URL 和 policy 未授权 origin 在启动导航前失败。导航超时且无法确认最终页面状态时返回 `outcome_unknown`，不得自动重发。

#### `browser_history`

模型可见 description：

> Move the current tab through browser history with `back`, `forward`, or `reload`. Use this only for history traversal in the active tab; use `browser_navigate` for a new URL and `browser_tabs` for another tab. Success reports the action, previous and final URL, title, page generation, and whether a navigation actually occurred.

主要输入为 `action=back|forward|reload`、可选 `wait_until` 和 `timeout_ms`。无可用 history entry 返回 `changed=false`，不能伪装成完成导航。

#### `browser_inspect`

模型可见 description：

> Read a bounded DOM, Accessibility, or hybrid snapshot of the current page and assign stable target refs to actionable elements. Use this to understand an unfamiliar page, enumerate frames, tables, scroll containers, Shadow DOM, and compound controls, or compare a later snapshot with an earlier one. Use `browser_find` for a focused semantic query, `browser_read` for one exact property, `browser_extract` for long-form page content, and `browser_screenshot` for visual layout. This tool never changes the page.

主要输入为 `view=dom|ax|hybrid|frames`、可选 `scope`、`frame_ref`、`interactive_only`、`actionable_only`、`limit` 和 `diff_from`。输出分别保留 `accessible_name`、`label`、`visible_text`、`value`、可见/启用/遮挡状态、compound 信息和 `target_ref`；密码、token、Cookie 和不受限正文不得进入 snapshot。

#### `browser_find`

模型可见 description：

> Find DOM elements by semantic role, accessible name, associated label, visible text, test id, or a read-only CSS selector and return structured candidates with stable target refs. Use this when you know what kind of element you need but do not yet have a trustworthy ref. Exact matching is the default for actions; contains matching is for discovery, and ambiguous results are returned for narrowing instead of silently choosing the first element. This tool does not click or modify a match.

主要输入为 `role`、`name`、`label`、`text`、`testid`、受限 `css`、`match=exact|contains|regex`、`nth`、`frame_ref`、`limit` 和 `text_max`。`regex` 仅允许读取；`nth` 只能选择本次结果中的明确候选。零匹配返回 `target_not_found` 及有界近似候选，多匹配返回 `target_ambiguous`。

#### `browser_read`

模型可见 description：

> Get one page property or one resolved element value as structured data. Use `kind=title|url|text|value|attributes|html|form_state` when you need an exact readback for planning or verification rather than a broad snapshot. Use `browser_find` first when the target is unknown, and use `browser_extract` instead of raw HTML when the goal is to read a long article or document. This tool never changes the page and never treats the first of several action targets as uniquely identified.

`title`、`url` 和 `form_state` 不要求 target；`text`、`value`、`attributes` 要求语义 target 或 `target_ref`；`html` 支持 policy 允许的只读 scope、`format=html|tree`、`max_chars`、`max_depth`、`children_max` 和 `text_max`。所有输出有界；密码字段值不返回。读取多匹配时返回真实 `matches_n`，只有显式 `nth` 才读取其中一个候选。

#### `browser_extract`

模型可见 description：

> Extract the readable content of the current page as cleaned Markdown and return a paragraph-aware bounded chunk. Use this for articles, documentation, reports, search results, or other long-form content that would be noisy or too large in a DOM snapshot. Continue a long page with the returned `next_start_char`; use `browser_read` for exact element properties and `browser_inspect` for actionable controls. This tool does not execute page actions or grant an action target.

主要输入为可选只读 `scope`、`chunk_size`、`start_char` 和 `frame_ref`。输出至少包含 URL、title、Markdown chunk、`start_char`、`next_start_char`、`has_more`、抽取来源和 evidence。无可读根节点、非法 scope 或 cursor 越界均返回结构化错误。

#### `browser_screenshot`

模型可见 description：

> Take a PNG screenshot of the current browser page without changing it. Use this to inspect layout, images, charts, canvas content, visual obstruction, or controls whose DOM semantics are insufficient. Set `annotate_refs=true` to overlay visible target-ref labels that correspond to the returned ref map; otherwise the image alone grants no action reference. Use `browser_backfill` only when the current page screenshot must be pasted into an editable image-backfill control.

主要输入为 `full_page`、可选 `width`、`height`、`annotate_refs` 和 `frame_ref`。输出包含 PNG artifact、实际尺寸、active tab/frame、page generation 和 evidence；标注模式还返回本次图片标签到 `target_ref` 的映射。`full_page=true` 时忽略 `height`，尺寸必须有明确上限。

#### `browser_console`

模型可见 description：

> Read recent browser console messages as bounded structured records. Use this to diagnose page errors, warnings, failed scripts, or application logs; it is not a substitute for DOM state or network responses. Filter by level or time window and continue with the returned cursor instead of requesting an unbounded live stream. This tool never executes console JavaScript.

主要输入为 `level=all|error|warning|log|info|debug`、`since_ms`、`until_ms`、`cursor` 和 `limit`。输出保留 level、text、timestamp、source、active tab/frame 和 next cursor，并执行字段/大小边界；不提供无限 `follow`。

#### `browser_analyze`

模型可见 description：

> Analyze an unfamiliar page and return evidence-backed signals about rendering pattern, known anti-bot challenges, likely real-data API responses, and the next browser observation to try. Use this for diagnosis when ordinary inspect, extract, or network observation cannot explain an empty, blocked, or dynamically loaded page. It does not bypass anti-bot checks, choose a site-specific adapter, or modify the page beyond the requested navigation.

主要输入为可选 `url`、`settle_ms` 和 network observation budget。输出包含 requested/final URL、rendering signals、anti-bot vendor/evidence、scored API candidates、initial-state signals 和 bounded recommendation；移除 OpenCLI 面向 adapter 开发的 `nearest_adapter`。

### 5.3 元素动作

以下动作均接受统一 `target`：语义目标或 `target_ref`，并可携带已验证的 `frame_ref`/`tab_ref`。写操作默认要求唯一 exact 目标；resolver 可以报告 `stable` 或 `reidentified`，但身份冲突、不可见、禁用、readonly 或滚入视口后仍被遮挡时不得执行。

#### `browser_click`

模型可见 description：

> Click one exact button, link, tab, option, date cell, or other non-editing target and verify the resulting browser or element state. Use a more specific tool for text entry, selection, checking, upload, drag, dialog, download, or image backfill. Set `click_count=2` for a double click; do not issue two separate clicks. Ambiguous, disabled, or obscured targets fail without choosing the first match.

主要输入为 `target`、`button=left|middle|right`、`click_count=1|2` 和 modifiers。receipt 包含 match level、click method、hit-test、retargeting、URL/tab/popup/dialog 变化和目标状态读回；无法确定点击是否到达页面时返回 `outcome_unknown`。

#### `browser_fill`

模型可见 description：

> Set one editable input, textarea, contenteditable, or supported date/time control to an exact value and verify the final DOM value. Use this when the final value matters and existing content should be replaced without simulating every keystroke. Use `browser_type` when the page must receive real key/input events or when text should be appended. Readonly, format-incompatible, or non-editable targets fail explicitly.

主要输入为 `target` 和完整 `value`。日期、时间、datetime-local、month 和 week 使用 inspect/find 返回的 compound format/min/max；成功必须 exact readback `actual == expected`。

#### `browser_type`

模型可见 description：

> Focus one editable target and enter text through real browser input events. Use `mode=replace` to select the current content before typing, matching OpenCLI `type`, or `mode=append` to keep the current value and add text at the active caret/end. Use `browser_fill` for deterministic exact assignment that does not require per-key behavior. Success reports the previous value, requested mode, inserted text, and verified final value.

主要输入为 `target`、`text`、`mode=replace|append` 和可选 `delay_ms`。默认 `mode=replace` 以保持 OpenCLI 1.8.7 行为；不得再把 `type` 固定描述成追加。对 autocomplete/rich editor 返回实际 input mode 和事件路径。

#### `browser_select`

模型可见 description：

> Select one exact option in a native select or a supported ARIA combobox/listbox and verify the selected value and visible label. Use this instead of manually clicking a dropdown and option when inspect/find reports a supported compound control. Supply an exact option label or value; ambiguous, disabled, missing, or unsupported custom-widget options fail without guessing.

主要输入为 `target`、`option` 或有序 `options`（级联/多选）、`match=label|value`。native select 与 ARIA combobox/listbox 分别实现和验收；未通过黑盒 fixture 的任意自定义 div 下拉框不得被笼统标成 supported。

#### `browser_check`

模型可见 description：

> Ensure one checkbox, switch, radio, or supported aria-checked control ends selected. Use this idempotent state action instead of clicking and guessing: an already selected target succeeds with `changed=false`. The receipt verifies checked, aria-checked, or selected state on the exact resolved control.

#### `browser_uncheck`

模型可见 description：

> Ensure one checkbox, switch, or supported aria-checked control ends unselected. Use this idempotent state action instead of clicking and guessing: an already unselected target succeeds with `changed=false`. Radio controls are rejected because a selected radio cannot be cleared independently in the normal group model.

#### `browser_hover`

模型可见 description：

> Move the browser pointer over one exact target to reveal hover-dependent UI such as a tooltip, menu, or action region. Use this only when hover itself is required; it does not click the target. Success verifies the target remains hovered and reports any requested visible postcondition.

主要输入为 `target`、可选 `duration_ms` 和可选 `expect`。只有鼠标移动 receipt、没有可观察 postcondition 时不得宣称 tooltip 或菜单已经出现。

#### `browser_focus`

模型可见 description：

> Focus one exact element without clicking or entering text. Use this before keyboard shortcuts or when the page behavior depends on focus; use `browser_type` when text entry is the goal. Success verifies the active element or equivalent focus state points to the resolved target.

#### `browser_press`

模型可见 description：

> Press one browser key or key combination such as Enter, Escape, Tab, ArrowDown, or Control+A. Provide a target when the shortcut must start from a specific element; otherwise it applies to the current focused element. Use `browser_type` for text and do not encode ordinary text as a sequence of key calls. Success reports focus and the requested observable page-state change.

主要输入为规范化 `key`、可选 `target`、modifiers 和可选 `expect`。组合键必须确定性解析，同一输入每次产生同一 CDP/Playwright key sequence。

#### `browser_scroll`

模型可见 description：

> Scroll the page or one scrollable container, bring one target into view, or perform a bounded auto-scroll for lazy-loaded content. Use `mode=by` with direction and pixels, `mode=into_view` with a target, or `mode=auto` with a bounded step count. Success reports before/after scroll positions or verified target visibility; reaching a scroll boundary returns `changed=false`.

主要输入为 `mode=by|into_view|auto`、可选 `container`、`target`、`direction=up|down|left|right`、`amount_px`、`steps` 和 `delay_ms`。auto-scroll 有总步数、时间和页面高度上限，不得无界滚动。

### 5.4 文件、剪贴板和拖拽

#### `browser_upload`

模型可见 description：

> Attach one or more approved artifacts to an exact file input and verify the page-visible file state. Use this for ordinary file upload; it reads only artifact-store references and never opens an uncontrolled native file chooser or accepts an arbitrary server path. Use `browser_backfill` when the required source is a fresh screenshot of the current page pasted through the clipboard protocol.

主要输入为 `target` 和 `artifact_refs`。执行前检查 file compound 的 `accept`、`multiple`、大小和类型；成功读回文件名、数量和可用的 preview/state，并逐 artifact 返回 receipt。

#### `browser_drag`

模型可见 description：

> Drag one exact DOM target to another exact target and verify the resulting order, position, or page state. Use this for sortable lists, boards, sliders with DOM handles, and drop zones; do not use it for file upload when `browser_upload` applies. Both source and destination must resolve uniquely and pass visibility and hit-testing checks.

主要输入为 `source`、`destination` 和可选 `expect`。receipt 分别返回 source/destination match level、实际输入路径和动作后终态；仅触发 drag events 不等于 drop 成功。

#### `browser_backfill`

模型可见 description：

> Capture the current browser page as a PNG and paste those exact image bytes into one editable image-backfill control that explicitly accepts clipboard images. Use this only when the workflow asks you to place a fresh screenshot of the current page into such a control. Use `browser_screenshot` when you only need to inspect or preserve an image, and use `browser_upload` for an existing file artifact. Success requires the target preview/content to match the pasted PNG bytes and reports both source and rendered SHA-256 values.

`browser_backfill` 是保留的正式公开工具，不是 deprecated alias。主要输入为 `target`、可选截图 viewport/full-page 参数；它固定执行“捕获当前页 -> 锁定 PNG bytes/hash -> 聚焦精确回填控件 -> clipboard paste -> 读取目标渲染内容/hash”。不得接受任意 `artifact_ref` 后伪装成 backfill，也不得因 clipboard item 数量、paste event 或任意 DOM 变化就返回成功。

### 5.5 标签页、弹窗、网络和等待

#### `browser_tabs`

模型可见 description：

> List, create, select, or close tabs owned by the current HomeMaster browser session. Use this when work must continue in a separate tab or a click opened a popup; use `browser_navigate` to change the URL in the active tab. Success returns the complete run-owned tab list with stable tab refs, URL, title, and the active tab.

主要输入为 `action=list|new|select|close`、可选 `tab_ref` 和 `url`。不能列出或接管用户其他浏览器的 tabs；关闭最后一个工作 tab 时必须有明确 policy。

#### `browser_dialog`

模型可见 description：

> Accept or dismiss a JavaScript alert, confirm, or prompt and report its type and message. When an action is expected to open a blocking dialog, provide that action as `trigger` so HomeMaster arms the dialog listener before executing it; otherwise handle the currently captured pending dialog. Use `prompt_text` only when accepting a prompt. Success means the exact dialog was handled, not merely that a listener was installed.

BrowserSession 从 page 创建时就安装 dialog listener 并保存唯一 pending dialog。主要输入为 `action=accept|dismiss`、可选 `prompt_text`、可选 `trigger` 和 timeout；多个并发 dialog、无 dialog、类型不符或 trigger outcome unknown 都结构化失败。

#### `browser_network`

模型可见 description：

> Capture browser network requests as bounded shape previews and retrieve one authorized response body by stable request ref. Use `mode=list` to inspect URLs, methods, status, timing, resource type, and JSON shape; use `mode=detail` only after selecting a returned request ref. Filter by fields, failure status, resource type, or time window, and continue with a cursor instead of an unbounded live stream. This tool observes traffic only and cannot send, replay, or modify a request.

主要输入为 `mode=list|detail`、可选 `request_ref`、`include_static`、`failed_only`、`fields`、`since_ms`、`until_ms`、`cursor`、`limit` 和 `max_body_chars`。稳定 ref 至少绑定 session/tab、method、URL、request identity 和 capture generation；body 受 origin policy、字段 allowlist、大小和敏感信息边界约束。

#### `browser_download`

模型可见 description：

> Arm browser download observation, perform one exact trigger action, and persist the completed download as an approved artifact. Use this when HomeMaster must initiate and collect a download atomically. Use `browser_wait(condition=download)` only to wait for a download already initiated by another allowed action. Success requires a completed browser download plus artifact existence, filename, size, SHA-256, and browser return state.

主要输入为 `trigger`、可选 filename/URL `pattern` 和 `timeout_ms`。listener 必须在 trigger 前建立；cancelled/interrupted、pattern 不符、文件缺失或 hash/readback 失败均不得返回成功。

#### `browser_wait`

模型可见 description：

> Wait for one bounded browser condition and return the last observed state. Supported conditions include text, semantic or read-only CSS selector, fixed time, matching XHR/response, DOM stability, URL, element state, popup, dialog, or an already initiated download. A timeout means only that the condition was not reached; it never proves a preceding action succeeded and never authorizes an unrelated later action.

主要输入为单个 `condition`，其 `kind` 为 `text_present|text_absent|selector_present|selector_absent|time|xhr|response|dom_stable|url|element_state|popup|dialog|download`，以及相应 target/value/pattern、`timeout_ms`。XHR/response 返回匹配 URL、status、method、content type 和 request ref；固定 time 使用纯 host-side sleep，不伪装成 DOM stable。

### 5.6 受控 escape hatch

#### `browser_eval`

模型可见 description：

> Execute JavaScript in the page context of one policy-authorized tab/frame and return a bounded structured result. Use this gated escape hatch only when the typed browser tools cannot observe or operate a required general web behavior, and state the expected external postcondition. Do not use it as a shortcut for find, read, extract, click, fill, or network. JavaScript has the page's same-origin authority and can mutate state, access page-visible credentials/storage, or initiate requests, so grant this tool only to a run trusted with that authority.

此工具默认不出现在模型工具列表；只有 run policy 显式授予独立 `browser.eval` capability 才注册。主要输入为 `script`、可选 `arguments`、`tab_ref`、`frame_ref`、`timeout_ms`、`max_result_chars` 和必填 `expected_postcondition`（纯读取允许明确 `none`）。输出和异常有界，完整脚本 hash、参数、origin、frame、耗时、返回/异常和动作后读回写入 JSONL。不得声称在 page context 中存在可靠的“只读 JavaScript sandbox”，也不得声称 AST/字符串 denylist 能阻止间接的 Cookie、storage 或 network 访问；`browser.eval` grant 本身就是对此同源页面权限的授权。跨 policy origin、宿主文件系统、CDP endpoint 和终端仍不可访问。

### 5.7 不纳入模型浏览器工具面的 OpenCLI 命令

- `bind`、`unbind`、`close`：Chrome Extension/daemon tab lease 生命周期；HomeMaster-owned Chromium 不需要。
- `init`、`verify`：OpenCLI adapter 开发命令，不是运行时网页操作。
- raw CDP endpoint、Cookie 导出、Node-side `fetchJson`：只属于后端实现或受信任诊断，不进入模型公开 schema。

上述排除不影响 Playwright 在 BrowserSession 内部使用 CDP 完成真实鼠标、键盘、文件、AX 或 frame 操作；Playwright 仍是唯一 browser owner。

## 6. 调用逻辑变化

旧逻辑是 inspect -> 下一轮使用 snapshot_id/element_id -> 一次写操作 -> 全部失效 -> review-only -> 再 inspect。V3.1 改为：

```text
browser action(target=semantic target 或 target_ref)
  -> 实时 resolver
  -> 唯一性、权限、可见性、控件类型检查
  -> Playwright 原子动作
  -> DOM/浏览器外部状态读回
  -> receipt 或 structured error
```

inspect 仍是探索工具；模型已知目标时可以直接动作。动作仍独占一次模型回复；timeout/cancel 后 fence 并废弃 session，未知是否已执行时禁止自动重试写操作。

模型选择读取工具时遵循：

```text
理解整页控件/结构       -> browser_inspect
已知语义、寻找一个目标   -> browser_find
读取一个准确属性/表单态  -> browser_read
阅读长正文               -> browser_extract
判断视觉布局/Canvas      -> browser_screenshot
诊断脚本/请求/反爬       -> browser_console / browser_network / browser_analyze
```

模型选择动作工具时遵循：

```text
激活普通非编辑目标       -> browser_click
精确设置最终输入值       -> browser_fill
需要真实输入事件/追加    -> browser_type
选择 native/ARIA option  -> browser_select
设定布尔/单选状态        -> browser_check / browser_uncheck
已有文件放入 file input  -> browser_upload
当前页面截图粘贴到控件   -> browser_backfill
触发并收集下载文件       -> browser_download
触发/处理阻塞 JS dialog  -> browser_dialog
typed 工具确实无法覆盖   -> policy 授权后 browser_eval
```

目标解析优先级：target_ref exact/stable/reidentified -> 唯一 strong id/testid -> exact accessible name+role -> exact label/text+role -> contains/regex（仅读或候选）。受限 CSS 只能通过 `browser_find`/只读 wait 生成候选引用，写动作仍须对引用执行可见性、唯一性和类型检查；普通 typed action 不自动降级到坐标、XPath 或 JavaScript。`browser_eval` 是 policy 显式授权、模型显式调用的独立 escape hatch，不能成为 resolver 的隐藏 fallback。

事件型操作固定先 arm 再 trigger：dialog、download、popup 和 XHR/response wait 在执行触发动作前建立 listener/capture；不能用动作完成后才开始监听的实现冒充支持。`browser_backfill` 固定锁定一次 PNG bytes/hash 后重复使用该值完成 paste 和终态核对，过程中不得重新截图导致目标值漂移。

## 7. 安全和边界

- 只允许 policy 注入的 origin；禁止 file/chrome/扩展页和未审核跨源跳转。
- iframe、popup、tab 携带 frame/tab 身份；跨源凭据、Cookie 和页面数据不得自动混入主页面上下文。
- upload 只接受 artifact store 中的受控引用，不接受任意服务器路径。
- `browser_network` 只读观察并执行 response-body policy，不提供请求注入、重放、修改或凭据导出。
- `browser_eval` 默认不注册；授权时明确拥有目标页面的同源脚本权限，不能依赖虚构的只读 sandbox。它仍不能访问宿主文件系统、CDP endpoint、终端或未授权 origin。
- `browser_screenshot` 与 `browser_backfill` 共享同一 Playwright page；backfill 的 source/rendered SHA-256 必须从真实 PNG 和目标读回独立计算。
- 业务成功由外部页面或独立 benchmark 验证；工具只报告浏览器事实。
- 所有 BrowserSession 实现和 fake 通过接口一致性审计。

## 8. 实施分期

### Phase A：发现与恢复地基

先按 2.6 锁定 OpenCLI 1.8.7 源文件、计算 import/export 依赖闭包、原样复制源码与对应 test/fixture、生成 provenance/hash manifest，并在未修改副本上跑通上游行为基线；再写 HomeMaster 失败测试和 adapter/loader，重构 DOM/AX snapshot 和字段模型；实现 semantic/受限 CSS find、exact/contains、候选、ambiguity、structured errors；实现 numeric ref、fingerprint、stable/reidentified resolver；加入 Shadow DOM、policy-allowed iframe、table、scroll container、diff 和 compound controls；实现 `browser_read`、`browser_extract`、frames inventory 和 annotated screenshot ref；将现有动作迁移到统一 resolver。此阶段只允许底层/接口单测和 fixture 调试，不切换公开 browser profile，不用旧 Skill 跑模型验收。

### Phase B：动作覆盖

增加 history、type replace/append、hover、focus、press、double click、scroll/into-view/auto、upload、drag/drop、dialog、tabs、console、network、download、XHR/response wait 和 analyze；保留并迁移 backfill 到统一 resolver；完成 27 个 safe typed tools 的 ToolDefinition、schema、BrowserSession 实现和逐工具底层测试；验证 screenshot、AX、native/ARIA select、日期时间、虚拟列表和 policy 允许的 iframe；必要时在 Playwright 内部增加 CDP 输入 fallback。此时工具后端已实现，但仍不得拿旧 Skill/Prompt 的模型行为当验收结果。

### Phase C：第 28 个 gated tool

增加独立 `browser.eval` capability、默认不注册的 `browser_eval`、完整脚本/参数/effect audit、输出边界和动作后 postcondition，完成第 28 个工具的底层测试。此阶段只验证 capability/执行边界；V3.1 常规能力测试不得依赖 eval。

### Phase D：工具完成后立即迁移 Skill/Prompt/Runtime

Phase C 最后一个工具实现完成后，下一步必须立即执行 10.1–10.2：迁移 `change-ticket-executor` Skill、browser gateway prompt、自动 observation barrier、上下文投影、Profile/registry/config budget 和最终 provider schema；将 browser profile 的 `observe` 单一切换为 `browser_screenshot`，机器人 profile 仍可保留其不同能力的 `observe`；保留 `browser_backfill` 的新协议。Phase B/C 与 Phase D 之间禁止运行或引用任何模型选工具、Web、端到端、陌生网页或 Run 32 结果，因为旧 Skill/Prompt 下的结果不代表 V3.1。公开激活只能在工具实现与本阶段消费者迁移同时就绪后发生。

### Phase E：测试、文档和发布门

Phase D 完成后才运行最终 provider schema、真实 `load_skill`、Skill 对抗、browser/robot profile 隔离、集成、陌生网页和 9.4 Run 32 回归。同步 README、用户指南、架构文档、配置模板和 CHANGELOG，删除站点/路由专用 browser 分支。构建 wheel/sdist 并在源码树外安装，执行 2.6.4 的 package-data/hash/license 门和移走全局 OpenCLI 后的真实 Playwright 黑盒门。分别验证：未授权 run 看不到 `browser_eval`，授权 run 可调用且跨 policy origin/宿主资源仍被拒绝；常规能力和 Run 32 均不能依赖 eval 才通过。

测试顺序固定为：实现前写失败的底层契约/行为测试 -> Phase A–C 边实现边跑聚焦单测 -> Phase D 立即迁移 Skill/Prompt/Runtime -> 更新并运行 Skill/provider schema 测试 -> 集成/黑盒/真实模型/Run 32。只有前两类底层测试可以在 Skill 迁移前执行。

## 9. 验收标准

### 9.1 工具契约

- 每个公开工具有输入 schema、结构化成功 receipt、失败码和 JSONL evidence。
- 本节每段“模型可见 description”及每个参数 description 进入最终 provider tool schema；不得只存在于设计文档或 Python 注释。
- 使用真实 provider request 逐工具断言最终名称、description、enum、required 和 `additionalProperties=false`，并证明同一能力没有别名工具。
- 每个 BrowserSession 实现包含全部公开方法；审计测试逐方法断言。
- 同一 session 同时服务全部 browser tools，`browser_screenshot`、`browser_backfill` 与动作作用于同一 page。
- 未授权 run 的最终工具列表没有 `browser_eval`；授权 run 只有一个 `browser_eval`，且 required capability 为 `browser.eval`。
- vendored OpenCLI 源码逐文件 hash、许可证、来源、修改记录、import/export 依赖闭包和 package data 全部通过 audit；runtime 只通过 HomeMaster adapter 使用选定的算法 export，不启动第二个 owner。

### 9.2 per-instance 黑盒门

至少分别验证未针对性适配的页面：DOM/AX snapshot、semantic/CSS find、title/URL/text/value/attributes/HTML tree/form state、正文 Markdown/cursor 分块、type replace/append、fill、focus/hover/double-click、键盘、日期/时间、native/ARIA select、checkbox/radio/switch、表格、菜单、history、弹窗、tab/popup、SPA 重渲染、滚动/into-view/auto、上传、拖拽、backfill、下载、console、network list/detail/failed/time-window、XHR/response wait、Shadow DOM、policy-allowed iframe、annotated screenshot、遮挡、禁用、重名、零匹配，以及 Canvas/图表降级到 `browser_screenshot`。

每个页面和每个目标分别断言外部 DOM/文件/URL 终态、浏览器返回码和错误时没有误动作；禁止用“任意一个实例成功”作为聚合 PASS。

事件型能力分别用“listener 在 trigger 前已建立”的真实时间顺序和最终 dialog/download/popup/network 终态验收。backfill 分别核对源 PNG、clipboard bytes 和目标渲染内容的 SHA-256；download 核对浏览器完成状态、外部文件和 artifact hash；network detail 按每个 request ref 核对准确 response，而不是取捕获集合中任意一个匹配。

工具描述有效性增加真实模型调用门：使用未见过的规范 DOM/ARIA 页面和固定任务，只提供最终 provider tool schema，不额外在 prompt 中解释工具；逐任务断言模型选择正确的 inspect/find/read/extract/screenshot、fill/type/select/click/upload/backfill、wait/download/dialog 工具。此门验证 description 能指导调用，但不能替代上述确定性外部终态门。

### 9.3 历史运行回归

- Run 27 类旧引用得到 stale_ref 或安全恢复，不能有无界协议拒绝循环。
- Run 28 类旧 snapshot 重用被明确拒绝或安全恢复，返回下一步建议。
- Run 29 类 07/second 7 不一致返回候选或明确 target_not_found，不能返回空成功。
- 页面构建失败等外部阻塞以页面真实错误停止，不能归因成动作成功。

### 9.4 Ant Design Pro / Run 32 同业务任务回归门

V3.1 发布前必须在真实 Web Console 和真实模型入口重新执行一次 Run 32 已成功的完整变更任务。此门验证 V3.1 改造没有破坏既有复杂业务工作流，也证明 28 个工具能组合操作真实 Ant Design Pro；它不能替代 9.2 的陌生网页逐能力测试。

固定资源：

```text
HomeMaster: /home/haodong2/weilin/red_bird/Homemaster
Ant Design Pro: /home/haodong2/weilin/red_bird/ant-design-pro
变更单: /home/haodong2/weilin/red_bird/hawkeye/show_data/ops_monitor_agent_demo.source.ticket.json
Run 32 baseline: ops-monitor-real-20260825-32-video
```

Run 32 的已确认基线事实为：三个 involved SOP 全部完成；告警窗口为 `2026-08-21 21:25:07~21:44:07`；创建变更前证据；为 `fixture-node-01` 执行 `update_agent_monitor` 到 `1.0.0`；外部执行 `exitCode=0`；独立验证命令 return code 0 且输出 `CONFIG_VERSION_OK`；变更后资产为 `running / 1.0.0 / fixture-region-01`；创建并正确关联变更后证据；Runtime 和 CLI 最终状态均为 completed。baseline 视频 SHA-256 为 `e0a5da38d9ff0d82142de4acb3f116173a22bef5e7163bcf3d3039163f9821a5`。

#### 9.4.1 环境和配置前置门

1. 使用全新 run ID、run root、session 和 Chromium context；不得复用 Run 32 或其他 run 的目录、tab、ref、memory 或 terminal receipt。
2. Ant Design Pro 必须先通过真实启动 preflight：进程/端口存活、入口 HTTP 成功、Chrome 最终页面无 Webpack/`Can't resolve 'umi'` 错误，并能看到“重置环境(全部)”语义控件。Run 30/31 类启动失败记为 environment failure，不得算 Agent 失败或成功。
3. 运行前独立确认 fixture 初态为 `agent_version=0.9.0`、`monitoring_enabled=true`、`node=fixture-node-01`；页面“重置环境(全部)”仍由 Agent 在任务开始时执行一次。
4. 建立 gitignored、mode-0600 的 `config/homemaster.browser.yaml`，从正常默认配置继承已锁定 provider/model/context 参数，只覆盖本门需要的差异：`browser_gateway.headless=false`、`memory.enabled=false`、`permissions.mode=full_auto`、terminal allowlist 仅增加变更单 `operate_verified` 原文指定的命令，并明确不授予 `browser.eval`。
5. 若仓库尚无对应模板，提交不含凭据的 `config/homemaster.browser.yaml.example`；真实配置和任何 `/tmp` 实验快照不得提交。
6. 启动入口使用已经真机核对支持 `--config` 的 Web 命令，并记录最终 config path、Web port、AntD URL 和返回码：

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
.venv/bin/python -m homemaster.cli serve \
  --browser \
  --config config/homemaster.browser.yaml \
  --port <recorded-free-loopback-port>
```

#### 9.4.2 V3.1 Web prompt fixture

业务要求保持 Run 32 不变，但工具协议必须迁移到 V3.1。验收时在全新 Web session 一次性发送下面的 prompt；不得继续发送旧版 `observe`、`snapshot_id/element_id` 或“每个动作前强制 inspect”的协议提示：

```text
请先调用 load_skill 加载 change-ticket-executor。

然后读取本机变更单：
/home/haodong2/weilin/red_bird/hawkeye/show_data/ops_monitor_agent_demo.source.ticket.json

读取、分页、解析或搜索这份变更单时，必须且只能调用 read_file；禁止调用 terminal、cat、grep、jq、Python 或任何 shell 命令处理变更单。

严格按照 data.sop_change_step 中 is_involved_step=true 的步骤，以及每一步的 operate_description 和 operate_verified，执行完整变更。先建立任务计划，并逐步更新任务状态。

执行要求：

1. 每次任务开始先通过页面提供的“重置环境(全部)”重置环境。
2. 所有浏览器操作必须只使用 HomeMaster 提供的 browser_* 工具和页面语义目标；禁止坐标点击，禁止通过 terminal、JavaScript、CDP、后台接口或另一个 Playwright/Puppeteer 会话检查或操作页面。
3. 已知唯一语义目标时可以直接使用动作工具；目标未知、多匹配或页面变化后身份不确定时，先单独调用 browser_inspect 或 browser_find，使用其返回的准确 target_ref。不得猜测、拼接或跨 tab/frame 混用 ref；stale、ambiguous 或 not found 时按结构化结果重新发现目标。
4. 浏览器写入或交互工具必须独占一次模型回复。task_progress_check 必须单独调用，等待返回后才能继续执行写操作。
5. 精确设置可编辑文本、日期和时间使用 browser_fill；需要真实输入事件时使用 browser_type；select/combobox 使用 browser_select；checkbox/radio/switch 使用 browser_check 或 browser_uncheck；按钮、链接、tab、option 等普通非编辑目标使用 browser_click。
6. 在以下关键节点单独调用 browser_screenshot 并根据视觉结果确认后再继续：
   - 时间选择器完成开始时间和结束时间设置后；
   - 正式执行变更脚本前；
   - 脚本执行完成并出现执行回显后；
   - 变更后资产查询及取证完成后。
7. 浏览器写操作后 HomeMaster 会自动附加图像 observation；不要为了重复确认而连续调用 browser_screenshot。
8. browser_backfill 只用于把当前页面的新截图粘贴到明确的图片回填控件；普通视觉检查使用 browser_screenshot，已有文件上传使用 browser_upload。
9. terminal 只能执行变更单 operate_verified 原文明确指定的非浏览器终态验证命令，除此之外禁止调用 terminal。
10. 缺少语义控件、任何前置检查失败、外部返回码非成功或任何终态验证失败时，立即停止并明确报告失败位置，不得假定成功。
11. 最终必须同时确认：
    - 三个 involved SOP 步骤均已完成；
    - 外部执行返回码成功；
    - agent_version 已真实变为 1.0.0；
    - 变更后资产页面显示 fixture-node-01 为 running、版本 1.0.0；
    - 前后两次取证记录均已成功关联到正确的 SOP 步骤和字段。

现在开始执行。
```

Web 页面和 WebSocket 在 terminal lifecycle 前保持连接；审批模式若被显式启用，则逐卡核对准确工具名和参数后批准。本门的标准配置为 `full_auto`，正常情况下不应出现人工审批依赖。

#### 9.4.3 V3.1 独立验收断言

以下各项必须逐项 PASS，不能用总任务 completed 掩盖其中一个失败：

1. 最终 provider tool schema 包含 V3.1 名称和 description，不包含 `observe`、旧 `snapshot_id/element_id` 写协议或 `browser_eval`。
2. HomeMaster、Skill、prompt 和 AntD 中没有为该 ticket path、`fixture-node-01`、`update_agent_monitor` 或 `/ops` route 新增站点/任务专用 browser 分支。
3. 三个 involved SOP step 分别完成，顺序和 `operate_description` 一致；每个 `operate_verified` 分别有真实返回码和终态证据。
4. 告警查询使用准确 `2026-08-21 21:25:07~21:44:07`，秒目标不得退化为裸 `07` 误命中日期；不存在 Run 29 类重复查找循环。
5. 变更前证据真实创建并关联到准确 SOP step/字段；不能复用 Run 32 的 `WSO-MONITOR-0001`，新 evidence ID 以本 run 页面/存储读回为准。
6. `update_agent_monitor` 的外部返回 `exitCode=0`；变更单原文 terminal 验证命令 return code 0 且 stdout 包含 `CONFIG_VERSION_OK`。
7. 独立于模型/工具 receipt 回读 fixture 文件，准确断言 `agent_version=1.0.0`、`monitoring_enabled=true`、`node=fixture-node-01`。
8. 变更后资产页面准确显示 `fixture-node-01 / running / 1.0.0 / fixture-region-01`；每个字段分别断言，不能以整页存在任意 `running` 或 `1.0.0` 作为 PASS。
9. 变更后证据真实创建并关联到准确 SOP step/字段；前后证据分别存在且身份不同。
10. 四个指定关键节点各有模型实际消费的 `browser_screenshot`；自动 write observation 与显式截图按来源 tool-call ID 区分，不要求复刻 Run 32 的 28 张图片总数。
11. Runtime terminal event、CLI/Web run status 和真实业务终态一致为成功；完整 stderr 无 traceback、迟到异常或浏览器 backend failure。
12. 视频/trace/JSONL/terminal receipt/provider request-response 全部属于本次唯一 run root，哈希和 manifest 可独立验证；不要求视频时长、evidence ID 或工具调用次数与 Run 32 相同。
13. 验收证据发布后执行 cleanup：关闭 HomeMaster/Chrome/recorder/AntD owned process，确认端口和 display 无残留，并把 fixture 恢复为 `0.9.0` 后独立读回。cleanup 后的恢复不能覆盖或伪造此前已验收的 `1.0.0` 终态证据。

这条真实任务回归必须在全部 V3.1 实现、单测和陌生网页黑盒门通过后执行一次；失败时先区分 environment、tool protocol、model decision 和业务终态层，保留原始 run，不允许用重跑成功覆盖失败诊断。

## 10. Skill、Prompt、文档和兼容性

Phase D 完成公开协议迁移、Phase E 开始最终测试前同步更新：

- docs/architecture/：BrowserSession、resolver、snapshot、owner 和不变量，并记录 vendored OpenCLI source -> generated page bundle -> adapter -> Playwright 的构建/运行数据流、版本升级流程和 owner 边界；
- docs/browser-gateway-user-guide.md：每个工具的真实输入、用途、失败示例；
- README.md：能力清单和边界；
- builtin Skill：调用顺序、工具选择和 backfill 保留用法；
- Web benchmark/user guide：包含 9.4 的 V3.1 prompt fixture、专用 ignored 配置和真实启动命令；
- CHANGELOG.md：公开协议变化和影响；
- docs/pitfalls.md：Run 27–32 的协议、环境分类和真实终态教训（如尚未记录）。

协议、ToolSpec、Protocol 和 fake 的改动必须同一提交，commit message 与 CHANGELOG 条目同源。

### 10.1 `change-ticket-executor` Skill 同源迁移

Phase A–C 完成工具后端后，必须把下面文件作为紧接着的 Phase D 第一项迁移；迁移完成前不得公开新 browser profile，也不得开始任何模型/集成/真实 Web 测试：

```text
src/homemaster/skills/builtin/change-ticket-executor/SKILL.md
tests/homemaster/skills/test_change_ticket_executor.py
tests/homemaster/skills/test_change_ticket_executor_evidence.py
```

当前 Skill 仍锁定 V2.1 协议：front matter 只声明旧 browser tools；正文强制“每个写操作前 fresh inspect”；动作参数使用 `snapshot_id/element_id`；`next_snapshot` review-only；显式视觉工具名为 `observe`。V3.1 实施时必须整体替换这些假设，不能在旧段落后追加一段相互冲突的新说明。

V3.1 Skill front matter 的 `tool_names` 至少包含其实际会指导模型使用的全部 safe typed browser tools：

```text
browser_navigate, browser_history, browser_inspect, browser_find,
browser_read, browser_extract, browser_screenshot, browser_console,
browser_analyze, browser_click, browser_fill, browser_type, browser_select,
browser_check, browser_uncheck, browser_hover, browser_focus, browser_press,
browser_scroll, browser_upload, browser_drag, browser_backfill, browser_tabs,
browser_dialog, browser_network, browser_download, browser_wait
```

同时保留 `load_skill`、ticket 读取、task plan/progress 和经过 ticket 授权的 terminal 工具。`browser_eval` 不进入 change-ticket Skill 的默认 `tool_names`：这类变更执行必须先证明 safe typed surface 足够，不能让 Skill 把高权限 escape hatch 变成常规路径。

Skill 正文必须明确：

1. 已知唯一语义目标时动作工具可直接接收 semantic target，不再机械要求 inspect。
2. 目标未知、多匹配、frame/tab 不明或身份漂移时使用 `browser_inspect`/`browser_find`；复制完整 `target_ref`，不猜测、不拼接，并按 `exact/stable/reidentified/stale_ref` 处理。
3. `browser_inspect` 用于整页结构，`browser_find` 用于目标发现，`browser_read` 用于准确属性/终态读回，`browser_extract` 用于长正文，`browser_screenshot` 用于视觉判断；不得把五者写成同义工具。
4. `fill/type/select/click/check/upload/backfill/download/dialog` 的选择规则与 5.3–5.5 完全一致；特别保留 backfill，但只用于当前页面截图的 clipboard image 回填。
5. 每个 browser 写/交互仍独占一个模型回复；动作后自动图像 observation 是 runtime evidence，不授权下一动作。需要额外视觉判断时显式调用 `browser_screenshot`。
6. dialog/download/popup/XHR 等事件型工作先 arm listener/capture 再 trigger；timeout 不证明前一动作成功。
7. terminal、raw JavaScript、CDP、坐标、后台接口或第二浏览器会话不能作为 change-ticket 网页操作 fallback；本 Skill 默认不使用 `browser_eval`。
8. TODO 状态只在模型可见外部终态证据后更新；结构化 EvidenceDrawer 和 image backfill 的业务权威性按 ticket/UI 实际要求区分，不能强制二者互相替代。

### 10.2 Browser prompt 和 runtime 协议迁移

Skill 不是唯一指令来源；下面消费者必须同步审计和更新：

| 文件/边界 | V3.1 必须修改的内容 |
| --- | --- |
| `src/homemaster/prompts/browser_gateway.md` | 删除 fresh `snapshot_id/element_id` 配对和每次写前 inspect；加入 semantic target/stable ref、读取工具分工和 `browser_screenshot` |
| `src/homemaster/prompts/agent_system_prompt.md` | 机器人 `observe` 规则只在 robot/对应工具存在时适用；browser profile 不得收到“必须调用 observe”的冲突指令 |
| `src/homemaster/agent/context_projection.py` | 删除旧 immediately-preceding-inspect 拒绝协议，改为统一 resolver 的 structured error/result 投影 |
| `src/homemaster/agent/generic_runtime.py`、`state.py`、`model_observation.py` | 自动 post-write image 使用 profile/ToolDefinition 指定的 observation tool，不把公开名称硬编码成 `observe`；browser 使用 `browser_screenshot`，robot 可继续使用 `observe` |
| `src/homemaster/browser/tools.py`、Profile/registry/config tool budget | 最终只注册 V3.1 名称和 schema；browser profile 无同义 `observe`，budget/required observation 与新工具一致 |
| compact/public projection/trajectory | 模型可见工具名同步；内部 “observation/observer” 事件和录制术语若不代表公开工具，无需机械重命名 |

这是一处公开协议和跨核心模块数据流改造，必须同步所有 `BrowserSession` 实现、fake、Profile、provider schema、自动 observation 调用和错误投影；只修改 Skill 或 `browser/tools.py` 不能算完成。

### 10.3 Skill/Prompt 验收门

1. 更新原有 Skill 测试：删除要求 `snapshot_id`、`element_id`、immediately preceding inspect、consumed snapshot 和 `observe` 的正向断言，改为对 V3.1 语义的正向断言与旧协议的负向扫描。
2. 从真实 `load_skill("change-ticket-executor")` tool result 和下一次 provider serialized request 解析正文，断言模型实际看到新 Skill；直接读取仓库 `SKILL.md` 不算 provider 可见证据。
3. 构建 wheel、安装到源码 checkout 外的空环境，再加载 builtin Skill，逐项断言 front matter tool names、正文和资源 hash；源码测试绿不能证明 package data 已更新。
4. 在 browser profile 的最终 provider request 中断言有 `browser_screenshot`、无 `observe`；在 robot profile 独立断言其 `observe` 仍存在且行为未被 browser 迁移破坏。
5. 使用 9.4 prompt 加载 Skill 后，断言后续模型没有发出旧参数、没有无条件 inspect/write 配对、没有调用 `browser_eval`，并能完成相同业务终态。
6. 对 Skill 工具选择做对抗用例：普通截图不得选 backfill，已有 artifact 上传不得选 backfill，精确设值不得选 click/type，长正文不得选 inspect，阻塞 dialog/download 必须选择预监听的专用工具。

## 11. 明确非目标

- 不把 OpenCLI daemon/Extension 作为核心依赖；
- 不承诺 Canvas、验证码、远程桌面和任意跨源页面完全语义化；
- 普通 typed 写动作不直达 CSS、XPath、坐标或 JavaScript；受限 CSS 仅用于只读 find/wait 并生成受 resolver 复核的引用；
- 不把 `browser_eval` 描述成安全 sandbox，也不默认授权；其显式 grant 只覆盖 policy 允许页面的同源脚本权限，不开放 CDP endpoint、宿主文件系统或终端；
- 不为每个网站增加专用 mode；
- 不以工具成功日志代替外部终态验证；
- V3.1 不同时迁移 Firefox、Safari 或 WebDriver/BiDi。
