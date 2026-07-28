# HomeMaster V2.1 通用浏览器工具层实施交接

## 当前状态

- 日期：2026-07-27
- 阶段：`PHASE_1_IMPLEMENTED_ANT_GATE_BLOCKED`
- 实现：第一阶段 A-B 与 C 的本地、fixture、安装 wheel 门完成；真实 Ant dev 外部门未完成
- 正式计划：`plan/V2.1/generic-browser-tools-implementation-plan-zh.md`
- 实验架构：`docs/architecture/generic-browser-tools-phase1.md`
- 验证记录：`docs/reports/2026-07-27-generic-browser-phase1-verification.md`
- reviewer：owner 明确要求本轮不再 review；未启动 reviewer
- 结论：不得标记 `GENERIC_BROWSER_FEASIBILITY_PASS`，不得进入第二阶段或宣称正式发布

## 已完成实现

1. 新增 `homemaster.browser` 唯一通用边界：contracts、inspection、targets、policy、tools、factory 和
   `PlaywrightBrowserSession`。
2. `ApplicationRuntime` 在第一次 provider request 前创建 run-scoped session，从 application Registry 派生 frozen
   per-run Registry，provider manifest 与 dispatch 使用同一视图；启用/禁用并发 run 不修改全局 Registry。
3. 九工具为 `browser_navigate`、`browser_inspect`、`browser_fill`、`browser_select`、`browser_check`、
   `browser_uncheck`、`browser_click`、`browser_wait`、`observe`。`observe` 保持 image-only。
4. 已实现 trusted-origin、latest-only exact snapshot、写后失效、目标指纹核对、fill/select/check/uncheck readback、
   click interaction receipt、bounded wait last-state、session 串行和 timeout/cancel fence。
5. 每个 run 生成 Playwright trace、动作 JSONL 和 WebM；session 正常、provider 构造失败、接口审计失败、run 结束和
   application close 都进入清理路径。
6. 第一阶段没有修改 Coworker 旧 Driver，没有加入 Coworker/Ant mode，也没有开放 CSS、XPath、JS 或任意 URL。

## 已通过验证

- 浏览器与 run-scope focused：`15 passed`；真实 Ant integration 因无 origin `1 skipped`。
- 已验证真实 fixture：fill、native select、check/uncheck、click、wait、stale ref、异步 DOM quiet window、同源和跨源
  iframe 能力探测、timeout 后 fence。
- Runtime/Registry/Executor/observe 扩展回归在当前字节上 `81 passed, 1 skipped`；生命周期修复定向回归 `9 passed`。
- Ruff、changed-file format、compileall、`uv lock --check`、`git diff --check`：返回 0。
- wheel：构建成功；从 `/tmp` 空 cwd 加载安装产物，确认模块来自 wheel target，九工具精确审计通过，并真实启动
  Chrome 149 完成 HTTP 200、DOM fill readback、PNG、close。
- wheel run 的 trace ZIP 完整；WebM 为 VP8、1280x720、25 fps、1.80 秒，Playwright FFmpeg 1011 返回 0 并实际解码
  两张 1280x720 PNG。8123/8124 和本次 Chrome 已清理。
- Ant 前端：Node 22.23.1 下 `54 passed`，Biome、`tsc --noEmit`、`npm run build` 返回 0。

## 未通过或未验证

- 全量 HomeMaster：`1160 passed, 2 skipped, 28 failed`。28 项都由并行 memory-system 工作树中的
  `_validate_memory_embedding_provider` 触发：旧测试的自定义 providers 没有 `MemoryEmbedding`。浏览器调用栈未参与；
  本任务不得修改或回退该用户工作。
- 真实 Ant dev server：`BLOCKED`。Linux 用户级 `max_user_watches=8192`，VS Code watcher PID `3495762` 占 8113；
  其归属无法确认，因此未终止。Umi 在 polling 模式下仍拒绝启动。
- Ant production preview 不能代替：入口和抽样资源虽为 HTTP 200，但目标路由最终加载 Umi `EmptyRoute`，`#root` 为空。
- Ant Automation 四字段逐实例 readback、独立 SUCCESS DOM、准确 command、Runtime JSONL、observe provider image、
  WebM/trace 和清理的同一次外部门仍未运行。
- Ant Monitor `Region` 的真实 ARIA combobox 选择保持 `UNVERIFIED`；不得用 native select fixture 为它背书。
- 真实 provider 自主选工具门保持 `UNVERIFIED`。redirect/popup/frame 生产策略、完整 timeout 恢复、snapshot revision、
  OpenCLI live Bridge 和 Coworker 迁移均属于第二阶段，未实施。

## Mac 续跑

Ant Design Pro 终端：

```bash
cd <ant-design-pro>
node --version  # 必须 >= 22
npm ci
npm test
npm run lint
npm run start -- --port 8124
```

确认 `http://127.0.0.1:8124/dashboard/automation` 是真实页面而非空 root 后，在 HomeMaster 终端：

```bash
cd <Homemaster>
uv sync --extra dev
.venv/bin/playwright install chromium
HOMEMASTER_ANT_ORIGIN=http://127.0.0.1:8124 \
  .venv/bin/pytest -q tests/homemaster/integration/test_generic_browser_ant_runtime.py -vv
```

测试通过后仍需人工独立核对同一次 run 的四个 input DOM 值、完整 command、SUCCESS 文本、Runtime JSONL、图片、
WebM 解码、trace ZIP 和 Chrome/端口清理。随后尝试真实 provider；失败必须保持 `UNVERIFIED`。全部第一阶段门通过后只把
状态改为 `GENERIC_BROWSER_FEASIBILITY_PASS` 并停下，等待 owner 决定是否进入第二阶段。

## 环境与工作树

- HomeMaster：`/hpc2hdd/home/wyuan140/weilin_workspace/Homemaster`
- Ant Design Pro：`/hpc2hdd/home/wyuan140/weilin_workspace/ant-design-pro`
- 基线：`bb927d4c6e11f1fe291db0e1d9b0ecb7adfd34d1`
- Playwright：`1.61.1-beta-1782139630000`；Chrome for Testing `149.0.7827.55` / v1228
- OpenCLI 1.8.6 的外部 live 行为仍为 `UNVERIFIED`；本实现无 daemon/Bridge 运行依赖。
- 工作树同时包含 memory system、Gateway 等用户修改；严禁 reset、checkout 或覆盖。未创建 commit，README、用户指南、
  CHANGELOG 未把本实验写成发布能力。
