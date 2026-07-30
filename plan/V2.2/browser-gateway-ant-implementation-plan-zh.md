# HomeMaster Browser Gateway 与 Ant Design Pro 接线实施计划

## 0. 状态与目标

- 日期：2026-07-29
- 状态：`IMPLEMENTED_ANT_AND_REAL_PROVIDER_VERIFIED`
- 目标：增加 `homemaster --gateway --browser`，让飞书认证消息进入
  `GenericAgentRuntime`，加载 Home 通用工具、唯一通用变更单执行 Skill 和通用浏览器
  工具层，并在真实 Ant Design Pro Mock UI 完成当前变更单的完整轨迹。
- 约束：不复用 Coworker 旧 `data-bid` 五工具，不修改飞书 Channel、ALFWorld
  行为或现有 prompt，不硬编码 Ant 凭据或任意 URL。

## 1. 当前根因

1. CLI 只支持 `--gateway --alfworld`；当前飞书进程因此固定绑定 `profile=alfworld`。
2. `ChannelBridge` 已生成权威 `RunRequest`，但 browser environment 没有类似
   `AlfworldGatewayApplication` 的绑定层，未注入 `browser_session_factory`。
3. 通用浏览器九工具已能在 `ApplicationRuntime._run_tool_view()` 中按 run 派生
   Registry，但缺少 Gateway composition 和部署配置。
4. Coworker 旧五工具只属于 `environment=coworker`，不得进入 browser profile。
5. Browser Gateway 直接使用飞书正文；正文可包含由通用工具读取的变更单 URL。
6. 当前 Ant dev server 使用 `MOCK=none`，Chrome 会跳转登录页；HTTP 200 只证明
   SPA 壳，不证明 Automation 页面可用。
7. Ant 的 EvidenceBackfill 依赖图片剪贴板 paste；现有浏览器协议只有文本 fill，
   agent 无法完成逐 step 截图回填。必须增加一个通用图片回填动作，不能修改飞书
   Channel 或把回填实现硬编码进 prompt。

## 2. 架构候选与决策

1. 在 `ChannelBridge` 内加入环境分支：代码少，但污染通用渠道边界。
2. 新建 `BrowserGatewayApplication` 薄 wrapper：仿照 ALFWorld，在权威 request
   后注入 factory、固定 profile、解释可信 JSON 附件。改动局部，推荐。
3. 新建独立 Browser runtime：隔离强，但重复 Gateway/session/provider/lifecycle。
4. 同一进程按消息切换 ALFWorld/browser：灵活，但新增路由授权和跨 profile session
   问题，不属于本次需求。

锁定方案 2。启动参数是上游部署决策：一个 Gateway 进程只运行一个 environment，
因此不增加 per-message mode。

## 3. 目标数据流

```text
Feishu authenticated event
  -> FeishuChannel download/containment/dedup
  -> ChannelBridge authoritative RunRequest
  -> BrowserGatewayApplication
       -> profile=browser
       -> exact Feishu text, including any change-ticket URL
       -> dependencies.browser_session_factory
  -> ApplicationRuntime creates run-scoped BrowserSession before provider request
  -> frozen per-run Registry adds browser_* and replaces observe
  -> provider operates configured Ant origin
  -> independent DOM/business terminal assertion
  -> trace/video/browser cleanup
  -> Gateway final to original authenticated delivery context
```

## 4. 配置与公开行为

新增 `browser_gateway` 配置：

- `start_url`：绝对 HTTP(S) URL。
- `allowed_origins`：非空、唯一，且包含 start URL origin。
- `headless` 与三个 timeout。

CLI 新增 `--browser`，只允许与 `--gateway` 使用，并与 `--alfworld` 互斥。
Ant server 是部署拥有的外部 Mock UI；HomeMaster 不隐式启动或重启它。入口必须
可直接访问，当前 `MOCK=none` 实例不得作为通过门。演示结论只宣称模型操作真实
Ant Pro 前端并触发 Mock UI，不宣称外部业务系统真实变更。

## 5. 工具与附件不变量

1. `build_tool_registry(environment="browser")` 保留 Home 通用层全部工具，不加载
   Coworker 旧五工具；新九工具按 run 加入同一个模型 manifest。
2. 每个 browser run 的模型工具精确包含新
   `browser_navigate/inspect/fill/select/check/uncheck/click/wait` 与 `observe`。
3. provider manifest 和 dispatch 使用同一个 frozen Registry。
4. 飞书正文精确保真，不解析附件；变更单 URL 由模型使用现有通用工具读取。
5. navigate、fill、select、check、uncheck、click、backfill、wait 都设置
   `requires_model_observation`，强制下一轮调用 `observe`，让既有 Gateway MEDIA
   通道发送截图后继续执行；不增加人工批准暂停。
