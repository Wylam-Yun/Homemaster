# Phase 0: Compatibility Shim Inventory

15 root-level files use `from homemaster.xxx import *` to re-export symbols from subpackages.

| Root Shim File | Re-exports From |
|---|---|
| `doctor.py` | `homemaster.cli.doctor` |
| `executor.py` | `homemaster.stages.executor` |
| `frontdoor.py` | `homemaster.stages.task_understanding` |
| `interactive_shell.py` | `homemaster.cli.interactive_shell` |
| `orchestrator.py` | `homemaster.stages.orchestrator` |
| `pipeline_core.py` | `homemaster.pipeline.core` |
| `pipeline_stages.py` | `homemaster.pipeline.adapters` |
| `recovery.py` | `homemaster.stages.recovery` |
| `skill_selector.py` | `homemaster.stages.skill_selector` |
| `stage_04.py` | `homemaster.stages.grounding_runner` |
| `stage_05.py` | `homemaster.stages.orchestration_runner` |
| `stage_06.py` | `homemaster.stages.summary_runner` |
| `stage_runtime.py` | `homemaster.pipeline.stage_runtime` |
| `summary.py` | `homemaster.stages.summary` |
| `verifier.py` | `homemaster.stages.verifier` |

## Key Risk

`stage_runtime.py` shim re-exports everything from `pipeline.stage_runtime`, including:
- `StaticMemoryQueryProvider` (test_double)
- `KeywordEmbeddingProvider` (test_double)
- `StaticScenarioDecisionProvider` (test_double)
- `deterministic_task_card()`, `deterministic_query()`, `deterministic_plan()`
- `dummy_provider()`

`executor.py` shim re-exports from `stages.executor`, including `StaticStepDecisionProvider`.

These shims allow old imports like `from homemaster.executor import StaticStepDecisionProvider` to work.
