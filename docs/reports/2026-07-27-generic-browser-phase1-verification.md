# Generic Browser Phase-One Verification - 2026-07-27

## Result

Status is `PHASE_1_IMPLEMENTED_ANT_GATE_BLOCKED`, not feasibility PASS. The generic Runtime path and real local browser
artifacts pass, while the required Ant Automation external terminal-state gate cannot run on this host.

## Passed Gates

| Gate | Result |
| --- | --- |
| Browser/Runtime focused | 15 passed; Ant test skipped only because origin is unset |
| Control fixture | Per-control fill/select/check/uncheck/click/wait/readback PASS |
| Ref/fence | Latest exact stale-ref and timeout session fence PASS |
| Iframes | Same- and cross-origin trusted-fixture collection probe PASS |
| Static quality | Ruff, changed-file format, compileall, lock and diff checks PASS |
| Installed wheel | Empty-cwd wheel import, exact nine tools, Chrome HTTP/DOM/PNG/close PASS |
| Artifacts | JSONL, valid trace ZIP, VP8 WebM and two decoded 1280x720 PNG frames PASS |
| Ant source project | 54 Vitest tests, Biome, TypeScript and production build PASS |

The wheel gate imported `homemaster.browser` from `/tmp/homemaster-browser-wheel-site`, not the checkout. It opened
the committed controls fixture with Chrome for Testing 149, asserted HTTP 200 and DOM readback, then closed the
session. The resulting 1.80-second, 25-fps WebM was decoded by Playwright FFmpeg 1011; the trace archive passed ZIP
integrity. No task-owned 8123/8124 listener remains.

## Blocked Ant Gate

The host has `fs.inotify.max_user_watches=8192`. VS Code file watcher PID 3495762 owns 8113 watches. Umi reports
`OS file watch limit reached` and does not listen even with Watchpack/Chokidar polling enabled. That process may belong
to another IDE session, so it was not terminated.

The successful production build and preview do not qualify as a substitute. HTTP and sampled assets return 200, but
the exported Automation route resolves to Umi `EmptyRoute` and leaves the application root empty. Changing browser
wait parameters cannot repair that routing fact.

## Full-Suite Context

The complete HomeMaster run ended with `1160 passed, 2 skipped, 28 failed`. Every failure originates in the concurrent
memory-system change: `HomeMasterConfig._validate_memory_embedding_provider` rejects older custom-provider fixtures
that omit `MemoryEmbedding`. The browser modules are absent from those stacks. This report records the failures but
does not modify user-owned memory work.

## Remaining Acceptance

On a host that can start Ant dev, run the deterministic Runtime integration test with `HOMEMASTER_ANT_ORIGIN`. Acceptance
must independently assert all four input values, exact command values, `SUCCESS (exitCode=0)`, provider-visible image,
Runtime JSONL, decodable WebM, valid trace, HTTP success and Chrome cleanup. The real Ant `Region` Select and real-provider
agent remain `UNVERIFIED`. Phase two must not start before the deterministic gate passes and phase one is explicitly
marked `GENERIC_BROWSER_FEASIBILITY_PASS`.
