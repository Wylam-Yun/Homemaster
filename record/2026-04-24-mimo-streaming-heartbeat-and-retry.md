# 2026-04-24 Mimo Streaming 心跳与重试实验

## 关键结论

- Mimo 支持 Anthropic-compatible `stream: true`，返回 `text/event-stream`。
- streaming 可以作为 agent 的“活性判断”：只要持续收到 SSE event，就说明连接没有断，Mimo 还在工作。
- 非流式请求只能等最终返回或 timeout，中间无法判断是正常生成、排队还是卡死。
- 本次 `max_tokens=128` 时，流式内容主要被 `thinking_delta` 消耗，最终没有正文输出；这说明 API 仍在工作，但 token 预算太小。
- agent 应把 `thinking_delta` 当进度/心跳信号，不要当最终答案；最终答案应看 `text_delta` 或完整 text block。

## 本次响应时间

| 测试 | 结果 |
| --- | ---: |
| 非流式请求 1 | 3733.12 ms |
| 非流式请求 2 | 3349.01 ms |
| streaming 首次响应头 | 605.38 ms |
| streaming 首个 data event | 605.69 ms |
| streaming 完整结束 | 2644.55 ms |
| streaming 最大 event 间隔 | 439.13 ms |

## Agent 处理建议

- 优先使用 streaming 调 Mimo。
- 记录 `first_event_ms`、`last_event_at`、`total_ms`。
- 如果收到 stream event，就标记为“仍在工作”。
- 如果长时间没有 event，再判定 stalled 并 retry。

建议 timeout：

```text
connect_timeout: 10s
first_event_timeout: 30s
stream_idle_timeout: 15-30s
total_timeout: 180s
```

建议 retry：

```text
timeout -> 取消请求 -> 1s/2s/4s backoff 重试
连续失败 N 次 -> 标记 Mimo unhealthy -> 切备用 provider
后台 probe 恢复后 -> 再放回可用池
```
