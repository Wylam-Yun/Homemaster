# Ops Monitor Agent 变更执行 Handoff

日期：2026-08-24
状态：方案已锁定，尚未进入 AntD 控件兼容性实现阶段

## 1. 当前目标

让 HomeMaster 通用 Agent 读取下面的 mock 变更单，并在 Ant Design Pro 运维控制台执行完整变更任务：

```text
/home/haodong2/weilin/red_bird/hawkeye/show_data/ops_monitor_agent_demo.source.ticket.json
```

目标执行环境：

```text
/home/haodong2/weilin/red_bird/ant-design-pro
```

Agent 本体：

```text
/home/haodong2/weilin/red_bird/Homemaster
```

## 2. 已确认的执行路线

本任务走 HomeMaster 的通用 Browser Gateway，不走专用 Coworker Demo 路由。

原因：Browser profile 保留 `read_file`、`terminal` 等通用工具，并增加浏览器工具。用户可以直接把本机 JSON 绝对路径写进飞书消息，Agent 自行调用 `read_file` 读取，无需 `dataset_manifest.json`、场景 YAML、DAG 或 `sop_type: CHANGE_SOP` 包装。

启动链路：

```text
飞书用户消息
  -> --gateway 接收消息
  -> --browser 为本次 run 创建独立 Playwright 浏览器
  -> read_file 读取变更单
  -> browser_* 工具操作 Ant Design Pro
  -> terminal 在运行 HomeMaster 的服务器执行验证命令
```

注意：`--browser` 是 HomeMaster 的工具环境选择，不是 AntD 页面路由。当前代码要求 `--browser` 必须和 `--gateway` 一起使用，不能直接用于本地 `homemaster run -p ...`。

## 3. 用户输入

建议在飞书发送：

```text
请读取本机变更单：
/home/haodong2/weilin/red_bird/hawkeye/show_data/ops_monitor_agent_demo.source.ticket.json

按照变更单中 data.sop_change_step 的顺序执行完整变更任务。
在 Ant Design Pro 的运维控制台中完成变更前检查、变更实施和变更后验证；
每一步按照 operate_description 操作，并按照 operate_verified 验证。
任何前置检查或验证不通过时立即停止，不得继续执行后续步骤。
```

## 4. 启动方式

AntD 前端需要先运行在：

```text
http://127.0.0.1:8000
```

HomeMaster 启动命令：

```bash
cd /home/haodong2/weilin/red_bird/Homemaster

PYTHONPATH=src .venv/bin/python -m homemaster.cli \
  --gateway \
  --browser \
  --config config/homemaster.yaml
```

`config/homemaster.yaml` 已补充并验证：

```yaml
browser_gateway:
  start_url: http://127.0.0.1:8000/ops/alarm-query
  allowed_origins:
    - http://127.0.0.1:8000
  headless: true
  action_timeout_ms: 15000
  navigation_timeout_ms: 30000
  wait_timeout_ms: 10000
```

配置文件权限仍为 `0600`。不要把该真实配置或其中的凭据提交到 Git。

## 5. 今天已经完成的修改

### 5.1 开发环境永久免登录

修改：

```text
/home/haodong2/weilin/red_bird/ant-design-pro/src/app.tsx
/home/haodong2/weilin/red_bird/ant-design-pro/src/app.test.tsx
```

开发环境现在直接注入 `Ops Demo / admin` 用户，不请求 `/api/currentUser`，页面切换不再跳 `/user/login`。生产环境认证逻辑保留。

验证证据：

- `src/app.test.tsx`：8/8 通过。
- Biome 对 `src/app.tsx`、`src/app.test.tsx` 检查通过。
- 全新无状态浏览器直接访问 `/ops/alarm-query`，最终 URL 未跳转，页面出现“告警取证台”，没有“登录”按钮。
- `npm run tsc` 仍有原有的 `config/config.ts:36 TS2883`，与本次登录修改无关。

### 5.2 Browser Gateway 启动配置

已在真实、gitignored 的 `config/homemaster.yaml` 中加入 Browser Gateway 配置。`load_config(...).browser_gateway.require_runtime()` 验证通过。

### 5.3 服务器终端配置 fixture

已创建：

```text
/home/haodong2/weilin/red_bird/ant-design-pro/mock/fixtures/monitor-agent/agent.conf
```

当前内容：

```ini
agent_version=1.0.0
monitoring_enabled=true
node=fixture-node-01
```

变更单 `change_implement[0].operate_verified` 已改为在运行 HomeMaster 的服务器终端执行：

```bash
grep -q "1.0.0" \
  /home/haodong2/weilin/red_bird/ant-design-pro/mock/fixtures/monitor-agent/agent.conf \
  && echo CONFIG_VERSION_OK
```

JSON 格式检查通过，命令实际输出 `CONFIG_VERSION_OK`。

重要：当前 fixture 初始就是 `1.0.0`，所以这里只证明终端验证可执行，尚未证明执行动作把版本从旧值改为新值。

## 6. 已复现的实际阻断

### 6.1 AntD 下拉框

正常期望：

