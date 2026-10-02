# 裁决④：MCP 验收矩阵与 `mcp/client.py` 去留

状态：已裁决（Phase 0 先决文档）
问题：上游 `agentscope.mcp.MCPClient` + `Toolkit(mcps=)` 能否替代
`homemaster/mcp/client.py`（`McpClientManager`）+ `adapter.py`？

## 0. 验收矩阵（逐项对照真实源码）

| HM 要求 | HM 实现 | AS `MCPClient`/`MCPTool` | 裁决 |
|---|---|---|---|
| 断连 fencing：连接断开后 fail-fast，不得继续调用 | `_mark_disconnected` + `_is_disconnect_error`（client.py:52/208），断连后 `_require_connection` 拒绝 | `_validate_connection` 只查 `self._is_connected` 布尔位；**断连异常不改状态、不阻止后续调用**（`_mcp_client.py:542`） | AS 不满足 |
| audit sink 失败隔离：审计失败不得中止业务 | `_audit` 捕获 sink 异常 → `McpAuditFailure` typed 留存（client.py:65/73） | **无 audit 概念**，只有 `logger` | AS 不满足 |
| artifact tenant ACL：MCP 大输出写盘为 `hm-artifact:` 句柄 + sha256 | `adapter.py` 经 `ToolOutputStore` 落 ACL artifact，模型只见 preview+handle+sha256 | `_convert_mcp_content_to_blocks` 把 MCP content **原样塞进 ToolChunk**——无落盘、无 ACL、无大小门 | AS 不满足 |
| `outcome_unknown` 分类：调用已发出后失败不得误报"未执行" | adapter/executor 链对 call-after-start 错误归 `outcome_unknown` | `call` 中 `session.call_tool` 异常**原样上抛**；`isError` 只映射 ERROR——无"已开始与否"区分 | AS 不满足 |
| tenant 隔离 | `McpClientManager` 由宿主持有，tenant 边界在上层 | 无 tenant 概念 | AS 不满足 |
| 未验证外部工具 fail closed | HM 纪律：未核对的 read-only annotation 按 mutating 处理 | `is_read_only = tool.annotations.readOnlyHint`——**直接信任服务端自报**（`_adapters.py`） | AS 违反纪律 |
| `check_permissions` 接 HM 许可链 | adapter 委托 `PermissionChecker.evaluate_tool` | `MCPTool.check_permissions`：read_only→ALLOW，否则 ASK——**绕过 HM deny/能力规则** | AS 不满足 |
| 多 server 管理/状态会计/重连 | `connect_all`/`reconnect_all`/`list_statuses` | 单 server client，无 manager | AS 不满足 |
| stateful 连接复用 + 清理 | per-server connection + `aclose`/`McpCleanupError` 聚合 | `connect`/`close`（`ignore_errors` 吞错） | AS 部分覆盖 |

## 1. 裁决

**`homemaster/mcp/client.py` + `adapter.py` 保留，为唯一 MCP 传输与适配层。**

MCP 工具进 AS 的路径与本地工具相同：`McpClientManager` → HM `adapter.py`
（artifact/ACL/输出 schema）→ `ToolRegistry` → `HomeToolAdapter` → `Toolkit`。
AS `Toolkit(mcps=)` 与 `MCPClient` **不启用**——不是不能跑，是它绕开了
HM 的 audit/artifact/许可/outcome 语义，且信任服务端自报的 readOnlyHint。

如未来上游补齐 disconnect fencing/audit hook/typed outcome，可在 Phase 3
重议；届时以本矩阵逐行复核为准。

## 2. 显式否决项

| 方案 | 否决理由 |
|---|---|
| `Toolkit(mcps=[AS MCPClient])` 直挂 | 上表 8 项不满足；`is_read_only` 信服务端自报是安全回退 |
| HM client 作传输 + AS MCPTool 作注册 | MCPTool 仍自做 `session.call_tool` 与结果转换，artifact/审计链被绕过 |
| 双轨（普通工具走 AS、MCP 走 HM adapter） | 工具面分裂，`Toolkit` 的 available_tools/schema 生成对两类不一致 |

## 3. 验收断言

- MCP 工具经 `HomeToolAdapter` 注册进 `Toolkit` 后 `get_tool_schemas` 可见、可调用（spike 套件扩展一条 MCP 路径用 `tests/homemaster/fixtures/fake_mcp_server.py`）
- 断连注入：第二次调用 fail-fast 且 audit 有 typed failure
- 大输出经 `ToolOutputStore` 落盘，模型只见 `hm-artifact:` 句柄
