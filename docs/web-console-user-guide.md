# HomeMaster Web Console 用户指南

Web Console 是 `ApplicationRuntime` 的本地浏览器 adapter。它不另建 Agent Runtime，不改变飞书等既有
公开事件投影；浏览器使用独立的 allowlist 投影接收实时 thinking、回答、工具、usage、审批和 run 终态。

## 启动

先按 README 配置 Python 环境和 provider，然后运行：

```bash
scripts/homemaster serve
```

默认地址是 `http://127.0.0.1:8000`。可指定其他 loopback 地址和端口：

```bash
scripts/homemaster serve --host 127.0.0.1 --port 8765
```

当前版本没有认证，只允许 `127.0.0.0/8`、`::1` 或 `localhost`。`0.0.0.0`、LAN 地址和其他 hostname
会在模型、工具和 Runtime 构造前直接失败。不要用端口转发把它公开到不可信网络；需要远程访问时应先在
可信 reverse proxy 增加认证和 TLS。

## 使用

- 左栏创建、选择和恢复 session；移动端通过左上角按钮打开 session side sheet。
- 连接状态为 `connected` 后才能发送；重连期间可以阅读，但 composer 禁止发送。
- Thinking 默认折叠，首个 reasoning delta 到达就出现；展开后查看完整流，snapshot 会校准最终文本。
- 每个工具调用按 `tool_call_id` 独立显示参数、结果、错误和 artifact；artifact 下载仍校验
  tenant/session/run 分区和 opaque handle。
- Stop 请求 Runtime 取消当前 session run。
- 危险操作显示一次性审批框。Reject 不调用工具 backend；重复、过期或已消费的 approval ID 会失败。
- WebSocket 最后一个订阅者断开、run 取消、超时或服务关闭时，pending approval fail closed。

MVP 重连采用“重新获取 history，再继续 live events”；断线窗口里的动画 delta 不回放，最终 thinking/answer
snapshot 负责校准。

## 前端开发

需要 Node.js 22+：

```bash
cd web
npm install
npm test
npm run typecheck
npm run build
```

生产 build 写入 `src/homemaster/web/static_dist/`，由 Python wheel 的 package-data 携带。开发服务需要把
`/api` 和 `/api/events` 代理到本地 HomeMaster 服务；发布前必须使用 `npm run build` 并运行 Python Web
测试。

## 故障排查

- `event_stream_not_ready`：等待连接状态变成 connected 后再发消息。
- `session_busy`：当前 session 已有 run，等待结束或点击 Stop。
- `approval_not_found`：审批已解决、过期或因断线清理，刷新状态后重新发起任务。
- 页面只显示旧资源：重新执行 `npm run build` 并刷新浏览器。
- `/usr/bin/google-chrome` 缺失与 Web Console 服务本身无关；只有依赖远端浏览器环境的单独工具/基线需要它。
