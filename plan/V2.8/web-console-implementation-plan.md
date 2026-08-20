# V2.8 Web Console Implementation Plan

> Status: discussion captured 2026-08-20. Owner: user (self-implement). This document
> freezes every decision reached so far so implementation can proceed without re-deriving.
> Decisions marked **DECIDED** are closed; items marked **OPEN** still need a call before
> the corresponding code is written.
>
> **Normative spec:** `web-console-spec.md`. Where this earlier discussion record differs
> from the spec, the spec wins. The spec closes the runtime lifecycle, permission boundary,
> thinking scope, frontend reuse strategy, request correlation and MVP reconnect decisions.

## Goal

Give HomeMaster a **browser** Agent console (not a TUI): a FastAPI service layer wrapping the
existing `ApplicationRuntime`, plus a browser React frontend that talks to it. Operations use case
is one driver, but the console is domain-general. This is the first HTTP/browser surface for
HomeMaster, which today has only the CLI and the Feishu Gateway.

## Reference Projects (selectively adapted)

- **OpenHarness** (`/hpc2hdd/home/wyuan140/weilin_workspace/OpenHarness`) — HomeMaster's upstream base.
  It has a terminal TUI only (React+Ink in `frontend/terminal/`, stdin/stdout `OHJSON:` line protocol,
  Python backend host `src/openharness/ui/backend_host.py`) plus a read-only static dashboard
  (`autopilot-dashboard/` reads `snapshot.json`). **No browser agent console.** Reuse its *backend-host
  logic* (protocol translation, permission Future handshake, event reduction), not its transport.
- **deepseek-harness** (`/hpc2hdd/home/wyuan140/deepseek-harness`) — the browser reference. It is a
  TS/Node monorepo, so its application runtime is not portable into the Python project. Selectively
  adapt the pure React components and client algorithms listed in `web-console-spec.md`; reuse three
  design areas:
  1. **Transport model** — HTTP POST upstream for commands (request/response, carries `rpcId`) +
     WebSocket downstream for events (`packages/client/connection/src/client/web-api-client.ts`).
  2. **Event protocol shape** — the `MuxFrame` union in
     `packages/host/apiproxy/src/api/events.ts` (session/event, approval/requested|resolved,
     question/requested|resolved, session/queue, session/jobs, session/projection) and the
     reconnect-with-generation-isolation controller in `connection.ts`.
  3. **Frontend domain split** — the `packages/client/ui-*` component layout
     (conversation / tool / plan / goal / jobs / subagent / sidebar / settings).

Hermes Agent is a secondary source for isolated generic components only. Do not copy its PTY/xterm
ChatPage or dashboard-specific API/auth/profile/plugin dependencies. Both source projects are MIT;
copied or derived source must follow the notice requirements in the spec.

## Closed Decision Disposition

The normative spec resolves the OPEN items retained later in this discussion record:

| Item | Locked result |
| --- | --- |
| Runtime lifecycle | One long-lived `ApplicationRuntime` per Web process |
| Permission subject | Dedicated server-owned Web subject; dangerous operations retain confirmation |
| Session ownership | Single-user local MVP; default loopback; unauthenticated non-loopback bind fails closed |
| Thinking scope | Dedicated `WebEventProjection`; existing Gateway projection remains unchanged |
| Additional events | Only the Web event allowlist in the spec; all others default-deny |
| Frontend reuse | Selectively adapt DeepSeek/Hermes files; do not import either application runtime |
| Request correlation | Browser `request_id`, Web registry, Runtime-owned `run_id` |
| Reconnect | One WebSocket; refetch history/snapshot, no cursor replay in V2.8 |

## Architecture

```
Browser (React + Vite)                      FastAPI layer (NEW, Python)           HomeMaster runtime (EXISTING)
─────────────────────                      ────────────────────────────           ──────────────────────────────
POST /api/sessions            ───────────► session_manager.open_or_resume()  ───► session_id
WS   /api/events?session_id   ───────────► subscribe public_gateway_stream() ─► event stream ready
POST /api/sessions/{id}/messages {text,rpc_id} ─► application.run(RunRequest) ─► agent starts
   ← immediate {accepted: true}             │                                   │
                                            │  events pushed as agent runs:      │
   ←WS tool.call_started                    │  event_bus emits ─────────────────►│ runtime runs + emits
   ←WS tool.call_completed                  │                                   │
   ←WS transport.delta (thinking, streaming)│                                   │
   ←WS assistant.reply                      │                                   │
   ←WS runtime.turn_completed               │                                   │
                                            │  application.run() returns ───────► RunResult
POST /api/approvals/{id} (if approval req) ─► resolve confirmation_handler Future ─► agent resumes
```

