# Browser Gateway 用户指南

## 用途

`homemaster --gateway --browser` 让现有飞书 Gateway 驱动一个部署者配置的浏览器
Mock UI。Browser profile 保留 Home 通用工具，增加通用浏览器工具；它不加载 ALFWorld
或 Coworker 旧工具。本功能的已验证目标是 Ant Design Pro Mock UI，不代表真实业务系统
已经发生变更。

## 配置与启动

真实凭据和环境地址只写入 Git ignored、mode-0600 的 `config/homemaster.yaml`：

```yaml
browser_gateway:
  start_url: http://127.0.0.1:8000/dashboard/automation
  allowed_origins:
    - http://127.0.0.1:8000
  headless: true
  action_timeout_ms: 15000
  navigation_timeout_ms: 30000
  wait_timeout_ms: 10000
```

Ant Mock UI 必须已启动，Automation route 必须直接返回可操作页面而不是登录页。HomeMaster
不会启动、重启或关闭这个外部前端。启动 Gateway：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --browser \
  --config config/homemaster.yaml
```

`--browser` 只能与 `--gateway` 使用，并与 `--alfworld` 互斥。每个 Gateway 进程固定一个
环境，不按飞书消息临时切换模式。

## 从飞书执行变更单

飞书消息只需包含自然语言要求和一个可读取的票据地址，例如：

```text
请按这个变更单在自动化页面执行，完成每个重要步骤后截图并回填：
https://docs.example/change/CASE-02
```

模型先加载 `change-ticket-executor`，再用 Home 通用读取工具读取地址。票据不要求严格
schema；标题、段落、列表和表格会一起解释。Skill 只定义通用元流程：提取并锁定计划、
执行、读取外部终态、逐步截图、页面回填、确认和按票据回滚。任何具体字段、命令、顺序
和回滚值都必须来自票据，不在 prompt 或 Skill 中硬编码。

## 截图与回填

每次 navigate、写操作、wait 或 `browser_backfill` 后，Runtime 会把下一次工具选择限制为
`observe`。该 PNG 同时进入下一轮模型上下文，并沿现有 Gateway MEDIA 链发送到飞书；模型
看图后继续执行，不等待人工批准，除非消息明确要求暂停。

`browser_backfill(snapshot_id, element_id)` 使用当前页面 PNG 触发目标控件的真实 clipboard
paste。只有页面明确接收 paste、DOM 随后变化且页面中的图片预览与该 PNG 字节完全一致才
返回成功；回执同时给出原图和预览 SHA-256。模型随后重新 inspect、点击页面
的确认回填控件，再 `observe` 展示确认状态。普通文本框拒绝图片 paste 时，工具返回
`backfill_rejected`，不会伪造成功。

Browser Gateway 不设置数值型工具迭代预算，完整票据轨迹不会被默认 12 次工具调用截断；
这一行为只在 `--browser` 绑定层生效，不改变现有 ALFWorld Gateway。

## 演示验收

演示成功至少同时满足：

1. 每个票据字段分别从页面 DOM 精确回读。
2. 页面命令预览包含所有目标值。
3. 页面显示 `SUCCESS (exitCode=0)`，而不仅是点击回执。
4. 回填预览真实出现，确认后页面显示已确认状态。
5. 每个重要动作和回填都有飞书可见的观察图。
6. browser action JSONL、Playwright trace 和 WebM 非空，run 结束后浏览器 session 已关闭。

Deterministic bundle 的 verifier 会从包内原始 `runtime_events.jsonl` 重新构建执行轨迹，
再与 `trajectory.jsonl` 逐项比较。只有私有临时目录通过哈希、工具生命周期、三个 SOP 阶段、
终端验证和外部终态校验后，bundle 才会原子发布；失败不得留下标记为成功的正式目录。

当前完整正常与异常回滚轨迹见
`data/browser_demo/case_02/agent_trajectory_ground_truth.md`。该文件是冻结规范：当前
Mock UI 已验证正常实现段，完整正常后检和异常回滚 UI 仍标 `UNVERIFIED`。真实 provider
或飞书展示若未在当次部署验收，不应由确定性 Mock 测试替代宣称。
