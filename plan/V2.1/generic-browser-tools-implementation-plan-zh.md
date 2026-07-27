# HomeMaster V2.1 通用浏览器工具层实施计划

## 0. 状态

- Owner：主 agent
- 日期：2026-07-27
- 基线提交：`bb927d4c6e11f1fe291db0e1d9b0ecb7adfd34d1`
- 当前阶段：根据 owner 评审意见修订为两阶段计划，尚未实施
- 用户已锁定的架构选择：方案 3，通用浏览器执行与 benchmark 评分/编排分离
- 计划评审：此前只读 reviewer 在 owner 要求停止后中断，未形成独立结论；本轮按 owner 已锁定意见修订
- 最终代码评审：全部实现、测试、外部终态验证和文档更新完成后进行唯一一次只读 reviewer subagent 评审
- 实施交接：`plan/V2.1/generic-browser-tools-implementation-handoff-zh.md`
- 工作树状态：已有用户修改；本任务不得覆盖或回退这些修改

本轮修订得到 owner 确认以前，禁止开始产品代码实现。不得把第一阶段可行性通过写成完整功能发布。

## 1. 目标与完成形态

计划分为两个连续里程碑。

第一阶段先证明浏览器创建和工具执行真正属于 HomeMaster 通用运行时，而不是 Coworker 专用能力。普通
`ApplicationRuntime` 必须通过通用 composition 创建并持有 run-scoped `PlaywrightBrowserSession`，完成：

1. 打开运行策略允许的前端 URL；
2. 从当前 live DOM 和 Accessibility 信息获取页面文字与可操作元素；
3. 使用页面快照产生的临时元素引用填写、选择、勾选、取消勾选和点击；
4. 等待页面文字、元素状态、URL 或 DOM 稳定状态发生预期变化；
5. 对每次动作做真实 DOM 读回和结构化轨迹记录；
6. 继续使用既有 `observe` 对同一个页面做截图验证；
7. 在真实 Ant Design Pro Automation Mock 页面上，由实际 `ApplicationRuntime` 完成一条端到端操作并独立验证
   `SUCCESS (exitCode=0)` 终态。

第二阶段再把 Coworker 迁移到同一个通用 BrowserSession，并补齐生产级 origin/frame 策略、超时终态恢复和更完整的
snapshot freshness。最终只有一套公开浏览器行为，不增加 `coworker_mode`、`ant_design_mode`、`webui_*` 或第二套
Driver。

第一阶段是不可发布的可行性里程碑：Coworker 旧 Driver 可以暂时保持不动，避免在通用链路尚未证明前同时重写
benchmark；第二阶段完成后必须删除旧通用职责实现，才可宣称整体交付。

## 2. 当前根因与基线事实

### 2.1 现有能力不是假的浏览器

- `PlaywrightBrowserDriver` 使用 `launch_persistent_context()` 启动 headed Chrome，持有 context、page、profile、
  trace 和关闭生命周期。
- 现有 `locator.fill()`、`input_value()`、`inner_text()`、`page.screenshot()` 已经读取真实 live DOM/页面，不需要
  OpenCLI Bridge 才能获得 DOM。
- 当前问题是浏览器实现与 Coworker Case02 业务耦合，不是 Playwright 无法读取 live DOM。

### 2.2 当前浏览器层的业务耦合

当前 `src/homemaster/benchmarking/coworker_demo/browser_driver.py` 同时承担：

- Playwright 浏览器生命周期和页面动作；
- 固定 `ticket/monitor/automation` 路由以及 `/{route}/{run_id}` URL 拼接；
- 固定 `[data-bid]` 目标解析；
- `EnvironmentClient.reserve()`、`record_action()` 和 state version；
- Coworker 的 receipt、job row、`ADD_WAIT` 等业务节点映射；
- Agent 页面 allowlist 和 Case02 专属等待条件。

只修改 ToolSpec 名称和参数不能得到通用能力。第一阶段先独立证明通用 Driver/工具链；第二阶段最终迁移时，Driver、
动作生命周期和 Coworker 评分投影必须作为一个闭环一起完成。

### 2.3 当前公开工具与 Registry

现有公开浏览器工具为：

- `browser_navigate(route)`
- `browser_click(bid)`
- `browser_fill(bid, value)`
- `browser_select(bid, value)`
- `browser_wait(job_id, target_status)`
- `observe({})`

浏览器工具仍以 legacy `ToolSpec` 定义，再由 `profiles.py` 适配为 canonical `RegisteredTool`。本次公开协议变更
不继续扩大 legacy 层，应把通用浏览器工具直接定义为 canonical registered tools。

### 2.4 Ant Design Pro 真实目标

- 仓库：`/hpc2hdd/home/wyuan140/weilin_workspace/ant-design-pro`
- Automation 页面：`src/pages/dashboard/automation/index.tsx`
- Mock 构造：`src/pages/dashboard/automation/actionCatalog.ts::buildMockExecution`
- 默认任务需要填写四个必填输入：`TenantId`、`ItemCode`、`SpecCode`、`ExtensionName`
- 提交按钮可访问名称：`确认执行`
- 成功终态真实 DOM 文本：`执行状态：SUCCESS (exitCode=0)`
- 页面执行只写浏览器本地 React/localStorage 状态，不需要真实业务后端。
- 前端声明 Node `>=22.0.0`；当前默认 Node 是 `v20.18.0`，真实启动前必须先建立并核对 Node 22+ 运行入口。

### 2.5 浏览器环境现状

