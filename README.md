# Memory-Grounded Embodied Task Brain

HomeMaster task brain CLI MVP.

This project demonstrates a memory-grounded embodied task loop with:

- rule-first instruction parsing
- structured memory retrieval
- deterministic high-level planning
- evidence-based verification
- failure-aware recovery
- auditable CLI traces

## Install

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install ".[dev]"
```

Use a non-editable install for Stage 0. In this local uv Python environment,
editable installs generate a hidden `__editable__` `.pth` file that Python
skips, so the package may not import outside pytest.

## Verify

```bash
python -I -c "import task_brain; print(task_brain.__version__)"
pytest -q
ruff check .
```

## Phase A Demo

Core scenarios:

```bash
task-brain run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
task-brain run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

Hardening scenarios (expected to fail but remain explainable):

```bash
task-brain run --scenario object_not_found --instruction "去厨房找水杯，然后拿给我"
task-brain run --scenario distractor_rejected --instruction "去厨房找水杯，然后拿给我"
```

If the console script is unavailable in your shell, use:

```bash
PYTHONPATH=src .venv/bin/python -m task_brain.cli run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
```

## Phase A Quality Gate

Run the gate commands:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/pytest tests/test_phase_a_quality_gate.py -q
```

Trace checkpoints:

- `retrieve_memory` before `generate_plan`
- `validate_plan` before `execute_subgoal_loop`
- `final_task_verification` before `respond_with_trace`
- stale recovery includes `write_task_negative_evidence` and `recovery_switch_candidate`
- cup retry includes `post_action_verification_failed` and `recovery_retry_same_subgoal`

## Phase Boundary

Phase A is the deterministic, mock-driven, CLI-first quality baseline.
Phase B extensions (simulator readiness, richer memory retrieval, and LLM planner)
must not regress the Phase A quality gate above.