6. `browser_backfill(snapshot_id, element_id)` 截取当前页面 PNG，将其作为 clipboard
   image 粘贴到指定可编辑回填控件；只有页面明确接收 paste、DOM 发生变化且预览图片
   SHA-256 与原图一致才成功。
   返回图片 SHA-256、字节数和页面接收证据，不返回图片正文。
7. 新增独立 browser prompt，只说明环境身份、inspect/snapshot 动作合同、观察屏障和
   Mock UI 终态，不包含当前 Case02 的步骤、字段或答案；不修改其他环境 prompt。
8. 新增唯一通用 `change-ticket-executor` Skill。Skill 只描述从任意变更单动态提取并
   锁定步骤、参数、验收、回滚、逐步回填和截图的元流程；不得出现当前票的脚本名、
   字段值、GT node id 或 evaluator 答案。
9. 当前票的完整 GT 独立保存，正常和异常回滚分支均使用新通用浏览器语义，不依赖
   旧 `data-bid`；旧 Coworker GT 保持不变。

## 6. 实施步骤

1. 先写失败测试：CLI/互斥、配置、Registry、wrapper、prompt、Skill、观察屏障、
   backfill clipboard/DOM readback 和 factory/cleanup。
2. 增加 `BrowserGatewayConfig`、example 配置和导出。
3. 增加 `gateway/browser.py` 薄 wrapper；不增加第二套 agent runtime。
4. 扩展 CLI/gateway composition。
5. 扩展 factory 的可选 `start_url`；创建后导航且拒绝偏离配置入口，失败即关闭
   session。navigate 完成后再次核对 final origin。
6. 增加唯一通用 Skill，并把当前 Case02 业务步骤从 browser prompt 移出。
7. 新增当前变更单的通用浏览器完整 GT 与 Markdown review snapshot。
8. 同步 README、Browser/Gateway 架构、用户指南、CHANGELOG 和本计划状态。

## 7. 验证门

### 7.1 内部与 Gateway 边界

- CLI/config/profile/run-scope focused tests 全绿。
- browser manifest 逐项断言新九工具存在，Coworker 旧签名不存在。
- 从真实 `ChannelBridge.handle()` 构造认证 Feishu input，断言 browser profile、
  exact text、tenant/session/generation/delivery context 保持，并在最终 provider
  request 看见新工具 manifest。
- 既有 Feishu Channel/Gateway 测试保持全绿；不修改其接收、下载、bus 和发送实现。
- 接口审计覆盖新增 backfill 方法；真实 paste fixture 逐项断言 PNG 类型/哈希、页面
  接收、DOM 变化和失败控件零成功回执。

### 7.2 真实 Ant 外部终态

1. 独立 preflight 确认 Automation route 未跳转 login，四个输入可见。
2. 顶层 browser Gateway application 用确定性 provider 完成逐字段
   inspect/fill/readback、click、wait 到 `SUCCESS (exitCode=0)`、observe。
3. 对每个 input 分别读取 DOM；独立读取 command preview 与 SUCCESS console。
4. 核对外部返回状态、Runtime JSONL、trace ZIP、可解码 WebM、Chrome 清理。
5. 最后用真实 provider 从 Gateway browser input 自主选择工具，独立断言相同 DOM 终态
   和 Gateway final。失败则保持 `REAL_PROVIDER_UNVERIFIED`。

## 8. 非目标

- 不迁移或修改 Coworker 旧 Case02 DAG、评分、terminal 与 SOP 工具；browser 使用
  独立的完整语义 GT。
- 不同时运行 `--alfworld` 和 `--browser`。
- 不开放 selector/JavaScript/任意 URL，不把截图当授权或业务成功。

## 9. 完成定义

- [x] `--gateway --browser` 可启动且与 `--alfworld` 互斥。
- [x] 飞书正文及变更单 URL 精确保真，现有飞书收发链未被修改。
- [x] 模型看到 Home 通用工具与新通用浏览器契约，不看到 Coworker 旧契约。
- [x] 模型可加载唯一通用变更单 Skill，具体 SOP 只来自变更单。
- [x] `browser_backfill` 在真实 Ant 回填控件完成图片粘贴和确认后的 DOM 终态。
- [x] 当前变更单正常与异常回滚完整版 GT 均冻结并有验证门。
- [x] 确定性 Ant 黑盒门与真实 provider 门分别有准确结果。
- [x] DOM/command/SUCCESS、返回码、Gateway final、trace/video/cleanup 全验证。
- [x] 代码、测试、README、指南、架构、CHANGELOG 同源更新。

验证明细见 `docs/reports/2026-07-29-browser-gateway-ant-verification.md`。真实 Provider
已自主读取票据并完成 Mock UI 操作与回填；本次没有替换正在运行的 ALFWorld Gateway，
因此明日真实飞书触发展示仍是部署演示步骤，不把确定性桥接测试描述成真实飞书发送。