- HomeMaster venv 中安装的是 Playwright `1.61.0`。
- Playwright 当前报告的 Chromium 路径为 `chromium-1228/.../chrome`，该文件当前不存在。
- 缓存中只有旧的 `chromium-1169`；它不能作为 Playwright 1.61 可用性的证据。
- `/usr/bin/google-chrome` 当前不存在，因此现有 Coworker example config 不能直接通过真实 Chrome preflight。
- 实施阶段必须使用项目 venv 的 Playwright 安装匹配浏览器，或者锁定并真机验证一个显式 executable；安装成功、
  启动返回和真实页面操作三项都通过以前，浏览器前置条件保持 `UNVERIFIED`。

## 3. 架构候选与锁定决策

### 3.1 浏览器与 benchmark 的职责边界

候选方案：

1. 只改现有 ToolSpec 参数。改动最小，但底层仍只认识 `data-bid` 和 Case02，不能满足目标。
2. 在现有 Driver 内增加 Coworker/普通页面条件分支。短期可跑，长期形成多个 mode，业务继续污染浏览器层。
3. 通用 BrowserSession + 通用工具服务 + 外围 benchmark 生命周期。只有一套浏览器动作，场景只在外围消费事实。
4. 直接依赖 OpenCLI daemon/Bridge。能操作已有 Chrome，但引入第二浏览器所有者和不需要的权限面。

锁定方案 3。用户已明确选择；实施不得退回 1/2，也不得引入 4 作为核心依赖。

### 3.2 OpenCLI 复用方式

候选方案：

1. 运行时调用 OpenCLI CLI/daemon。
2. 整套复制 OpenCLI browser 命令。
3. 以 HomeMaster Playwright 重现经过选择的 OpenCLI 行为，并对直接翻译部分保留来源清单和 Apache-2.0 归属。
4. 完全从零设计 DOM 快照与 stale-ref 逻辑。

锁定方案 3。原因：HomeMaster 已经持有浏览器和录屏生命周期；OpenCLI 的价值是算法，不是 Bridge。

### 3.3 Agent 如何指定元素

候选方案：

1. Agent 直接提供 CSS；灵活但脆弱，扩大权限面。
2. 每次动作只提供 role/name；重名和页面变化时歧义较大。
3. 截图坐标；缩放、滚动和重排后不稳定，且无法提供 DOM 读回。
4. `browser_inspect` 生成 `snapshot_id + element_id`；动作前用指纹重新核对 live DOM。

锁定方案 4。首个正式发布版本不向模型暴露 CSS、XPath、任意 JS 或 Playwright locator。

### 3.4 二值控件工具命名

不采用难读的 `browser_set_checked`，而采用 OpenCLI/Playwright 常见语义：

- `browser_check`：确保复选框、开关或单选项为选中状态；已经选中时不重复点击。
- `browser_uncheck`：确保复选框或开关为未选中状态；单选项不允许直接取消。

这两个动作是幂等目标动作，不是“切换一次”。

### 3.5 浏览器创建与生命周期

候选方案：

1. 继续由 Coworker 创建浏览器再注入工具。改动最小，但普通 Home 无法使用，拒绝。
2. 普通 Home 和 Coworker 各自创建一套 Driver。短期直观，但形成两套所有权和行为，拒绝。
3. HomeMaster 通用 composition 通过 `BrowserSessionFactory` 创建 run-scoped BrowserSession；普通 Home 与 Coworker
   只提供配置和外围 lifecycle。生命周期统一、run 间隔离，第一阶段采用。
4. 首次工具调用时惰性启动 application-scoped 长期浏览器。可减少无浏览任务的启动成本，但并发、恢复和清理更复杂，
   留作后续优化。

锁定方案 3。`BrowserSessionFactory`、配置、启动、关闭和失败清理位于 `homemaster.browser` 与通用 composition，禁止
benchmark 模块创建生产 BrowserSession。浏览器能力未启用时，composition 不向该 run 的模型暴露 `browser_*`；启用
时必须在第一次 provider request 前完成创建并绑定，同一 session 同时供 `browser_*` 与 `observe` 使用。

启用状态必须形成 immutable per-run tool view，provider manifest 与 dispatch 使用同一个 view；禁止注册/删除全局
application Registry 来切换单个 run。并发启用与禁用 run 必须互不影响。第一阶段的显式启用是 composition/RunRequest
级实验入口，不新增对外稳定配置字段；正式配置 schema、`.example` 和用户入口在第二阶段发布时设计并同源更新。

## 4. 非目标

首个正式发布版本明确不做：

- 接管用户已经打开、未开放调试能力的 Chrome；
- OpenCLI Chrome Bridge、daemon、profile 和 tab lease；
- Firefox、Safari/WebKit 或 WebDriver/BiDi 多浏览器支持；
- 任意 CSS、XPath、JavaScript eval、Cookie、网络请求抓取；
- 文件上传、下载、拖拽、hover、双击、键盘快捷键和多标签管理；
- Canvas、视频帧、远程桌面像素的 DOM 化；这些继续由 `observe` 提供视觉证据；
- 为 Ant Design Mock 新建 benchmark 评分体系；本计划只验证通用工具与真实页面终态；
- 让浏览器工具自己判断业务正确性或计算分数；
- 未启用浏览器能力的 Home run 自动启动 Chrome；第一阶段采用显式配置启用的 run-scoped session，不启动长期服务。

最后一项不妨碍通用工具交付：浏览器创建已经归属通用 composition，只是通过显式配置决定当前 run 是否启用，不能
依赖 Coworker 才能创建。第一阶段必须由普通 Home `ApplicationRuntime` 黑盒证明这条路径。

## 5. 不可破坏的不变量

