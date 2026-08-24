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

固定 episode 的 ALFWorld 模式复用 Gateway 已有的 worker、tool profile 和 session owner：

```bash
scripts/homemaster serve --alfworld --host 127.0.0.1 --port 8765
```

该模式要求 ignored `config/homemaster.yaml` 的 `alfworld_gateway` 已配置 `asset_root`、`data_root`、
`config_path`、`python_executable` 和 `trial_manifest`。第一个执行任务的 Web session 独占 episode；其他
session 会收到 `alfworld_session_busy`，重启服务才会创建全新 episode。每个会改变环境的
`robot_go_to` / `robot_manipulate` 都经过现有审批框，Approve 恢复同一个被阻塞的 tool call，Reject 在
backend 前失败关闭。

从本机访问远端服务器时，保持服务绑定 loopback，并另开本机终端建立 tunnel：

```bash
ssh -N -L 8765:127.0.0.1:8765 hkust4
```

然后打开 `http://127.0.0.1:8765`。不要把未认证端口绑定到 `0.0.0.0`。

当前版本没有认证，只允许 `127.0.0.0/8`、`::1` 或 `localhost`。`0.0.0.0`、LAN 地址和其他 hostname
会在模型、工具和 Runtime 构造前直接失败。不要用端口转发把它公开到不可信网络；需要远程访问时应先在
可信 reverse proxy 增加认证和 TLS。

## 使用

- 左栏创建、选择和恢复 session；移动端通过左上角按钮打开 session side sheet。
- 连接状态为 `connected` 后才能发送；重连期间可以阅读，但 composer 禁止发送。
- Thinking 默认折叠，首个 reasoning delta 到达就出现；展开后查看完整流，snapshot 会校准最终文本。
- 每个工具调用按 `tool_call_id` 独立显示参数、结果、错误和 artifact。`image/*` artifact 直接显示在
  对应工具卡片内，点击图片可放大；关闭按钮、`Esc` 或点击遮罩可退出，`Open original` 保留原图入口。
  图片加载失败和非图片 artifact 继续显示授权链接；所有读取仍校验 tenant/session/run 分区和 opaque handle。
- Stop 请求 Runtime 取消当前 session run。
- 危险操作显示一次性审批框。Reject 不调用工具 backend；重复、过期或已消费的 approval ID 会失败。
- WebSocket 最后一个订阅者断开、run 取消、超时或服务关闭时，pending approval fail closed。

MVP 重连采用“重新获取 history，再继续 live events”；断线窗口里的动画 delta 不回放，最终 thinking/answer
snapshot 负责校准。

## 记忆管理

左栏顶部可以在“对话”和“记忆管理”之间切换。切换到记忆页面不会关闭当前会话连接；“历史会话”标题右侧的按钮可以折叠或展开会话列表，折叠状态保存在当前浏览器中。

记忆页面目前只提供查看能力：

- “生效中的记忆”“已归档的记忆”“记忆总数”和“来源会话”都从当前数据动态计算，不是固定数字；
- “来源会话”只计算非空 `session_id` 的去重数量，“未关联会话”不计入该数字；
- 生效和归档记忆分标签显示，可以按正文、会话标题、记忆 ID 搜索，也可以按记忆类型筛选；
- 记忆严格按照保存时的 `session_id` 分组，同一 session 归入同一组；没有 session 的记录统一放入“未关联会话”；
- 分组标题优先使用该 session 的第一条用户消息，读取失败时显示短 session ID；会话组和历史会话列表都可以折叠；
- 点击记忆可以查看完整正文、类型、状态、时间、归档原因、校验后的结构化信息和已有版本历史；损坏的原始结构化 JSON 不会返回浏览器；
- 当前页面没有新增、编辑、删除、归档、恢复或入库审核操作，后续审核功能将另行接入。

如果记忆服务不可用，页面会显示独立的中文错误和重试按钮，不影响对话页面继续使用。远端访问方式不变：服务保持 loopback 绑定，并使用前文的 SSH tunnel。

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
- `alfworld_session_busy`：固定 episode 已归另一个 Web session 所有；恢复原 session 或重启服务。
- `ALFWorld Gateway requires configured paths`：补齐真实、ignored 的 `alfworld_gateway` 路径配置。
- `display ... is not usable`：启用 `manage_xvfb` 并使用空闲 display，或为既有 Xvfb 正确提供授权。
- 页面只显示旧资源：重新执行 `npm run build` 并刷新浏览器。
- 记忆页面提示“记忆服务暂不可用”：确认当前组合已启动 MindMemOS，然后点击“重新加载”；对话功能仍可继续使用。
- `/usr/bin/google-chrome` 缺失与 Web Console 服务本身无关；只有依赖远端浏览器环境的单独工具/基线需要它。
