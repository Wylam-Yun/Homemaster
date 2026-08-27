# Web Console Full-Auto / 双窗口录制交接

更新时间：2026-08-26

## 目标

在 `hkust4` 上运行 Homemaster Web Console，让变更单可以直接从网页输入并自动执行；另开一个浏览器窗口
实时展示同一 session 的 thinking、回答、工具名称、工具参数和工具结果，用于同步录制。

## 本地已完成的改动

- `src/homemaster/web/serve.py`
  - 普通 Web、browser Web、ALFWorld Web 三个入口统一使用 `PermissionMode.FULL_AUTO`。
  - 启动前探测 loopback 端口；发现 `EADDRINUSE` 时在构造 Runtime 前报错并提示换端口。
  - 受限环境禁止创建 socket 时跳过探测，由 Uvicorn 执行最终 bind。
- `web/src/App.tsx`
  - 支持 `?session_id=<id>`，录制窗口固定连接指定 session。
  - 支持 `?record=1`，默认展开 thinking 和工具 Arguments。
  - 指定 session 不存在时提示错误，不会自动切换到其他历史会话。
- `web/src/components/ReasoningRow.tsx`、`ToolCallCard.tsx`、`styles.css`
  - 增加录制模式展开和较大字号。
- `src/homemaster/web/static_dist/`
  - 已重新执行前端生产构建，静态 bundle 已更新。
- README、架构文档、Web 用户指南、CHANGELOG、session handoff 已同步更新。

## 本地验证

- `cd web && npm test -- --run`：25 passed
- `cd web && npm run typecheck`：通过
- `cd web && npm run build`：通过
- `./.venv/bin/pytest -q tests/homemaster/web/test_serve.py`：19 passed，1 skipped
- `./.venv/bin/ruff check src/homemaster/web/serve.py tests/homemaster/web/test_serve.py`：通过
- 完整 Web 测试曾因环境依赖长时间无输出后终止，不能视为通过。

## 手动同步到 hkust4

以下命令从本地工作区执行。不要加 `--delete`，以免删除远端已有的运行产物和视频：

```bash
rsync -az \
  --exclude .venv \
  --exclude node_modules \
  /hpc2hdd/home/wyuan140/weilin_workspace/Homemaster/ \
  haodong2@hkust4:/home/haodong2/weilin/red_bird/Homemaster/
```

同步后在 `hkust4` 上确认：

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
git status --short
rg -n "permission_mode=PermissionMode.FULL_AUTO" src/homemaster/web/serve.py
rg -n "record=1|指定会话不存在" web/src/App.tsx
```

如远端 Node 依赖或静态 bundle 不一致，再执行：

```bash
cd web
npm install
npm run build
cd ..
```

## 启动和端口纪律

启动前只允许保留一个 Homemaster 服务监听目标端口：

```bash
ss -ltnp | egrep ':(8000|8890|8891|8765)\b' || true
```

若 `8890` 已有可用 Homemaster，直接复用，不要再启动第二个；若被其他进程占用，改用 `8891`：

```bash
scripts/homemaster serve \
  --host 127.0.0.1 \
  --port 8890 \
  --browser \
  --config config/homemaster.browser.yaml
```

服务必须绑定 `127.0.0.1`，远程访问使用 SSH tunnel，不要绑定 `0.0.0.0`。

## 双窗口录制

1. 在主窗口打开 Web Console，创建或选择 session，记下 session ID。
2. 在第二个窗口打开：

   ```text
   http://127.0.0.1:8890/?record=1&session_id=<session-id>
   ```

3. 在主窗口输入变更单原文并发送。两个窗口必须使用相同 session ID。
4. 录制窗口会实时收到同一 run 的 thinking、回答、工具参数、工具结果和终态。

Web 入口已固定 `full_auto`，因此不会等待 Approve/Reject；denied tools、敏感路径和命令规则仍然有效。

## 当前阻塞

Codex 沙箱无法执行远端同步。SSH 在远端命令执行前失败：

```text
No user exists for uid 205096
```

原因是沙箱内 UID `205096` 无本地 passwd/NSS 映射，且沙箱网络被禁用；你的正常终端可以 SSH，因此请由你的
终端执行上面的 `rsync` 和启动验证。不要把本地测试结果当作远端运行成功的证据。