1. `observe` 保持 image-only：不返回 DOM、元素引用、评分收据或完成判断。
2. 所有浏览器动作作用于 `observe` 截图的同一个 Playwright page。
3. 浏览器核心不得 import Coworker 的 `EnvironmentClient`、budget、outcome、ticket、job、receipt 或评分模块。
4. 通用动作只陈述浏览器事实；“交互已执行”不等于“业务成功”。
5. 页面动作前必须验证目标唯一、身份未漂移、可见、可用且动作类型匹配。
6. 填写、选择、check/uncheck 必须逐控件读取真实 DOM 状态，不能只信 Playwright 无异常返回。
7. 第一阶段只在显式配置的可信本地测试 origin 上验证；顶层初始 URL 仍必须通过运行时注入的 policy，不硬编码
   localhost，也不允许 `file:`、`chrome:` 或扩展页。redirect、popup 和 frame 的逐跳生产级策略留到第二阶段。
8. 所有 BrowserSession 实现和测试 fake 必须通过接口公开方法一致性审计。
9. Coworker 的在线顺序门、预算、准确 job ID、state version、SOP 证据和终态评分语义不得因迁移丢失。
10. 第一阶段只新增通用链路并保持不可发布；第二阶段迁移 Coworker 时删除旧 route/bid 公共路径，不把临时双实现发布
    或长期保留。
11. 关键调用、结果、耗时、失败码和 evidence refs 进入既有结构化 Runtime/benchmark JSONL，不新增互相竞争的日志真理源。
12. 不修改用户拥有的 Ant Design 前端改动来“配合测试”；工具必须操作页面已有的 label/ARIA/DOM。
13. 第一阶段同一个 BrowserSession 一次只执行一个动作；动作 timeout/cancel 后立即 fence 并废弃整个 session，禁止
    自动重试或继续复用。旧同步调用是否最终完成只记为 `outcome_unknown`，完整终态查询与恢复留到第二阶段。
14. 第一阶段只接受当前最新 snapshot；导航或任一写动作完成后立即使其失效，下一次写动作前重新 inspect。跨动作稳定
    引用、DOM revision 和 `reidentified` 自动恢复留到第二阶段。
15. 第一阶段跨源 iframe 仅用于可信本地 fixture 的能力探测，不视为安全边界已完成，不得用于带账号、Cookie、凭据或
    生产数据的页面；正式发布前必须完成第二阶段 frame/redirect/popup policy。

## 6. 目标数据流

```text
普通 Home 或 Coworker composition 启用 browser capability
  -> 通用 BrowserSessionFactory 创建 run-scoped PlaywrightBrowserSession
  -> 在第一次 provider request 前绑定同一个 session 到 browser_* 与 observe
  -> 模型调用 browser_* 工具
  -> canonical BrowserTool executor
  -> URL/target/action policy
  -> BrowserActionLifecycle.before_action（普通运行 no-op；Coworker 做预算/预约/关联）
  -> 唯一 PlaywrightBrowserSession 执行真实动作
  -> live DOM 读回形成 BrowserActionReceipt
  -> BrowserActionLifecycle.after_action/on_error（Coworker 映射外部证据）
  -> canonical ToolExecutionResult + Runtime JSONL
  -> 外部 benchmark 读取轨迹和独立真实终态评分

observe
  -> 同一个 PlaywrightBrowserSession.screenshot()
  -> 既有 image-only provider 投影
```

`BrowserActionLifecycle` 是第二阶段 Coworker 迁移使用的外围扩展点，不是浏览器 mode。第一阶段普通运行使用 no-op
生命周期证明通用链路；第二阶段 Coworker 使用同一个通用动作路径，只在动作前后处理自己的在线约束和证据。

## 7. 公开工具契约

### 7.1 `browser_navigate`

- 模型含义：打开一个允许访问的网页地址。
- 必填输入：`url`。
- 工具自己使用受限的加载与 DOM 稳定默认值；不让模型选择任意底层等待策略。
- 返回：请求 URL、最终 URL、标题、是否重定向、加载状态、页面 generation、耗时和 evidence refs。
- 导航成功立即让此前所有 snapshot/element 引用失效。

### 7.2 `browser_inspect`

- 模型含义：查看当前网页的可见文字和可操作控件，并返回临时元素编号。
- 可选过滤：`role`、`name`、`label`、`text`、`interactive_only`、`limit`。
- 返回页面级字段：`snapshot_id`、page generation、URL、标题、可见关键文字、滚动/iframe 摘要。
- 每个元素返回：
  - `element_id`；
  - 直白控件类型（输入框、按钮、链接、下拉框、复选框、开关、单选项、标签页等）；
  - role、可访问名称、label、可见文字；
  - 当前值或状态；
  - visible、enabled、editable、required、readonly、checked、selected、expanded、obscured；
  - 原生 select/date/file 等复合控件的必要结构信息；
  - frame 身份和是否为本次新出现元素。
- 密码控件不返回明文当前值。
- 默认输出必须做 token 预算和数量上限；截断时返回总匹配数和明确提示，不假装结果完整。

### 7.3 `browser_fill`

- 必填输入：`snapshot_id`、`element_id`、`value`。
- 仅允许 input、textarea 和 contenteditable。
- 动作前滚动、检查可编辑性和身份；动作后读取 `value`/`innerText`。
- 返回 expected、actual、verified、控件类型、匹配等级和页面变化。
- `verified=false` 必须作为工具失败，不能以 succeeded receipt 返回。

### 7.4 `browser_select`

- 必填输入：`snapshot_id`、`element_id`、`option`。
- 原生 `<select>`：按唯一 label 或 value 选择并读取最终 option/value。
- ARIA combobox（包括 Ant Design Select）：打开列表，按唯一可访问 option 选择，再读取 combobox 显示值/选中状态。
- 找不到时返回可用候选的有界列表；多项匹配返回 ambiguity，不默认选第一项。
- OpenCLI 1.8.6 的 `select` 只覆盖原生 `<select>`；ARIA combobox 部分是 HomeMaster 扩展，真实验证前标记
  `UNVERIFIED`。

### 7.5 `browser_check`

