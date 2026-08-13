# Experience Message Rendering Design

## Goal

Use the runtime trace as the only persisted task record. Build a `TaskTraceEnvelope` in memory, render selected semantic fields into `DialogueMessage` objects, and submit those messages to MindMemOS Vanilla Add.

## Input selection

- `runtime.turn_started.payload.user_text` becomes a `user` message.
- `assistant.thinking.payload.thinking` becomes an `assistant` message prefixed with `[thinking]`.
- Non-empty `assistant.reply.payload.reply` becomes an `assistant` message.
- `tool.call_completed` and `tool.call_failed` become `tool` messages containing the tool name, one copy of its arguments, status, and result/error.
- Session exit reason becomes a final `system` message.
- Transport events, usage events, empty tool-call replies, `tool.call_started`, and duplicate `runtime.turn_completed` replies are omitted.
- Runtime IDs and transport metadata are never rendered.

## Persistence and identity

`TaskTraceEnvelope` remains an in-memory boundary between trace collection and rendering. `task_trace.json` is no longer written. `job.json` remains for idempotency and operation results. The job and input hash use a stable serialization of the rendered dialogue messages, and the extractor version advances to `experience-v2`.

## Verification

Tests assert roles and selected content, preservation of thinking and tool failures, omission of internal IDs and duplicates, absence of `task_trace.json`, stable idempotency, and graceful provider failure.
