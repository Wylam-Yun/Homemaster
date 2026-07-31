# Browser Gateway 与 Ant Mock 验证报告

## 结论

日期：2026-07-29。`homemaster --gateway --browser`、通用变更单 Skill、完整双分支 GT
和 `browser_backfill` 已实现。确定性 Runtime 门与真实配置 Provider 门都在独立 Ant Design
Pro Mock UI `http://127.0.0.1:8002/dashboard/automation` 达到外部终态。没有修改飞书
Channel 源码，也没有停止或替换当前 ALFWorld Gateway。

## 确定性 Gateway 到 Runtime 外部门

命令：

```bash
HOMEMASTER_ANT_ORIGIN=http://127.0.0.1:8002 \
  .venv/bin/pytest -q tests/homemaster/integration/test_generic_browser_ant_runtime.py
```

结果 `1 passed`。同一次 run 从 authenticated Feishu message 经过 `ChannelBridge`、
`BrowserGatewayApplication` 和 `ApplicationRuntime`，独立断言：

- 四个 input 分别精确回读，命令预览包含全部值。
- 页面显示 `SUCCESS (exitCode=0)`。
- 26 个工具调用逐轮属于该轮 manifest；九张 `observe` 图片实际进入模型上下文。
- 九张 Gateway MEDIA 和 FINAL 的 identity、session、generation、delivery context 一致。
- PNG clipboard paste 被页面接受，预览字节 SHA-256 与回执一致，确认后显示 `已确认回填`。
- action JSONL、Playwright trace ZIP、WebM 和 Runtime trace 非空；session 已关闭。

## 真实 Provider 外部门

使用 ignored `config/homemaster.yaml` 中的真实 Provider 配置。请求只给出本机变更单路径和
要执行的票据步骤，没有给出四个字段答案。模型实际调用 `load_skill`、读取票据、inspect、
fill、observe、click、backfill 和确认，并返回 `completed`。

关闭浏览器前由测试外的独立页面读取取得：

```text
TenantId       = tenanttenanttenant000198
ItemCode       = read
SpecCode       = ext.read.type1
ExtensionName  = read-ext
console        = SUCCESS (exitCode=0)
backfill image = 1
已确认回填     = 1
browser closed = true
```

证据位于：

```text
/tmp/homemaster/runs/browser-real-provider-gate/browser/run-5ac1c79eade9/browser_actions.jsonl
/tmp/homemaster/runs/browser-real-provider-gate/browser/run-5ac1c79eade9/browser_trace.zip
/tmp/homemaster/runs/browser-real-provider-gate/browser/run-5ac1c79eade9/page@e37efb46dfaaac2813fcc19b48becae0.webm
```

## 回归

- Browser/config/CLI/Skill/GT/Gateway binding 最终聚焦回归：`68 passed`；其中 Feishu
  bridge 到 browser binding 的文件内用例为 `2 passed`。
- Gateway/Channel 非 live 回归：`88 passed, 1 deselected`。
- 全量 Gateway/Channel 首轮为 `87 passed, 1 failed`；唯一失败是既有
  `live_alfworld` 用例在代码运行前发现外部 DISPLAY `:107` 已占用。未停止该未知进程，
  并按 marker 排除后复跑全绿。
- wheel 安装产物 Skill 门：`1 passed`。
- HomeMaster 全量非 live：`1590 passed, 1 skipped, 2 deselected, 5 failed`。五项均在
  browser 路径之外并可独立复现：一项现有 memory 权限测试在返回 denied 后仍发现文件已写入；
  一项 cleanup guard 尚未分类 vendored mem0 的 legacy terms；三项 V1.9 release 测试依赖已不存在
  的同级 `OpenHarness` 仓库。它们没有被本任务修改或隐藏，browser 聚焦、真实 Ant 和真实
  Provider 门保持 PASS。

## 边界

Ant 页面明确是确定性 Mock UI；本报告不宣称真实业务系统变更。现有飞书 Gateway 的真实发送
由 owner 已确认正常，本次新增跨边界测试覆盖 authenticated Feishu message、原文、principal、
generation、delivery context 和 terminal final，但没有为了测试而替换当前正在运行的 ALFWorld
Gateway。明日展示需以 `--gateway --browser` 启动独立进程或先受控切换部署。

GT 已完整冻结正常和异常回滚规范，并校验真实票据 SHA-256 与全部 `sop_step_id` 来源。
当前 Ant UI 只实际跑通正常实现段；完整正常后检和异常回滚仍为 `UNVERIFIED`，不得把规范
完整误写成真机执行完整。