- 必填输入：`snapshot_id`、`element_id`。
- 支持 checkbox、switch、radio 及对应 `aria-checked` 控件。
- 已选中时返回 `changed=false`，不点击；未选中时点击并读回 checked/aria-checked。

### 7.6 `browser_uncheck`

- 必填输入：`snapshot_id`、`element_id`。
- 支持 checkbox 和 switch。
- 已未选中时返回 `changed=false`；已选中时点击并读回。
- radio 返回明确的 `unsupported_control`，提示选择同组另一项，不能伪造取消成功。

### 7.7 `browser_click`

- 必填输入：`snapshot_id`、`element_id`。
- 使用 Playwright 的真实 actionability/鼠标事件路径；禁止把 DOM `el.click()` 成功当作首选证明。
- 返回目标身份、URL 前后值、页面 generation、popup/navigation/DOM 变化、匹配等级和 evidence refs。
- 返回的是 `interaction_verified`，绝不命名为 `business_success`。

### 7.8 `browser_wait`

- 模型通过直白 condition 选择：
  - 页面文字出现/消失；
  - 目标元素出现/消失、可用/不可用；
  - 目标元素文字包含指定值；
  - URL 包含指定值；
  - DOM 在安静窗口内稳定。
- 元素条件使用 `snapshot_id + element_id`；文本和 URL 条件使用明确期望值。
- timeout 有配置上限；超时返回最后观察状态和 `wait_timeout`，不能只返回泛化异常。
- 首个正式发布版本不开放 CSS wait、XHR 和 download wait。

### 7.9 `observe`

- 公开名称、空输入 schema、image-only 输出和 provider 投影保持不变。
- 只把 screenshot source 从 Coworker 命名适配器迁到通用 BrowserSession screenshot source。

### 7.10 读写、权限与并发合同

所有浏览器工具共享 `resource_key=browser:backend`，使用 `RESOURCE_KEY` 串行化；`observe` 也必须进入同一资源门，
不能与页面动作交错截图。第一阶段锁定如下，不留给实现时临时推断：

| 工具 | 分类 | state effects | required capabilities | execution proof |
| --- | --- | --- | --- | --- |
| `browser_navigate` | 写 | `browser.navigate` | `device.control`, `network.http` | structured receipt |
| `browser_inspect` | 读 | `read` | `device.read` | none |
| `browser_fill` | 写 | `browser.dom_write` | `device.control` | structured receipt |
| `browser_select` | 写 | `browser.dom_write` | `device.control` | structured receipt |
| `browser_check` | 写 | `browser.dom_write` | `device.control` | structured receipt |
| `browser_uncheck` | 写 | `browser.dom_write` | `device.control` | structured receipt |
| `browser_click` | 写 | `browser.interact` | `device.control` | structured receipt |
| `browser_wait` | 读 | `read` | `device.read` | structured receipt |
| `observe` | 读 | `read` | `device.read` | none，保持 image-only |

这里的“写”表示会改变浏览器、DOM 或可能触发页面外部副作用，因此受 plan mode、confirmation 和 mutating timeout
语义约束；不以 HTTP 方法或动作看似轻量为由降成 read-only。第一阶段复用现有 `device.read/device.control`，不新增
`browser.*` capability 名；后续若要拆分权限，必须作为独立公开权限协议变更处理。

## 8. 元素快照与 stale-ref 规则

### 8.1 OpenCLI 行为基线

参考本机 `@jackwener/opencli==1.8.6`：

| 安装包文件 | SHA-256 |
| --- | --- |
| `dist/src/browser/dom-snapshot.js` | `9b9dbd40af4a2c669879669dde2256c2fb22418298740e4391e3d001e060418b` |
| `dist/src/browser/target-resolver.js` | `86d99d7be3045b1001dc80d6398c19634ea54dd0b27af1cac2195faf0a575c93` |
| `dist/src/browser/target-errors.js` | `3eb84b869f3fd512f43066f7035c72ff77ad1aa2728beea96a7555d13d5b5a0b` |

已从安装包源码/测试确认的能力：DOM 裁剪、可见性和遮挡检查、Shadow DOM、same-origin iframe、嵌套交互元素
去重、属性白名单、表格/滚动摘要、form compound 信息、semantic find、元素指纹、exact/stable/reidentified/
stale、结构化错误和 fill readback。OpenCLI Bridge 当前未连接，所以其本机真实浏览器运行仍是 `UNVERIFIED`；
HomeMaster 不以此作为验收证据。

### 8.2 HomeMaster 引用模型

- `snapshot_id` 绑定 run/session、BrowserSession generation、page identity 和创建时间。
- `element_id` 只在该 snapshot 内有效，不能由模型猜测另一个 snapshot 的元素。
- 第一阶段 snapshot store 只保留当前最新 snapshot；创建新 snapshot、导航或任一写动作完成后，旧 snapshot 全部失效。
- 内部指纹至少包含 tag、role、accessible name、label、稳定 id、test id、配置允许的场景属性和 frame identity。
- 导航、page replacement、session close 立即失效。
- 第二阶段引入 DOM/action revision 后，才可评估普通 value/checked 变化是否保留同页引用。
- 第一阶段写动作只允许当前 snapshot 的 `exact`；`stable` 和跨动作复用留到第二阶段。
- OpenCLI 的 `stable/reidentified` 第一阶段只作为诊断研究，不进入写动作；第二阶段如启用仍要求 `reidentified` 后重新
  inspect，避免高影响按钮被错误替换。
- 任何多匹配都失败，不提供模型 `nth` 绕过歧义。

## 9. DOM 快照移植边界

以下是第二阶段完整移植目标。第一阶段只实现真实 Ant 页面和 committed control fixture 所需的基础可见性、语义名称、
唯一目标和 exact snapshot，不以可行性里程碑声称下列高级能力已经完成：

