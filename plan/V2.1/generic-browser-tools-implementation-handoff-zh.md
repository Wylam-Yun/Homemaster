# HomeMaster V2.1 通用浏览器工具层实施交接

## 当前状态

- 日期：2026-07-27
- 阶段：`OWNER_REVISED_CONFIRMATION_PENDING`
- 实现：未开始
- 产品文件修改：无
- 正式计划：`plan/V2.1/generic-browser-tools-implementation-plan-zh.md`
- 计划 reviewer：曾启动但按 owner 要求中断，未形成独立结论，不得记为 PASS
- 最终 reviewer：尚未启动；只允许在实现、全部验证和文档完成后启动一次

## 已锁定决策

1. 使用方案 3：通用浏览器动作与 Coworker benchmark 编排/评分分离。
2. 继续使用 HomeMaster 启动并持有的 Playwright Chrome，不接管已有 Chrome。
3. 保留 `observe` 作为 image-only 验证工具，新增 `browser_inspect` 获取 live DOM 元素列表。
4. Agent 通过 `snapshot_id + element_id` 操作，不开放 CSS、XPath 或任意 JS。
5. 参考/移植 OpenCLI 1.8.6 的 snapshot、semantic find、target resolver、结构化错误和 readback 行为，
   不引入 OpenCLI daemon/Bridge 运行依赖。
6. 二值控件公开名为 `browser_check` / `browser_uncheck`，不使用 `browser_set_checked`。
7. 通用层只记录客观动作和轨迹；外部 benchmark 使用轨迹与独立真实终态评分。
8. 只有一套服务器端浏览器行为，不增加 Coworker/Ant Design mode。
9. 计划拆为两阶段：第一阶段先证明普通 Home `ApplicationRuntime` 的通用浏览器创建与真实 Ant 页面链路；第二阶段
   再迁移 Coworker 并完成生产加固。
10. 通用 composition 通过 `BrowserSessionFactory` 创建 run-scoped session；Coworker 只能提供配置和外围 lifecycle，
    不能创建生产 BrowserSession。
11. 第一阶段复用 `device.read/device.control`，九工具读写、state effects、resource key 和 verification policy 已在
    正式计划第 7.10 节锁定。
12. 跨源 iframe 第一阶段只在可信本地 fixture 探测能力，不做完整逐 frame policy；不得用于带账号、凭据或生产数据
    的页面。
13. 完整 timeout 恢复与复杂 stale-ref 后置；第一阶段保留 timeout 后 session fence/no-retry 和 latest exact
    snapshot/write 后失效两条最小规则。

## 下一步

1. 等待 owner 确认本轮两阶段修订；未确认前不实施。
2. owner 确认后把计划状态改为 `REVIEWED/LOCKED`，只实施第一阶段 A-C，不自动进入第二阶段。
3. 第一阶段通过后 handoff 只记录 `GENERIC_BROWSER_FEASIBILITY_PASS`，停下等待 owner 决定是否进入第二阶段。
4. 第二阶段完成全部实现、外部门和发布文档后，才启动唯一一次最终代码只读 reviewer。

## 当前阻塞与 UNVERIFIED

- Playwright venv 版本为 1.61.0，但其期望的 `chromium-1228` 可执行文件当前不存在；旧 `chromium-1169`
  不能作为兼容证据。
- `/usr/bin/google-chrome` 不存在，现有 Coworker example config 的默认浏览器路径不可用。
- Ant Design Pro 要求 Node >=22，当前默认 Node 是 20.18.0；此前可通过临时 Node 22 运行测试，但本次尚未锁定
  dev server 的真实启动入口和返回码。
- OpenCLI daemon 在端口 19825 运行但 Chrome Bridge/profile 未连接；这不阻塞方案，因为运行时不依赖 OpenCLI，
  但 OpenCLI 自身 live browser 行为不能作为已验证事实。
- 普通 Home composition 的通用 BrowserSession、Ant Design dev server、真实 provider Agent 尚未运行。
- Coworker 两个 live scenario 属于第二阶段，尚未运行，不阻塞第一阶段可行性结论，但阻塞最终发布。
- redirect/popup/frame 生产策略、同步 timeout 终态恢复和完整 snapshot revision 属于第二阶段；第一阶段不得宣称这些
  安全/恢复能力完成。

## 环境关键事实

- HomeMaster：`/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`
- Ant Design Pro：`/hpc2hdd/home/wyuan140/weilin_workspace/ant-design-pro`
- HomeMaster 基线：`bb927d4c6e11f1fe291db0e1d9b0ecb7adfd34d1`
- OpenCLI：`@jackwener/opencli 1.8.6`，Apache-2.0
- OpenCLI 关键来源哈希已写入正式计划第 8 节。
- Ant Automation 成功文本：`执行状态：SUCCESS (exitCode=0)`
- HomeMaster 工作树已有用户修改；不得 reset、checkout 或覆盖。

## 用户已有工作树修改

计划创建时 `git status --short` 已显示以下用户修改，本任务不得回退：

- `CHANGELOG.md`
- `CLAUDE.md`
- `README.md`
- `docs/pitfalls.md`
- `docs/skills-and-config-user-guide.md`
- `progress.md`
- `src/homemaster/cli/app.py`
- `src/homemaster/cli/gateway_command.py`
- `tests/homemaster/gateway/test_runtime.py`
- `tests/homemaster/test_cli_help.py`
- `tests/homemaster/test_homemaster_cli.py`

后续实施需要更新其中 README/CHANGELOG 等文件时，必须先读取并保留现有用户内容，再做最小范围合并。