The FastAPI layer is a thin translator: left side speaks HTTP/WebSocket to the browser; right side
calls capabilities HomeMaster already has. Nothing in the runtime is rewritten.

## Frontend Subscription — What The Browser Receives

HomeMaster already defines a public projection allowlist
(`src/homemaster/events/public_projection.py`, `_ALLOWED_TYPES`). The browser subscribes to exactly
these (plus thinking, see below). 10 base event types:

| Event | Meaning | Frontend use |
| --- | --- | --- |
| `tool.call_started` | agent invoking a tool | "running <tool>…" row |
| `tool.call_completed` | tool finished (tool_name, result, artifact refs) | render result / screenshot |
| `tool.call_failed` | tool failed | red error row |
| `assistant.reply` | agent text reply | append to assistant bubble |
| `runtime.turn_completed` | turn done, final reply | mark turn end |
| `runtime.turn_failed` | turn failed | error |
| `runtime.budget_exhausted` | budget/turns exhausted | "limit reached" notice |
| `runtime.cancelled` | cancelled | mark cancelled |
| `context.compaction` | context compacted | optional notice |
| `usage.update` | token usage | usage stats |

**Boundary:** model thinking, raw tool internals, and usage detail are NOT in the base allowlist.
Thinking is added back by the streaming-thinking decision below; the rest stay internal-only.

## First Prompt — End To End

1. **Open session** — browser `POST /api/sessions` → backend `session_manager.open_or_resume()`
   → returns `session_id`.
2. **Subscribe events** — browser opens `WS /api/events?session_id=<id>` → backend attaches to
   `public_gateway_stream()` (with thinking streaming enabled). Stream is now live.
3. **Send prompt** — browser `POST /api/sessions/{id}/messages { "text": "...", "rpc_id": "abc" }`
   → backend calls `application.run(RunRequest(text=..., session_id=..., resume=<open?>,
   run_policy=RunPolicy(max_tool_iterations=...)))` — exactly what `interactive_shell.py:160-186`
   does today. **Returns `{accepted: true}` immediately**, does NOT wait for the agent.
4. **Stream events** — while step 3's `await application.run()` is still pending, the runtime emits
   events; the backend forwards them over the WS from step 2. Browser matches on `rpc_id == "abc"`
   and renders under that prompt:
   `tool.call_started → tool.call_completed → transport.delta(thinking)* → assistant.reply → runtime.turn_completed`.
5. **Run returns** — `application.run()` yields `RunResult` (final_reply, status). `final_reply` was
   already delivered via `assistant.reply` in step 4; this is backend bookkeeping only.
6. **Approval / question** — if the agent hits a gated action, `confirmation_handler` suspends on a
   Future. Backend pushes an approval frame over WS; browser shows approve/reject; browser
   `POST /api/approvals/{id}`; backend resolves the Future; agent resumes.

## Streaming Thinking — DECIDED (do streaming)

User wants thinking streamed to the frontend (default collapsed, expand on click). This is NOT the
"send one whole `assistant.thinking` snapshot" minimal option; it is real streaming.

### Root cause of current non-streaming (verified in source)

- The provider **does** yield reasoning deltas: `llm_client.py:339-340`
  `if delta.text_delta or delta.reasoning_delta: yield delta`.
- But the streaming publish callback `generic_runtime.py:926-933` `_publish_text_delta` only emits
  `text_delta` and **drops `reasoning_delta`**:
  ```python
  text = getattr(delta, "text_delta", None)
  if text:                                   # only text_delta published
      await emit("transport.delta", payload={"text_delta": text})
  ```
- So reasoning only surfaces once, whole, at message end from
  `assistant_msg.reasoning_content` → `assistant.thinking` event (`generic_runtime.py:503-507`).

### Constraint to verify BEFORE enabling (UNVERIFIED until tested in real env)

`generic_runtime.py:1273` `_reasoning_only_delta` + the `1234` check treat a provider call whose
deltas are **all reasoning, no text and no tool call** as a **failure** path. Before shipping
streamed reasoning, confirm that forwarding `reasoning_delta` over `transport.delta` does not trip
this failure path. Per CLAUDE.md §3: no fix without root cause; do not assume the enum/API name is
safe — verify in a real environment.

### Work items

1. **`generic_runtime.py` `_publish_text_delta`** — also publish `reasoning_delta`, tagged so the
   receiver can tell thinking vs reply apart (e.g. `transport.delta` payload gains a
   `reasoning_delta` field distinct from `text_delta`, or a `kind` discriminator). One variable
   changed at a time.