- 可见/零尺寸/opacity/display/visibility 裁剪；
- viewport 扩展和滚动容器摘要；
- button/link 内部图标、SVG、文本的 bounding-box 去重；
- modal/overlay 遮挡判断；
- open Shadow DOM；
- same-origin iframe；cross-origin frame 的能力探测使用可信本地 fixture。Playwright/CDP 具体能力在当前运行时核对前保持
  `UNVERIFIED`，生产级 frame origin policy 在第二阶段实现；
- 交互 role、landmark、属性白名单和 accessible name 组合；
- form state、select/date/file compound 信息；
- 增量 diff/new element 标记；
- token/深度/iframe/文字长度上限。

明确修改 OpenCLI 行为：

- 不往公开工具暴露 CSS path；
- 不接受任意 `nth`；
- 不使用页面全局 ref 作为跨 run 权威，改为服务端 snapshot namespace；
- 不允许写动作自动接受 `reidentified`；
- 不依赖 Chrome extension/CDP Bridge；使用 HomeMaster Playwright page/frame；
- 不把 OpenCLI 的 AX prototype 名称直接当作已验证 API。Playwright/CDP 的实际 AX 能力先在当前 1.61 运行时核对，
  未核对的外部符号保持 `UNVERIFIED`。

第二阶段直接翻译或移植 OpenCLI 代码前建立 `plan/V2.1/opencli-browser-port-manifest.json`，逐项记录来源版本、源文件哈希、移植函数/行为、
HomeMaster 目标文件、行为差异和测试。直接翻译或复制代码时同步更新 `THIRD_PARTY_NOTICES.md`，保留 Apache-2.0
归属；不得只在代码注释中口头提及。

## 10. 通用模块与文件边界

建议建立以下唯一实现边界；实施时如代码事实要求改名，必须在 handoff 记录原因，不能改变职责：

- `src/homemaster/browser/contracts.py`
  - BrowserSession protocol、snapshot/element/action receipt、error code 和 action lifecycle 合同。
- `src/homemaster/browser/inspection.py`
  - DOM/AX 采集、裁剪、语义名称、compound 和输出预算。
- `src/homemaster/browser/targets.py`
  - snapshot store、指纹、page generation、过期/歧义解析。
- `src/homemaster/browser/playwright_session.py`
  - 唯一 Playwright 生命周期、导航、动作、等待、截图和线程所有权。
- `src/homemaster/browser/factory.py`
  - 通用 `BrowserSessionFactory`、run-scoped 创建/关闭、失败回滚和显式 enabled 配置；不得 import benchmark。
- `src/homemaster/browser/tools.py`
  - canonical RegisteredTool 定义、直白 schema/description、ToolExecutionResult 投影。
- `src/homemaster/browser/policy.py`
  - 第一阶段顶层可信 origin、协议、超时和输出上限；第二阶段扩展 redirect/popup/frame 策略。策略由 composition 注入，
    不读取 Coworker 配置。
- 通用 composition root
  - 浏览器启用时在 provider request 前创建 session、按 run dependencies 绑定工具并注册 run-scope 清理；未启用时不
    暴露工具；用 immutable per-run tool view 同时约束 provider manifest 与 dispatch，不修改全局 Registry。
- `src/homemaster/benchmarking/coworker_demo/browser_adapter.py`
  - Coworker action lifecycle、route/bid/job/state-version/evidence 映射。

完成迁移后删除 Coworker 的旧通用职责实现，不保留第二套 driver/tools。必要的 Coworker adapter 可以留在 benchmark
目录，但不得实现 DOM 查找、fill/click/select。

## 11. Coworker 外围迁移（第二阶段）

### 11.1 动作前

Coworker adapter 在通用副作用发生前负责：

1. 检查共享终态和 browser budget；
2. 根据当前 run state 预约 action，取得准确 state version；
3. 将通用 action id 和必要的 opaque domain correlation 注入当前页面；
4. 让 Case02 页面继续能把真实后端请求关联到当前 action。

这里必须保留现有 `window.__coworkerAction` 数据流的真实语义，不能在未读清页面 consumer 前删除。目标实现可以
改为通用 action-context bridge，但必须用真实 Case02 页面证明 correlation、state version 和 backend receipt 一致。

### 11.2 动作后

Coworker adapter 从通用 receipt 和受配置允许的内部 DOM metadata 映射：

- URL -> `ticket/monitor/automation` route；
- 元素内部 `data-bid` -> 既有 domain action；
- job row 内部 `data-job-id` -> 准确 job wait；
- DOM readback -> Coworker parameter evidence；
- backend receipt -> current-run evidence refs。

Agent 不再输入 route、bid 或 job-specific wait schema；这些只作为 Coworker 外部评分/编排投影。允许 Coworker
读取的自定义属性通过 adapter policy 配置，不能硬编码到通用 inspection 输出合同。

### 11.3 失败与事务顺序

- `before_action` 失败：不得执行浏览器副作用。
- 预约成功后浏览器失败：调用 `on_error`，保留准确失败事实，不得倒填成功 event。
- 浏览器成功但 Coworker record 失败：通用 receipt 必须保留，整体结果标记 domain-record failure，不能声称
  未发生浏览器动作，也不能静默重放高影响点击。
- stop/timeout 后先查询页面和环境真实终态，再决定是否允许幂等恢复；不能盲目重复 submit。

### 11.4 Coworker 公共迁移

- 更新 Coworker prompt：先 `browser_navigate(url=ticket_url)`，再 `browser_inspect`，使用 element ref；不再指导
  模型使用 `data-bid`。
- Registry 工具数量和稳定顺序加入 `browser_inspect`、`browser_check`、`browser_uncheck`。
- Skill tool_names、presentation projection、trajectory bundle verifier、fixture 和文档同步新公共参数。
- 既有 DAG 和 scorer 可以继续使用 domain `route/bid/job_id`，但必须来自 adapter 的真实映射，不得由模型输入。
- normal 和 anomaly 两个 scenario 逐实例通过；不能用其中一个成功替代另一个。

