# V2.1 Memory System Verification

Date: 2026-07-27

## Result

- Full non-live repository suite: `1238 passed, 2 skipped`.
- Focused memory/runtime slice: `99 passed`.
- BM25 forced-offline gate: `1 passed` with offline mode and unreachable proxies.
- Ruff, focused format, lock, build and isolated wheel gates: PASS.

## External terminal-state gates

- SiliconFlow returned HTTP success for `Qwen/Qwen3-Embedding-8B`; every dense vector had exactly 4096 finite values.
- Chinese fact and procedure queries returned the intended records. Update retained the same ID, repeated procedure add
  remained idempotent, and delete was absent from raw Qdrant plus an independent reopen.
- Raw points contained the named BM25 sparse vector. Semantic, BM25 and exact branches each contributed independently
  labeled results.
- Controlled outbound capture saw only `/v1/embeddings` and the exact model. Bodies contained no API key, evidence ref,
  URL query, actual procedure input or chat messages; socket/DNS capture saw no telemetry host.
- An unsupported raw record schema was excluded from normal results and emitted only a stable code, ID hash and
  match-source diagnostic. Direct get failed closed.
- A timed-out sync mutation retained the store lock until its worker completed; close waited, and an independent reopen
  read the terminal state. No automatic retry occurred.
- A same-path Qdrant lock conflict appeared in doctor as a memory-backend warning while file memory remained readable.

## Installed artifact gate

The wheel was installed with all declared dependencies into a fresh Python 3.14 venv outside the source checkout.
`homemaster`, `mem0`, `fastembed` and `qdrant_client` imported; the default Home registry exposed exactly the six V2.1
memory tools and no legacy Home memory tools; disabled mode exposed none. `homemaster/prompts/soul.md` and
`homemaster/memory/threat_patterns.json` were readable package data.