2. **`public_projection.py`** — let thinking through. **OPEN: scope** — prefer a per-channel
   `include_thinking` flag on `public_gateway_stream` (default False, Web layer passes True) so the
   Feishu Gateway behaviour is unchanged, rather than globally adding `assistant.thinking` to
   `_ALLOWED_TYPES`. Decide before editing.
3. **Frontend** — buffer `transport.delta` reasoning deltas into a per-turn collapsible "thinking"
   region under the assistant message; default collapsed, click to expand. Reuse the same
   rpc_id/turn_index grouping as the reply.

## FastAPI Service Layer — NEW

New `homemaster serve` subcommand (project already depends on `fastapi` + `uvicorn`). Endpoints
sketch (final schema in implementation):

- `POST /api/sessions` — create/resume session.
- `GET /api/sessions` — list persisted sessions (wraps `session list`).
- `GET /api/sessions/{id}/history` — session transcript.
- `POST /api/sessions/{id}/messages` — send prompt → `application.run()`, returns `accepted` + `rpc_id`.
- `POST /api/sessions/{id}/cancel` — `application.cancel()`.
- `POST /api/approvals/{id}` — resolve a suspended `confirmation_handler` Future.
- `WS /api/events?session_id=<id>` — downstream event stream over `public_gateway_stream()`.
- `GET /api/tools` — `registry.list_tools()` / `to_api_schema()` for tool-call visualization.

### OPEN decisions (resolve before coding these parts)

1. **Runtime lifecycle** — one long-lived `ApplicationRuntime` instance per process (shared across
   requests) vs per-request. Affects concurrency model and the session-ownership design. Recommend
   single shared instance with session-level isolation (matches how the shell uses one `application`
   for many sessions).
2. **Permission subject** — `RunRequest` defaults to `local_operator` with full capabilities
   (`process.exec`, `device.control`, ...). Web exposure needs a chosen `permission_subject` and a
   decision on whether to keep the human-approval gate for dangerous ops. Recommend keeping the
   `confirmation_handler` approval flow (maps to the approval frame above).
3. **Session ownership / multi-user** — how a browser user maps to `tenant_id`/`session_id`. No auth
   layer exists today. For MVP, single-user local is acceptable; note it as a known boundary.
4. **Thinking scope** — per-channel flag (recommended) vs global allowlist add. See above.
5. **Other allowlist openings** — any other currently-private events to surface collapsed (e.g.
   `memory.automatic_recall`, `context.compaction` detail)? None requested yet; default = none.

## Frontend — NEW (browser, React + Vite + TypeScript)

Independent JS project (Homemaster is Python; the frontend is a separate build). Reference
deepseek-harness `ui-*` domain split. Minimum viable surfaces:

- Conversation view (transcript: user / assistant / tool rows), with streaming reply + collapsible
  streaming thinking.
- Tool-call display (name, input, result, error, artifacts).
- Composer (input + send + cancel), rpc_id correlation.
- Approval / question modal (dangerous-op confirm, agent follow-up question).
- Session sidebar (list / new / resume).
- Connection status (connected / reconnecting) from the reconnect controller.

Connection logic ported in shape from `connection.ts`: HTTP upstream + WS downstream, rpc_id
pairing, exponential-backoff reconnect with generation isolation.

## Verification Discipline (CLAUDE.md §2)

When implementing, the black-box gates for the external boundary (FastAPI ↔ runtime ↔ browser) are:

- **External terminal state**, not internal trace: assert the browser actually receives the event
  stream (a real WS connection gets `tool.call_*` / `assistant.reply` / streaming thinking deltas),
  not just that the backend logged "emitted".
- **Return codes / presence**: `POST /messages` returns `accepted`; the corresponding events arrive
  on the WS with matching `rpc_id`; approval round-trip actually unblocks the agent.
- **Per-instance, not aggregate**: each event type asserted individually per turn; do not pass on a
  "best of" check.
- **Streaming thinking**: assert reasoning deltas arrive incrementally (more than one delta for a
  thinking turn), AND that the `_reasoning_only_delta` failure path did not fire (run completes,
  not misclassified as failure).

## Out Of Scope (this version)

- Real robot / VLA / VLM (robot skills stay `skill_mode=simulated`).
- Auth / multi-tenant hardening beyond single-user-local MVP.
- Durability/retry for the FastAPI layer beyond what `application.run()` already guarantees.
- Global thinking allowlist change (Feishu Gateway untouched) unless OPEN item 4 resolves otherwise.

## Change Log

- 2026-08-20: captured full discussion — browser console decision, reference projects, transport
  model, first-prompt flow, streaming-thinking decision with root cause and the
  `_reasoning_only_delta` constraint to verify, FastAPI/frontend sketch, open decisions.