## 12. 测试优先实施步骤

### 第一阶段 A：通用创建与合同 RED 测试

1. 添加普通 Home composition 启用/禁用浏览器的失败测试：启用时在首次 provider request 前存在 run-scoped session，
   禁用时模型工具列表不包含 `browser_*`，两种路径都不依赖 Coworker import。
2. 添加 canonical 九工具 schema 快照、7.10 读写/权限/资源/verification 矩阵测试。
3. 添加 BrowserSession protocol 实现审计和浏览器核心禁止 import `benchmarking.coworker_demo` 的架构测试。
4. 添加创建失败、run 完成、provider 失败和 application close 的逐实例清理测试。
5. 并发运行一个启用和一个禁用 browser 的普通 Home run，逐 run 断言 provider manifest、dispatch allow-set 和 session
   数量，证明没有修改或泄漏 application-wide Registry。

### 第一阶段 B：最小 inspection、动作与 session 规则

1. 受控 HTTP fixture 覆盖 label、aria-label、role、visible/enabled、重名歧义、基础 input/select/checkbox/radio。
2. 只接受最新 snapshot 和 exact target；新 inspect、导航、写动作后旧引用必须返回 `stale_ref`。
3. 实现九工具基础合同；fill/select/check/uncheck 逐实例 DOM readback，click 只报告 interaction，wait 返回 last-state。
4. 同一 session 并发动作必须串行；timeout/cancel 返回 `outcome_unknown` 后 session fenced，后续动作拒绝且不自动重试。
5. 用可信本地 iframe fixture 单独探测 same-origin/cross-origin 可采集性；结果标明仅是能力验证，不宣称生产策略完成。

### 第一阶段 C：普通 ApplicationRuntime 与 Ant 黑盒门

1. 通过普通 Home `ApplicationRuntime`、canonical Registry 和通用 composition 调用工具，不借 Coworker entry/harness。
2. 用确定性 provider 完成 Ant Automation navigate -> inspect -> 四次 fill（每次重新 inspect）-> click -> wait -> observe。
3. 独立 Playwright locator 逐字段断言 DOM、最终 SUCCESS 文本和 command；断言 Runtime 返回码、工具 JSONL、图片到
   provider request、BrowserSession/Chrome 清理终态。
4. 再用真实 provider 做不含元素编号的同链路验证；失败保持 `UNVERIFIED`，不影响确定性接线事实，但第一阶段不能宣称
   真实模型自主能力通过。
5. 第一阶段结果只记为 `GENERIC_BROWSER_FEASIBILITY_PASS`，不得更新 README/CHANGELOG 为正式发布能力。

### 第二阶段 D：生产级 inspection、policy 与恢复

1. 完成第 9 节 OpenCLI port manifest、来源测试、DOM/AX/Shadow DOM/iframe/遮挡/裁剪和 bounded snapshot store。
2. 引入 DOM/action revision，覆盖 stable/reidentified 诊断、异步导航和 React 重渲染 freshness。
3. 对 redirect、最终 URL、popup 和每个 frame 执行生产 policy；增加允许/拒绝及跨部署 origin 回归。
4. 实现同步 Playwright 超时后的 worker drain、独立终态查询和受控恢复，证明旧动作未结束前 lease/session 不可复用。

### 第二阶段 E：Coworker 单路径迁移

1. 先用失败测试锁定动作前 reserve/context injection、动作后 record/evidence 和失败路径顺序。
2. 将 browser budget/terminal/domain mapping 移入 Coworker lifecycle adapter。
3. 让 Coworker composition 使用通用 `BrowserSessionFactory`；更新 turn dependency、screenshot source、prompt、skills、
   registry、presentation、bundle projection、dataset contract 和 fixture。
4. 删除旧专用 browser tool/driver 行为及死兼容路径，运行 Case02 全部内部与真实外部门。

### 第二阶段 F：发布文档与来源

1. 更新 `THIRD_PARTY_NOTICES.md`、README、两份用户指南、两份架构和 CHANGELOG。
2. 文档给出真实工具调用流程，但不把 CSS、内部指纹或评分答案教给模型。
3. 如发现“单测绿、真浏览器失败”等非显而易见坑，完成根因与修复后更新 `docs/pitfalls.md`；严重问题同时向
   `CLAUDE.md` 加入正向规则。

## 13. 第一阶段真实 Ant Design 外部终态门

### 13.1 前置条件

1. 在 Ant Design 项目依赖环境中建立 Node >=22 的真实运行入口并核对版本；不得用当前 Node 20 启动成功猜测兼容。
2. 使用项目 lock/依赖管理方式，不污染全局依赖。
3. 为 HomeMaster Playwright 1.61 安装或锁定匹配 Chromium，执行真实 launch/page/close preflight。
4. 启动 Ant Design dev server，记录实际 PID、监听地址、端口和日志；如果默认端口被占用，选择新端口并把实际
   origin 注入 BrowserPolicy。
5. 用 HTTP 检查入口和抽样静态资源均成功；HTML 200 不能代替资源可用。

### 13.2 确定性 ApplicationRuntime 门

使用记录型确定性 provider 驱动真实 `ApplicationRuntime`，按工具合同执行：

