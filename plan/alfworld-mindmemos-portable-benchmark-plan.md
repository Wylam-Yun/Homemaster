# ALFWorld Visual Benchmark with Portable MindMemOS Plan

## Goal

Make `benchmark-alfworld` run the visual `AlfredThorEnv` harness through the same embedded MindMemOS composition used by
the ordinary HomeMaster application. A benchmark run must expose the canonical MindMemOS tools, perform automatic
recall, own the memory FIFO and backend lifecycle, and fail closed when memory is disabled or unavailable. The hkust4
deployment must use host-independent relative runtime paths. Live model calls are outside this implementation gate.

## Root Cause

`AlfworldApplicationEntry` currently calls the generic `create_application()` factory directly. Its registry receives
only the legacy `memory_mode=disabled|readonly|full` file-memory tools and it never composes `EmbeddedMindMemOS`,
`MemoryAddQueue`, automatic recall, migration, managed Neo4j, or their cleanup ownership. Separately,
`MemoryNeo4jConfig` resolves relative paths against process cwd during Pydantic validation, so the deployed ignored
configuration copied HPC2-only absolute Neo4j and Java paths to hkust4. The synchronized file is byte-identical but not
operationally portable.

## Candidate Designs

1. Delegate `AlfworldApplicationEntry` to `create_home_application(tool_environment="alfworld")` and require the
   returned bundle to contain MindMemOS. This reuses the authoritative registry, startup, recall, FIFO and cleanup
   lifecycle. It requires a narrow composition option for benchmark-owned run/session paths. **Recommended.**
2. Extract a reusable `MemoryComposition` from CLI composition and inject it into the existing ALFWorld entry. This
   creates a cleaner library boundary, but it moves a large amount of stable lifecycle code and expands regression risk.
3. Reimplement MindMemOS construction and startup inside `AlfworldApplicationEntry`. This is locally small but creates
   a second lifecycle implementation that will drift when memory contracts change.
4. Run memory ingestion/search as pre/post benchmark scripts. This avoids composition work but does not make recall or
   tools visible at the actual provider boundary, so it does not evaluate the requested behavior.

Use candidate 1 for the benchmark. Keep candidate 2 as the future extraction point if another non-CLI entry needs the
same composition overrides.

## Implementation

1. Add explicit `runtime_root` and `session_root` overrides to `create_home_application()`. Preserve existing defaults.
2. Make `AlfworldApplicationEntry` own a `HomeApplicationBundle` created with `tool_environment="alfworld"`, the
   episode event sink, and benchmark-owned roots. Require `bundle.mindmemos` and `bundle.memory_add_queue`; otherwise
   raise before any provider call.
3. Remove legacy ALFWorld memory tools from the selected registry. `memory_mode` remains accepted only as a deprecated
   CLI compatibility input and must be `disabled`; canonical MindMemOS availability is controlled only by
   `memory.enabled`. This removes the second writer instead of enabling it.
4. Resolve YAML relative `memory.data_root`, legacy migration sources, `memory.neo4j.home`, and
   `memory.neo4j.java_home` against the loaded config file directory. Direct `HomeMasterConfig(...)` construction keeps
   existing cwd-relative behavior.
5. Provision hkust4 runtime assets under ignored worktree-local `.runtime/` and update ignored
   `config/homemaster.yaml` to relative paths. Do not commit credentials or generated runtime state.
6. Update README, ALFWorld guide, memory guide, architecture, CHANGELOG, and session handoff from the same behavior.

## Verification

1. Unit tests first fail against the old entry, then assert an ALFWorld entry exposes all canonical MindMemOS tools,
   starts and closes the same bundle resources, and fails closed when memory is disabled. Run the interface audit for
   all environment/profile implementations.
2. Config tests load one YAML from a non-cwd directory and assert each relative memory/Neo4j path resolves against that
   YAML directory; absolute paths remain exact.
3. Run focused ALFWorld, composition, config, memory, application and interface-audit tests plus Ruff, compileall and
   `git diff --check`.
4. On hkust4, run secret-safe local doctor without `--live`; no model/provider request is allowed.
5. Start managed Neo4j and embedded MindMemOS through the production composition without a model call. Write one
   benchmark-scoped fact through the real FIFO, check the receipt return status, then open a fresh backend and read the
   exact raw ID/content/type from Qdrant and its Source/`EXTRACTED_FROM` state from Neo4j.
6. Start one real `AlfredThorEnv` worker under a fresh Xvfb display using the locked trial manifest. Assert worker ready,
   successful reset return status, nonblank PNG dimensions/pixels, and clean worker/display termination per instance.
7. Do not run `doctor --live`, `benchmark-alfworld` with a real provider, or any command that sends benchmark content to
   a model during this implementation gate.

## Merge Boundary

The merge branch is `alfworld-benchmark` in
`/home/haodong2/weilin/red_bird/Homemaster-alfworld`, based on
`c0a9dad4b3f85ccb95df8040c75ae3957aa26346`. Runtime configuration and `.runtime/` stay ignored; source, tests and
documentation are the mergeable change set.