```text
点击“云服务”
  -> 等待下拉列表出现
  -> 页面检查返回所有选项
  -> Agent 选择“监控 Agent 服务”
  -> 再读取当前值确认成功
```

当前 HomeMaster 行为：

```text
点击下拉框
  -> 不等待 AntD 异步渲染完成
  -> 立即查询 [role=option]
  -> 找不到选项
  -> option_not_unique / match_count=0
```

同时，通用 `browser_inspect` 当前没有把下拉选项列为可返回交互元素。因此 Agent 不能在失败后重新检查并看到选项列表。

实测：

- `Region` 按标签检索不到对应控件。
- “云服务”控件能找到，但选择“监控 Agent 服务”返回 `match_count=0`。
- Region 默认已是 `fixture-region-01`，告警级别默认已是 `critical + major`，可以只校验；云服务默认是 `VPC`，必须修改，因此是硬阻断。

### 6.2 AntD 时间选择器

HomeMaster 能找到并打开“时间窗”，也能看到“选择年份”“选择月份”，但页面检查不会返回日期“21”和时、分、秒选项。

Agent 即使在截图中视觉上看见这些内容，也只能把 `element_id` 交给 `browser_click`；日期和时间项没有 `element_id`，当前又没有坐标点击工具，因此无法完成变更单中的细粒度时间选择。

### 6.3 取证抽屉

变更单要求的业务流程与页面一致：点击“取证”，选择变更单、步骤、字段，点击“确认取证”。但这些字段仍使用 AntD Select，可能遇到同样的选项不可见问题。

另外，通用 `change-ticket-executor` Skill 仍强制要求 `browser_backfill` 把截图粘贴到页面图片控件；当前 EvidenceDrawer 没有图片粘贴控件。需要决定以变更单原生 EvidenceDrawer 为准，还是修改页面增加图片回填。推荐以前者为准，截图继续通过 `observe` 发给模型和飞书，不强制页面粘贴。

### 6.4 Mock 状态没有因果闭环

资产查询当前始终返回 `fixture-node-01 / running / 1.0.0`，即使没有执行变更也会通过变更后核查。

终端 fixture 当前也预先是 `1.0.0`。因此网页“执行成功”、资产 `1.0.0`、终端 `1.0.0` 三者目前没有真实状态关联，可能形成假成功。

## 7. 推荐解决方案

### 7.1 浏览器控件：HomeMaster 为主，前端辅助

HomeMaster 应完成：

1. 点击下拉框后等待选项实际出现，而不是立即查询。
2. `browser_inspect` 返回下拉选项、日期单元格和时间选项。
3. 允许 Agent 按名称点击这些元素，并在操作后重新读取选中值。
4. 失败时返回当前发现的可用选项，不能只给 `match_count=0`。
5. 增加当前真实 AntD `/ops/alarm-query` 的集成回归测试。

AntD demo 可以辅助：

1. 给 Select、RangePicker 增加明确的可访问名称。
2. demo Select 关闭虚拟渲染，降低自动化的不确定性。
3. 增加稳定测试标识，但不要把 AntD 私有 CSS 类硬编码进 HomeMaster。

不增加坐标点击。所有操作必须通过可检查、可回读的语义元素完成。

### 7.2 终端变更闭环

推荐把 fixture 初始值改为 `0.9.0`，在 `change_implement` 中让 Agent 在服务器终端执行真实修改，例如：

```bash
sed -i \
  's/^agent_version=.*/agent_version=1.0.0/' \
  /home/haodong2/weilin/red_bird/ant-design-pro/mock/fixtures/monitor-agent/agent.conf
```

然后用更精确的命令验证：

```bash
grep -Fxq 'agent_version=1.0.0' \
  /home/haodong2/weilin/red_bird/ant-design-pro/mock/fixtures/monitor-agent/agent.conf \
  && echo CONFIG_VERSION_OK
```

还需要让资产查询读取同一份 mock 状态，避免执行前后都固定返回 `1.0.0`。

## 8. 已锁定的后续工作

1. 增强 HomeMaster 通用浏览器能力：底层共用弹层语义发现，`browser_inspect` 返回下拉、日期和时间元素，`browser_select` 等待选项、精确选择并回读验证；日期时间使用 `browser_inspect + browser_click`，不增加坐标点击。
2. 调整 mock 因果闭环：fixture 初始为 `0.9.0`，执行动作将其更新为 `1.0.0`，终端精确验证同一文件，资产查询读取同一状态。
3. EvidenceDrawer 的结构化业务取证取代 Skill 对 `browser_backfill` 的无条件要求。

## 9. 工作区注意事项

- HomeMaster 与 AntD 工作区均已有其他未提交修改，不得 reset、checkout 或清理无关文件。
- `config/homemaster.yaml` 包含真实配置并为 gitignored、mode 0600，不得输出或提交其中凭据。
- 不要把专用 Coworker `case_02` 的锁定数据包限制重新套到这份通用 Browser 变更单上。
- 不要宣称完整变更已跑通；当前只验证了登录关闭、Browser 配置加载、页面可直达、终端 fixture 命令可执行，并复现了 Select/RangePicker 阻断。