1. `browser_navigate` 打开真实 Automation URL；
2. `browser_inspect` 分别确认四个必填输入框各有且只有一个匹配；
3. 对 `TenantId`、`ItemCode`、`SpecCode`、`ExtensionName` 逐字段重新 inspect 后调用 `browser_fill`；
4. 每次填入后从独立 Playwright locator/DOM 读取实际值，逐字段断言，不使用“全部里任意一个正确”；
5. 再次 inspect，确认“确认执行”按钮 enabled；
6. `browser_click` 点击该按钮；
7. `browser_wait` 等待 `SUCCESS (exitCode=0)`；
8. 用工具实现之外的独立 locator 读取 execution console，断言准确文本和输入值生成的 command；
9. 调用 `observe`，证明非黑 PNG 到达下一次真实 provider request 的 image block；
10. 断言 Runtime 返回码/终态、工具 JSONL 顺序、每个 action 的 verified readback、浏览器 trace 和无残留进程。

确定性 provider 只消除模型随机性，用于证明接线；它不能证明真实模型会正确选工具。

### 13.3 真实模型 Agent 门

使用仓库真实 provider 配置，通过同一个 ApplicationRuntime 给模型一条不包含元素编号的任务：打开 Automation
URL，检查页面，填写四个给定值，点击确认并验证成功。

验收要求：

- provider 返回成功状态且模型身份/endpoint 在配置允许列表中；
- 模型先 inspect，再使用返回的 snapshot/element 引用；
- 四个字段分别有准确 fill readback；
- 最终页面由独立 DOM 黑盒确认 `SUCCESS (exitCode=0)`；
- provider iteration request/response 和 tool calls 逐轮一一对应，不能只证明 provider 曾调用一次；
- 模型最终文本不能替代真实 DOM；
- 若真实 provider 不可用或模型无法完成，自主模型能力保持 `UNVERIFIED`，不能用 scripted provider 冒充；只要确定性
  ApplicationRuntime 与独立 DOM 门通过，仍可记录第一阶段通用接线可行性通过，但不得进入第二阶段最终发布 DoD。

### 13.4 其他控件真实门

Automation 页面当前没有 select/checkbox。不能用 Automation 的 fill/click 成功声称所有工具有效。

- 在 Ant Design 现有带 `<Select>` 的页面上逐个验证至少一个真实 ARIA combobox，例如 dashboard monitor 的
  Region/集群；每个目标分别断言最终显示值。
- 使用通过 HTTP serve 的 committed browser-control fixture 逐个验证原生 select、checkbox、switch/radio 和
  check/uncheck 幂等行为。
- fixture 只用于通用控件合同，不修改 Ant Design 产品页面制造测试钩子。

## 14. 第二阶段 Coworker 外部回归门

由于本次改动跨越 Coworker 核心动作流，单测不能替代真实 benchmark：

1. 更新 `config/coworker_demo.yaml` 的真实本地配置时只改 gitignored 文件；example 只写可移植占位说明。
2. 运行 `scripts/coworker_demo/preflight.py`，真实 runner Python、service Python、Chrome、VNC、FFmpeg、tmux、
   bubblewrap 和 provider 每项分别通过。
3. 从真实顶层 `homemaster shell` 运行 normal 和 post-change-anomaly 两个 scenario。
4. normal 独立要求 24/24 trajectory、14/14 result、终态 complete、准确 job 和进程返回码。
5. anomaly 独立要求 22/22 trajectory、11/11 result、add grep 0、remove grep 1、终态 rolled_back。
6. 两个 run 分别运行 `verify_run_bundle.py --expected-model mimo-v2.5`；不得用一条成功或 aggregate best 掩盖另一条。
7. 分别检查视频/trace/manifest、Chrome 和服务清理终态，无遗留进程或端口。

如果本次工具迁移导致现有 Coworker live gate 不能完成，整体计划不能宣称完成。

## 15. 测试与质量命令范围

第一阶段按依赖顺序运行，所有命令逐项检查退出码：

1. 新增浏览器核心和工具 focused tests；
2. ApplicationRuntime、通用 composition、Registry、ToolExecutor、observe/provider projection 回归；
3. 全量 `tests/homemaster`；
4. Ant Design 现有 Vitest、TypeScript/lint（使用 Node 22+）；
5. HomeMaster Ruff、format check、compileall、`uv lock --check`、`git diff --check`；
6. installed wheel 从空 cwd 启用普通 Home browser run 并审计全部浏览器工具；
7. 第 13 节真实外部终态门。

第二阶段在上述门基础上增加：

1. `tests/homemaster/benchmarking/coworker_demo`；
2. `tests/case02_openenv`；
3. `tests/coworker_demo` bundle/presentation verifier；
4. 第 14 节两个真实 Coworker 外部 run；
5. 生产级 redirect/frame policy、timeout recovery 和 snapshot freshness 对抗测试。

不得用最后一条命令 exit 0 覆盖前序失败。预先存在的无关失败必须稳定复现、记录根因和范围，不能顺手修或删测试。

## 16. 文档同源清单

第一阶段不可发布的可行性里程碑更新本计划、handoff、实验架构文档和可复查验证记录，不把 README 能力清单或用户指南
写成已发布。若第一阶段代码形成 commit，仍按仓库纪律在 CHANGELOG 记录“实验性、默认不可见、尚未发布”的真实范围，
不能因不发布而漏记 commit。

第二阶段用户可感知能力正式交付时同步：

- `README.md`：工具能力清单、受控 Chrome 前置条件和真实使用入口。
- `docs/generic-browser-tools-user-guide.md`：工具含义、inspect -> action -> wait -> observe 工作流、错误恢复。
- `docs/architecture/generic-browser-tools.md`：BrowserSession、snapshot、action lifecycle、轨迹和安全不变量。
- `docs/coworker-demo-user-guide.md`：移除 data-bid 用户指导，更新工具数和真实操作流程。
- `docs/architecture/coworker-demo.md`：评分/在线约束如何位于通用浏览器外围。
- `THIRD_PARTY_NOTICES.md`：OpenCLI 1.8.6 Apache-2.0 来源和移植范围。
- `CHANGELOG.md`：改了什么、为什么、兼容性影响和验证结果。
- 本计划与 handoff：更新实际状态、命令、结果、阻塞和偏差。

