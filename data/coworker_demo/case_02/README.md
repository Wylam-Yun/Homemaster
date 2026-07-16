# Case 02: Tool / Location / Access Log + Black Screen Command

这个 case 验证从 SOP 原文出发，如何找到白屏工具访问日志和黑屏命令日志。

## 匹配流程

白屏工具访问链路：

```text
SOP 原文中的工具语义
  -> test_set/tool_catalog.json
     用中文名 cnName 或英文名 enName 找 toolId
  -> test_set/tool_location_mapping.json
     用 toolId 找 location
  -> test_set/monitor_cls_query_request.json
     用 key_words=schema.location=<location> 构造查询
  -> test_set/operation_access_log.json
     找同 location、同时间窗、同 SOP 语境的访问日志
```

白屏日志只能证明访问了某个平台或工具入口。对于自动化执行平台，日志可以证明访问了 `auto_platform` / `/automation/console/script/execute`，但不能把它当成黑屏命令执行结果。

黑屏命令链路：

```text
begin_time/end_time
  -> test_set/black_screen_output.json 的 cmd_infos
  -> 规范化 SOP 原文里的命令
  -> 与 cmd_infos[].cmd 比较
```

## 目录结构

```text
case_02/
  dataset_manifest.json
  README.md
  test_set/
    item_change_ticket.json
    tool_catalog.json
    tool_location_mapping.json
    mcp_query_config.json
    monitor_cls_query_request.json
    operation_access_log.json
    black_screen_output.json
  schema/
    tool_catalog.json
    tool_location_mapping.json
    monitor_cls_query_request.json
    operation_access_log.json
    black_screen_output.json
    validation_chain_ground_truth.json
  ground_truth/
    validation_chain_ground_truth.json
```

## 文件职责

- `test_set/item_change_ticket.json`: 输入变更单。
- `test_set/tool_catalog.json`: 工具中文名/英文名到 `toolId` 的目录。
- `test_set/tool_location_mapping.json`: `toolId` 到 `location` 的映射。
- `test_set/mcp_query_config.json`: 外部传入时间窗的 mock 配置。
- `test_set/monitor_cls_query_request.json`: 已构造好的白屏查询请求样例，关键字段是 `key_words/begin_time/end_time/search_scene`。
- `test_set/operation_access_log.json`: MCP 返回的白屏工具访问日志样例。
- `test_set/black_screen_output.json`: 黑屏命令接口输出样例。本 case 只放一条真实终端 `grep -A 3` 命令。
- `ground_truth/validation_chain_ground_truth.json`: 最终标准答案，包含 SOP 原文、匹配依据、命中的具体日志或未命中原因。
- `schema/`: 单条记录的字段结构说明。
- `dataset_manifest.json`: 数据集清单和记录数量，不是匹配关系。

## Ground Truth 字段

`ground_truth/validation_chain_ground_truth.json` 每条记录表示一个从 SOP 原文出发的验证点。它不是中间链路答案表，而是最终评测答案。

```json
{
  "sop_text": "告警检测（必选）：检查当前未闭环告警...",
  "source_field": "operate_description",
  "expected_status": "matched",
  "evidence_type": "white_screen",
  "evidence_scope": "tool_access",
  "match_basis": [
    "SOP 原文出现工具语义：告警检测",
    "tool_catalog: 告警检测/QueryAlarmAndSlaStatus -> toolId=monitor_alarm_query",
    "tool_location_mapping: monitor_alarm_query -> location=/monitor/alarm/sla/query",
    "monitor_cls_query_request: key_words=schema.location=/monitor/alarm/sla/query"
  ],
  "matched_logs": [
    {
      "source_file": "test_set/operation_access_log.json",
      "time_local": "2026-01-18 22:05:10",
      "location": "/monitor/alarm/sla/query",
      "content": "tool=QueryAlarmAndSlaStatus; ..."
    }
  ],
  "unmatched_reason": null
}
```

`evidence_scope` 的含义：

- `tool_access`: 白屏日志证明访问了具体监控工具入口。
- `platform_access_only`: 白屏日志只证明访问了自动化执行平台/工具入口，不能证明脚本参数的最终执行结果。
- `command_execution`: 黑屏日志证明终端命令执行记录存在。
- `unmatched`: 当前测试集没有足够日志证明该 SOP 原文片段。

## 当前覆盖

当前共 16 条标准答案，粒度是 `sop_text_verification_point`：

- 变更前：告警、拨测、容量、核心组件性能、性能检测，均命中白屏工具访问日志。
- 变更前配置确认：缺少对应日志，标记为 unmatched。
- 变更执行：自动化执行平台访问日志命中，创建后 `grep` 黑屏命令命中。
- 变更后：告警、拨测、容量、核心组件性能、性能检测，均命中白屏工具访问日志。
- 变更后业务验证：自动化执行平台访问日志命中，scope 为 `platform_access_only`。
- 回退：自动化执行平台访问日志命中；回退后的 `grep` 黑屏命令缺少独立日志，标记为 unmatched。

## 时间窗来源

`item_change_ticket.json` 不包含本次变更的真实开始/结束时间。第一版不往变更单里加字段，而是由外部配置传入时间窗：

```text
test_set/mcp_query_config.json
  -> change_time_window.begin_time
  -> change_time_window.end_time
  -> test_set/monitor_cls_query_request.json
```

后续真实业务里，这个配置可以替换成工单系统、调度系统或独立时间窗工具的输出。