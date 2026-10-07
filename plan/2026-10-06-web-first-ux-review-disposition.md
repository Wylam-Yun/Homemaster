# 评审处置记录 — web-first UX 与可分发安装计划

评审对象：`plan/2026-10-06-web-first-ux-and-install.md` v1
评审方式：独立上下文子 agent，全部断言对照真实代码复核
Verdict：需修订后实施（5 BLOCKER + 7 IMPORTANT + 1 YAGNI）

## BLOCKER 处置（全部采纳）

| # | 评审发现 | 处置 |
|---|---|---|
| B1 | wheel 的 `Requires-Dist: mindmemos` 会逼 pip 上 PyPI 解析，`uv tool install` 必死 | 采纳。W1.5 新增"从 dependencies 删除 `mindmemos`"为显式步骤；`traced` 列入审计 |
| B2 | `EmbeddedMindMemOS` 在 `if memory.enabled:` 块内与文件记忆捆绑，`mindmemos=None` 会连带杀死 `file_memory_store`/`frozen_memory_context`；`profiles.py:177` 一刀切注册 7 个记忆工具 | 采纳。W1.2 改为"拆块"（files 档保留 file store + frozen context）；W1.3 明确 files 档只注册 `context_memory`、摘掉 6 个 `mindmemos_*` |
| B3 | `ask_user` 无回传通道（web 路径返回 `waiting_user` 终态）、`compact`/`status` 无 REST、skill 解析依赖 client 本地 registry | 采纳。W2.1 协议补齐清单显式列入：pending-question registry + answer 端点、compact/status 端点、skills/resolve 端点 |
| B4 | files 档下 `doctor` 硬 import mindmemos/qdrant 必 FAIL，且它是 shell 启动闸 | 采纳。W1.4 独立交付物：doctor 按 mode 分档 |
| B5 | WS 断连即取消 pending approval + 无 replay buffer + `run.completed` 空 payload | 采纳。W2.2 新增"断连契约"小节：审批不因断连取消（服务端 durable + 宽限期）、重连 history+pending-approvals 回补、completed 带终态 |

## IMPORTANT 处置（全部采纳）

| # | 发现 | 处置 |
|---|---|---|
| I1 | W1 验收用 `-p` 依赖 W2 thin client，阶段顺序矛盾 | W1 验收改走 compose 层 pytest + serve+curl smoke，不依赖瘦客户端 |
| I2 | `POST /messages` 要求先 WS 订阅（409） | W2.3 写明"先订阅后发送"顺序 |
| I3 | extras 清单漏了 mindmemos vendored 闭包顶层依赖（omegaconf/jieba/litellm/traced/structlog/numpy）；posthog/sqlalchemy 疑似死依赖 | W1.5 改为显式依赖审计步骤，逐一判归属；死依赖删而非搬 |
| I4 | 配置路径 fallback 到 `cwd`，安装形态下 auth 产物会写飞 | W1.1 增 `HOMEMASTER_HOME`（默认 `~/.homemaster`）与四段解析序 |
| I5 | `run` 的 compose 期 flag 无跨进程对应物；session/cron/memory 子命令是本地第二写者 | W2.3 增 `--local` 逃生舱语义 + session 子命令转 API + cron/memory 标"server 同机管理面" |
| I6 | thin client 同机假设未写 | W2.3 写死"工具在 server 侧执行，SSH tunnel 只是 UI 通道" |
| I7 | files 档下 alfworld/browser/gateway/web 记忆页行为无定义 | W1.6 补定义（能跑但无轨迹记忆；memories API 503+前端降级） |

## YAGNI 处置（采纳）

`GET /api/models` 全量 catalog 砍掉：`model_override` 只能匹配已配置 provider，catalog 是
装饰品。本期 picker = `GET /api/providers` 已配置项 + 自由文本 model id
（cline `ModelPickerWithManualEntry` 形态）。litellm/models.dev catalog 列为后续项。

## 评审纠错的计划断言（v2 已修正）

- `base.py:443` 实为 `if enabled:` 块内构造，非"无条件"
- 重依赖 import 本来就是函数级，W1.3 改为"保证 files 档不可达"而非"下沉 import"
- `model_override` 是"匹配已配置 provider"语义而非任意 model id（W2.1 写明改写路径）
- `websockets` 非正式依赖（lark-oapi 传递引入）→ 开放问题 4
- litellm 是普通依赖非 vendored；cline 实际组件名 `ClineModelPicker`/`ModelPickerWithManualEntry`
- web 端点清单补全 memories/permissions revoke 系列

## 结论

v2 修订稿已吸收全部评审意见。剩余待用户裁决项收敛为 §5 四条
（auto-spawn 策略、enabled 迁移期、`-p` 兼容期、websockets 依赖形态）。