若最终创建 commit，commit message 必须与 CHANGELOG 条目同源，不能只写泛化标题。

## 17. 分阶段完成定义（DoD）

### 17.1 第一阶段可行性完成

- [ ] `BrowserSessionFactory` 位于通用层，普通 Home composition 能显式启用并创建 run-scoped session。
- [ ] 禁用时模型不看见 `browser_*`；启用时九工具与 `observe` 在首次 provider request 前绑定同一 session。
- [ ] 启用/禁用并发 run 的 immutable tool view 分别约束 provider manifest 与 dispatch，全局 Registry 无变更或泄漏。
- [ ] 九工具 schema 和第 7.10 节读写/权限/资源/verification 矩阵锁定并通过审计。
- [ ] 最新 exact snapshot、写后失效、单动作串行和 timeout 后 session fence 的最小规则通过。
- [ ] 普通 `ApplicationRuntime` 在真实 Ant Automation 完成逐字段 readback、独立 SUCCESS DOM、图片 provider 投影、
      返回码和清理终态门。
- [ ] committed control fixture 的 select/check/uncheck 逐实例门通过；可信 iframe fixture 只记录能力探测结果。
- [ ] focused、全量、lint/format/compile/lock/wheel/diff 检查逐项记录退出码。
- [ ] handoff 明确标记 `GENERIC_BROWSER_FEASIBILITY_PASS` 或准确阻塞，不宣称正式发布或 Coworker 已迁移。

第一阶段完成后停下，由 owner 决定是否进入第二阶段；不得自动把临时双实现发布。

### 17.2 第二阶段最终发布完成

以下全部满足才可进入最终代码评审：

- [ ] 唯一通用 PlaywrightBrowserSession 已替代 Coworker 专用浏览器执行。
- [ ] 九个公开工具契约与本文一致，`observe` image-only 不变量未变化。
- [ ] Agent 不再输入 route、bid、job-specific schema、CSS 或 JS。
- [ ] OpenCLI port manifest、哈希和 Apache-2.0 notices 完整。
- [ ] DOM snapshot、完整语义过滤、stale/ambiguity、revision 和所有动作读回 focused tests 通过。
- [ ] BrowserSession 所有实现/测试 fake 接口审计通过。
- [ ] canonical Registry/ApplicationRuntime/ToolExecutor/observe provider 接线通过。
- [ ] Ant Automation 四字段逐实例 readback、确认执行和独立 SUCCESS DOM 门通过。
- [ ] Ant Design custom select 与 committed control fixture 的 select/check/uncheck 逐实例门通过。
- [ ] 真实 provider Agent 自主选择工具并完成 Ant Mock；否则发布能力保持 `BLOCKED/UNVERIFIED`。
- [ ] redirect/final URL/popup/frame policy 与同步动作 timeout 终态恢复的生产门通过。
- [ ] Coworker normal 和 anomaly 两个真实 run 分别通过评分、返回码、bundle 和清理终态门。
- [ ] README、两份用户指南、两份架构、THIRD_PARTY_NOTICES、CHANGELOG、计划和 handoff 同步。
- [ ] focused、全量、lint/format/compile/lock/wheel/diff 检查逐项记录退出码。
- [ ] 所有用户已有修改保持不丢失，计划外文件无无关重构或格式 churn。

第二阶段全部实现、测试、外部终态验证和文档完成后，才启动唯一一次最终只读 reviewer subagent。主 agent 逐条处理发现；
采纳就做针对性修改与验证，不采纳就记录具体理由，不自动追加第三次评审。

## 18. 停止条件与阻塞处理

- 找不到 Playwright 1.61 可启动浏览器：先建立根因和项目内依赖修复，不使用旧缓存假装通过。
- Node 22+ 无法建立或 Ant dev server/资源失败：保持 Ant 门 `BLOCKED/UNVERIFIED`。
- OpenCLI 外部 API/AX/CDP 符号未在当前真实运行时核对：保持 `UNVERIFIED`，改用已验证 Playwright 能力或停止。
- 普通 Home composition 不能在 provider request 前创建并绑定 BrowserSession：第一阶段失败，不能退回 Coworker 注入
  或测试 harness 冒充通用接线。
- 第一阶段 timeout/cancel 后仍允许同 session 执行新动作：第一阶段失败；无需先实现完整恢复，但必须 fence 并废弃。
- Coworker action context/receipt 在迁移后无法一致关联：停止实现，回到数据流边界取证，不加 mode 或后补评分事件。
- 同一真实页面现象修复 2-3 次仍复现：停止调参，重新检查目标解析/事件分发/React 重渲染的上游假设。
- 用户工作树变更与目标文件冲突：保留用户内容；只有无法合并时才请求具体方向。

## 19. 计划评审记录

2026-07-27 owner 评审锁定以下修订：

1. 浏览器创建必须属于通用 HomeMaster composition，不能只满足 Coworker：已采纳，见 3.5、6、10、12、17。
2. 当前目标先验证能否跑通，跨源 iframe 暂不做完整策略：已采纳并限定为可信本地能力探测，见 5、9、12、17；
   生产策略保留为第二阶段发布门。
3. 每个工具现在明确读取/修改：已采纳，见 7.10。
4. 完整 timeout 恢复与复杂元素 freshness 后置：已采纳；第一阶段只保留 session fence/no-retry 和 latest exact snapshot
   两条最小防护，见 5、8、12、17。
5. 原一次性交付计划拆成第一阶段通用可行性与第二阶段生产迁移：已采纳，见 1、11-17。

此前启动的只读 reviewer 应 owner 要求中断，未形成独立评审结论，不得记为 reviewer PASS。本轮修改仍需 owner 确认后
才能把计划状态改为 `REVIEWED/LOCKED` 并开始第一阶段实现。
