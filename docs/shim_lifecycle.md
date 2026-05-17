# Shim Lifecycle & Root-Level File Inventory

This document tracks every root-level `.py` file in `src/homemaster/`, its purpose, and its migration plan. It is the authoritative reference for Phase 7 acceptance criterion: "root-level implementation modules have migration target or explicit public-facade justification."

## Shim Lifecycle Table

These files are backward-compatibility re-exports. They exist so that legacy import paths (`from homemaster.frontdoor import ...`) continue to work during the transition.

| Shim path | New import path | Compat window | Target removal | Type |
|-----------|----------------|---------------|----------------|------|
| `homemaster.executor` | `homemaster.stages.executor` | Phase 1-7 | Phase 8 (when stages/executor.py migrated) | selective re-export |
| `homemaster.stage_runtime` | `homemaster.pipeline.stage_runtime` | Phase 1-7 | Phase 8 | selective re-export |
| `homemaster.skill_registry` | `homemaster.skills.registry` | Phase 1-7 | Phase 8 (when stages/executor.py migrated) | legacy impl (355 lines, not a pure re-export) |
| `homemaster.frontdoor` | `homemaster.stages.task_understanding` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.orchestrator` | `homemaster.stages.orchestrator` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.pipeline_core` | `homemaster.pipeline.core` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.pipeline_stages` | `homemaster.pipeline.adapters` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.doctor` | `homemaster.cli.doctor` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.interactive_shell` | `homemaster.cli.interactive_shell` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.stage_04` | `homemaster.stages.grounding_runner` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.stage_05` | `homemaster.stages.orchestration_runner` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.stage_06` | `homemaster.stages.summary_runner` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.skill_selector` | `homemaster.stages.skill_selector` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.summary` | `homemaster.stages.summary` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.verifier` | `homemaster.stages.verifier` | Phase 1-7 | Phase 9 | star-import |
| `homemaster.recovery` | `homemaster.stages.recovery` | Phase 1-7 | Phase 9 | star-import |

## Root-Level Implementation Files

These are NOT shims — they contain actual implementation code. Each file is categorized by why it lives at root level and where it should eventually move.

### Public Facade (shared contracts/config used by multiple subpackages)

| File | Purpose | Migration target |
|------|---------|-----------------|
| `contracts.py` | Pydantic models: TaskCard, ExecutionState, OrchestrationPlan, StepDecision, Subtask | public facade — stays at root |
| `runtime.py` | Runtime config helpers, path constants, provider config | public facade — stays at root |
| `logger.py` | Minimal logging setup | public facade — stays at root |
| `trace.py` | JSONL trace events, write_json, sanitize_for_log | public facade — stays at root |
| `prompt_loader.py` | .txt template loader (stdlib string.Template) | public facade — stays at root |
| `token_budget.py` | Central max_tokens policy for all LLM call kinds | public facade — stays at root |

### Stage-Specific (used by pipeline compat path; will move when stages/ are removed)

| File | Purpose | Migration target |
|------|---------|-----------------|
| `execution_state.py` | Stage 05 runtime state bookkeeping | `stages/` when executor migrated |
| `fact_memory.py` | Append-only JSONL fact/event memory persistence | `memory/` |
| `failure_log.py` | Stage 05 failure record construction | `stages/` when executor migrated |
| `failure_rule_provider.py` | Declarative failure-rule loader from failures.json | `stages/` or `tools/` |
| `grounding.py` | Stage 04 grounding reliability checks for memory RAG hits | `stages/grounding_runner.py` |
| `memory_commit.py` | Stage 06 evidence bundling and memory commit planning | `stages/summary_runner.py` |
| `memory_index.py` | BM25 index + embedding cache for Stage 03 RAG | `memory/` |
| `memory_profile.py` | Corpus + profile materializer for object_memory payload | `memory/` |
| `memory_rag.py` | Stage 03 object_memory-only RAG retrieval | `memory/` |
| `memory_tokenizer.py` | Jieba-based tokenization for memory retrieval | `memory/` |
| `orchestration_validator.py` | Validators for Stage 05 orchestration LLM output | `stages/` when executor migrated |
| `planning_context.py` | Stage 04 PlanningContext assembly | `stages/grounding_runner.py` |
| `recovery_config.py` | max_attempts config from homemaster.json | `stages/` when executor migrated |
| `runtime_memory_store.py` | Runtime object memory overlay persistence | `memory/` |
| `scenario_catalog.py` | Scenario catalog + manifest loader | `scenarios/` (new package) |
| `scenario_runner.py` | Stage 07 scenario matrix runner | `scenarios/` (new package) |
| `scenario_validator.py` | Dev/CI validator for scenario data integrity | `scenarios/` (new package) |
| `task_record.py` | Task record + commit-log JSONL persistence | `memory/` or `events/` |
| `task_runner.py` | Stage 07 single-task runner (wires Stages 02-06) | `scenarios/` or stays as public facade |
| `world_overlay.py` | Applies world_overlay.json to a global HomeWorld dict | `scenarios/` (new package) |

### Provider (candidate for providers/ package)

| File | Purpose | Migration target |
|------|---------|-----------------|
| `embedding_client.py` | BGE-M3 embedding HTTP client | `providers/` |
| `llm_client.py` | Raw JSON LLM client (Mimo) for smoke tests | `providers/` |

## Production Static Gate Allowlist

The production static gate `rg` command checks for test-double symbols in production code. The following hits are **intentional** — they are deprecation-handling code that references deprecated config key names to warn users away, not actual test-double usage:

| File | Line(s) | Pattern matched | Reason |
|------|---------|----------------|--------|
| `runtime.py` | 118-125 | `live_models`, `mock_skills` | Deprecation warning: rejects old config keys with error message |
| `config/runtime_settings.py` | 56-80 | `live_models`, `mock_skills` | Deprecation handling: `_DEPRECATED_KEYS` set and error messages |
| `pipeline/stage_runtime.py` | 93-102 | `live_models`, `mock_skills` | Deprecation: `from_flags()` raises error if old flags used |

No other production code hits are expected. If the gate returns new hits, they must be fixed or added to this allowlist with justification.
