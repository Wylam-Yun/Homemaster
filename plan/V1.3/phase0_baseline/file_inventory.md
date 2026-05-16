# Phase 0: File Inventory

## src/homemaster/ root (45 Python files)

```
__init__.py
contracts.py
doctor.py              # shim -> cli.doctor
embedding_client.py
execution_state.py
executor.py            # shim -> stages.executor
fact_memory.py
failure_log.py
failure_rule_provider.py
frontdoor.py           # shim -> stages.task_understanding
grounding.py
interactive_shell.py   # shim -> cli.interactive_shell
llm_client.py
logger.py
memory_commit.py
memory_index.py
memory_profile.py
memory_rag.py
memory_tokenizer.py
orchestration_validator.py
orchestrator.py        # shim -> stages.orchestrator
pipeline_core.py       # shim -> pipeline.core
pipeline_stages.py     # shim -> pipeline.adapters
planning_context.py
prompt_loader.py
recovery_config.py
recovery.py            # shim -> stages.recovery
runtime_memory_store.py
runtime.py
scenario_catalog.py
scenario_runner.py
scenario_validator.py
skill_registry.py
skill_selector.py      # shim -> stages.skill_selector
stage_04.py            # shim -> stages.grounding_runner
stage_05.py            # shim -> stages.orchestration_runner
stage_06.py            # shim -> stages.summary_runner
stage_runtime.py       # shim -> pipeline.stage_runtime
summary.py             # shim -> stages.summary
task_record.py
task_runner.py
token_budget.py
trace.py
verifier.py            # shim -> stages.verifier
world_overlay.py
```

## src/homemaster/pipeline/ (5 files)

```
__init__.py
adapters.py
core.py
stage_01_smoke.py
stage_runtime.py
```

## src/homemaster/stages/ (12 files)

```
__init__.py
executor.py
grounding_runner.py
orchestration_runner.py
orchestrator.py
recovery_loop.py
recovery.py
skill_selector.py
summary_runner.py
summary.py
task_understanding.py
verifier.py
```

## src/homemaster/cli/ (5 files)

```
__init__.py
__main__.py
app.py
doctor.py
interactive_shell.py
```

## src/homemaster/prompts/ (15 files)

```
__init__.py
stage_01_retry.txt
stage_01_task_card_prompt.txt
stage_02_retry.txt
stage_02_task_understanding_prompt.txt
stage_03_memory_query_prompt.txt
stage_03_retry.txt
stage_05_orchestration_prompt.txt
stage_05_orchestration_retry.txt
stage_05_recovery_prompt.txt
stage_05_recovery_retry.txt
stage_05_step_decision_prompt.txt
stage_05_step_decision_retry.txt
stage_06_summary_prompt.txt
stage_06_summary_retry.txt
```

## Target subpackages (not yet created)

```
agent/          # AgentRuntime contracts and future loop
tools/          # ToolSpec / ToolResult / registry contracts
skills/         # SkillSpec / SkillLoader / SkillRegistry contracts
config/         # run-scoped RuntimeSettings and runtime path helpers
events/         # RuntimeEvent contracts
providers/      # LLM/embedding/Mimo decision provider clients
```

## tests/homemaster/test_doubles/

Not yet created. Will be needed in Phase 1 for relocated deterministic providers.
