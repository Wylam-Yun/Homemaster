# Portable Memory Runtime Implementation Plan

## Goal

Make one HomeMaster checkout movable between servers without editing machine-specific absolute paths before every run.
Each server performs one explicit setup, after which `scripts/homemaster` owns the config, Python and memory runtime
selection.

## Invariants

- Tracked configuration and documentation contain no live server path or credential.
- The ignored `config/homemaster.yaml` always stores the same relative runtime paths:
  `../.runtime/memory`, `../.runtime/neo4j`, and `../.runtime/java`.
- `.runtime/venv`, optional `.runtime/alfworld-venv`, `.runtime/neo4j`, and `.runtime/java` are server-local bindings
  created once by setup. ALFWorld may keep its THOR/Torch environment separate from the HomeMaster dependency venv.
- Existing memory data is never copied, deleted or replaced implicitly. `--memory-home` binds it explicitly.
- Setup is idempotent for the same targets and fails closed for conflicting existing paths.
- The launcher works from any cwd, fixes `HOMEMASTER_CONFIG_PATH` and `PYTHONPATH`, and refuses an incomplete runtime.
- A successful CLI import is separate from memory readiness; deployment verification checks both.
- Persistent migration protocols are versioned. Mount aliases resolving to the same target are equivalent, while
  legacy manifests are validated under their original schema and preserved before an in-place schema upgrade.

## Implementation

1. Add `scripts/setup_memory_runtime.py` with structured YAML updates, installation validation, idempotent local
   bindings, a read-only `check` command and machine-readable output.
2. Add `scripts/homemaster` as the only recommended source-checkout launcher.
3. Add tests for first setup, existing-memory binding, idempotency, conflict rejection, config anchoring and launcher
   behavior from an unrelated cwd.
4. Update the example config, README, memory/ALFWorld guides, architecture, changelog, pitfalls and session handoff.
5. Initialize the formal HomeMaster worktree independently on HPC2 and hkust4. Do not move or delete existing memory,
   Java or Neo4j installations.
6. Verify CLI import, managed Neo4j readiness, exact Qdrant/Neo4j memory readback, then one LoCoMo and one visual
   ALFWorld instance with independent stderr and cleanup gates.
7. Upgrade historical v1 migration state to v2 under the memory lock, preserving the exact v1 manifest/journal as
   audit files and rejecting unknown legacy shapes or genuinely different roots.

## Rollback

The setup changes only ignored `config/homemaster.yaml` and ignored `.runtime` bindings. Restore the private YAML
backup and remove only bindings created by the operator if rollback is required; external installations and existing
memory roots remain untouched.
