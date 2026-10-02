# Vendored: AgentScope

- **Upstream**: https://github.com/agentscope-ai/agentscope
- **Version**: 2.0.10dev
- **Commit**: `a1f30d48b6de5bfb25a4bf2c5fbf1b6442f5106d` (2026-09-30)
- **Copied**: entire upstream `src/agentscope/` package (verbatim, including `py.typed`, `_models/*.yaml`, `Dockerfile*.template`, alembic assets)
- **License**: Apache-2.0 — see `LICENSE` in this directory
- **Why vendored**: 2.0.10dev is not published to PyPI; HomeMaster's agent-foundation migration (plan/V3.7) requires this exact dev-version API surface.
- **Discipline**: keep this tree verbatim. Local patches MUST be recorded in the patch list below; an upstream upgrade = replay the whole tree at a new commit + re-apply listed patches + run contract tests.

## Local patches

1. `middleware/__init__.py` — replaced eager re-exports with PEP 562 lazy
   `__getattr__`. Upstream pulled optional `tts`/`classifier`/`mem0`
   subpackages whenever `agentscope.middleware` (or just `MiddlewareBase`)
   was imported; lazy resolution keeps extras-gated code unloaded until its
   middleware is actually requested. Public API unchanged.
2. `model/_anthropic/_model.py`, `model/_openai_chat/_model.py`,
   `model/_ollama/_model.py`, `model/_utils.py` — preserve provider-native
   `stop_reason`/`finish_reason`/`done_reason`
   on `ChatResponse.metadata["stop_reason"]` (stream chunk → accumulator →
   final response; non-stream parsers set it directly). HomeMaster's
   `finish_reason` contract (e.g. `max_tokens` → `length` truncation
   detection) depends on the native value upstream discarded.
3. `agent/_agent.py` `_handle_error_tool_call` — attach
   `metadata={"hm": {"data": {"backend_attempted": False, "status": ...}}}`
   to the `ToolResultBlock` it persists and to the `ToolResultEndEvent` it
   yields. Upstream built both without metadata, so HomeMaster's canonical
   `ToolResultMessage.data` (and the projection's `backend_attempted`
   field) lost the machine pocket on denied/interrupted/error results.
4. `formatter/_anthropic_formatter.py` — emit `"is_error": true` on
   `tool_result` blocks whose `ToolResultBlock.state` is not `SUCCESS`, so
   denied/interrupted/error results are distinguishable from successful
   ones on the Anthropic wire.
5. `agent/_agent.py` `_reply_impl` `finally` — when the reply generator is
   being closed (`aclose()`/GC finalization, `GeneratorExit` propagating),
   skip the yielding of `end_event`/interruption `AssistantMsg` (yielding
   during close raises `RuntimeError: async generator ignored
   GeneratorExit`); the unfinished-tool-call cleanup still runs to keep
   `state.context` consistent, discarding its events.
6. `agent/_agent.py` `_execute_concurrent_tool_calls` — added a `finally`
   that cancels and joins the worker `gather_task` on generator close.
   Upstream only handled `CancelledError`; on `aclose()` the task kept
   running detached, mutating `state.context` after the caller's terminal
   snapshot.
7. `tool/_toolkit.py` `call_tool` — re-raise exceptions marked with
   `_hm_propagate` instead of converting them to tool-error chunks.
   HomeMaster stamps `SessionGenerationError` /
   `AutomaticRecallRunDeadlineExceeded` with the flag; upstream's blanket
   `except Exception` would hide the generation fence behind a tool error.
8. `agent/_agent.py` — `import sys` (required by patch 5).
