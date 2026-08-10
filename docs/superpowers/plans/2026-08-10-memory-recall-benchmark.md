# HomeMaster 100-Record Memory Recall Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a resumable benchmark that writes 100 synthetic website-operation facts through serial `homemaster -p` subprocesses and separately scores forced retrieval and natural memory routing.

**Architecture:** Put deterministic dataset construction, private artifact storage, stream-event parsing, subprocess execution, checkpointing, and scoring in one focused `homemaster.benchmarking.memory_recall` module. Keep `scripts/memory_recall_benchmark.py` as a thin argparse adapter. The runner treats CLI return codes and machine-readable `tool_completed` receipts as authoritative and stops without retry after an ambiguous mutation.

**Tech Stack:** Python 3.11 standard library, existing HomeMaster CLI, pytest, Ruff, JSON/JSONL artifacts.

---

### Task 1: Deterministic dataset and private run artifacts

**Files:**
- Create: `src/homemaster/benchmarking/memory_recall.py`
- Create: `tests/homemaster/benchmarking/test_memory_recall.py`

- [ ] **Step 1: Write failing dataset and permission tests**

Add tests that call `build_dataset("run-test")` twice and assert identical output, 100 unique subjects, the exact 70/20/10 kind counts, ten websites, snake-case predicate `web_operation_steps`, and `source=user_statement`. Add a test that creates `BenchmarkPaths` under `tmp_path` and asserts directory mode 0700 and dataset/checkpoint mode 0600.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/homemaster/benchmarking/test_memory_recall.py -q
```

Expected: collection fails because `homemaster.benchmarking.memory_recall` does not exist.

- [ ] **Step 3: Implement deterministic models, dataset builder, and atomic private writes**

Define frozen dataclasses `BenchmarkRecord`, `BenchmarkPaths`, and `Checkpoint`. `BenchmarkRecord` must own index, kind, website, subject, predicate, record value, exact query, paraphrase query, and optional distractor target. Construct ten fixed website specifications and derive exactly ten records from each without random LLM generation. Implement `_atomic_write_json(path, value)` using a same-directory temporary file, `os.chmod(..., 0o600)`, `flush`, `os.fsync`, and `os.replace`.

Required public signatures:

```python
def build_dataset(run_id: str) -> tuple[BenchmarkRecord, ...]: ...

@dataclass(frozen=True)
class BenchmarkPaths:
    root: Path
    dataset: Path
    checkpoint: Path
    write_results: Path
    recall_results: Path
    routing_results: Path
    raw: Path

    @classmethod
    def create(cls, base: Path, run_id: str) -> "BenchmarkPaths": ...

def generate_run(*, base: Path, run_id: str) -> BenchmarkPaths: ...
```

- [ ] **Step 4: Run focused tests**

Run the Task 1 pytest command and expect all Task 1 tests to pass.

### Task 2: Authoritative `homemaster -p` write runner and resume

**Files:**
- Modify: `src/homemaster/benchmarking/memory_recall.py`
- Modify: `tests/homemaster/benchmarking/test_memory_recall.py`

- [ ] **Step 1: Write failing parser, command, and checkpoint tests**

Use a fake subprocess runner returning newline-delimited events. Cover: exact command contains `-p` and `--output-format stream-json`; success requires matching `add_memory` `tool_started` and `tool_completed`; final prose alone fails; missing ID fails; record mismatch fails; timeout before start is safe-to-retry; timeout after start is outcome-unknown; confirmed records are skipped on resume; dataset hash mismatch refuses resume; at most one fake runner call is active.

- [ ] **Step 2: Verify focused failures**

Run the focused pytest file and expect failures for undefined `parse_stream_events`, `build_write_prompt`, and `write_run`.

- [ ] **Step 3: Implement stream parsing and serial writes**

Add injectable protocol:

```python
class CommandRunner(Protocol):
    def __call__(
        self, command: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
    ) -> CompletedCommand: ...
```

The real runner uses `subprocess.run(..., capture_output=True, text=True, check=False, timeout=...)`. Build this exact command shape from the repository root:

```python
[
    str(repo_root / ".venv/bin/python"),
    "-m", "homemaster.cli",
    "-p", build_write_prompt(record),
    "--output-format", "stream-json",
]
```

Parse only complete JSON objects from stdout. Decode `tool_completed.output` when it is a JSON string. Accept a write only when exit code, `success`, `status`, `verified_terminal_state`, `backend_attempted`, memory ID, and the complete returned record match the locked dataset row. Append one mode-0600 JSONL result before atomically advancing the checkpoint. Stop on the first non-confirmed item. Never retry inside `write_run`.

- [ ] **Step 4: Run focused tests and static checks**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/homemaster/benchmarking/test_memory_recall.py -q
PYTHONPATH=src .venv/bin/ruff check src/homemaster/benchmarking/memory_recall.py tests/homemaster/benchmarking/test_memory_recall.py
```

Expected: PASS.

### Task 3: Retrieval/routing evaluator and report

**Files:**
- Modify: `src/homemaster/benchmarking/memory_recall.py`
- Modify: `tests/homemaster/benchmarking/test_memory_recall.py`

- [ ] **Step 1: Write failing per-instance scoring tests**

Add fixtures for ranked search records and tool-call sequences. Assert independent Recall@1, Recall@5, reciprocal rank, exact ordered steps, duplicate subject, distractor outrank, memory-tool routing, incorrect observe/robot/browser routing, and final-answer fidelity fields. Assert an aggregate cannot pass when any individual expected ID is missing. Assert natural-routing cases are exactly three deterministic target samples per website.

- [ ] **Step 2: Verify focused failures**

Run the focused pytest file and expect failures for undefined evaluation/scoring functions.

- [ ] **Step 3: Implement four suites and orthogonal scoring**

Implement exact forced queries for all 100 records, paraphrased forced queries for 70 targets, 20 distractor contrast queries, and 30 natural-routing queries. Each query launches a fresh serial `homemaster -p --output-format stream-json` subprocess. Forced scoring reads `search_memories` tool output; natural scoring separately records tool choice and final-answer fidelity. Persist every query result even after failures.

Required aggregate fields:

```python
{
    "write_success_rate": ...,
    "recall_at_1": ...,
    "recall_at_5": ...,
    "mean_reciprocal_rank": ...,
    "exact_step_order_accuracy": ...,
    "distractor_confusion_rate": ...,
    "duplicate_record_rate": ...,
    "natural_memory_routing_rate": ...,
    "natural_final_answer_accuracy": ...,
    "incorrect_environment_routing_rate": ...,
    "write_latency_p50_seconds": ...,
    "write_latency_p95_seconds": ...,
    "search_latency_p50_seconds": ...,
    "search_latency_p95_seconds": ...,
}
```

Render `summary.json` and `report.md` with every failed or ambiguous instance listed by subject and query. Do not infer retrieval failure when a natural case never calls memory; classify it as routing failure.

- [ ] **Step 4: Run focused tests and static checks**

Run Task 2's pytest and Ruff commands and expect PASS.

### Task 4: CLI adapter, documentation, and external smoke gate

**Files:**
- Create: `scripts/memory_recall_benchmark.py`
- Modify: `README.md`
- Modify: `docs/memory-user-guide.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/homemaster/benchmarking/test_memory_recall.py`
- Add: `docs/superpowers/plans/2026-08-10-memory-recall-benchmark.md`

- [ ] **Step 1: Write failing CLI-dispatch tests**

Test `main(["generate", "--run-id", "run-test", "--base", str(tmp_path)])`, `status`, `write`, `resume`, and `evaluate` through injected module functions. Assert there is no cleanup/delete subcommand and that write/evaluate require an existing run.

- [ ] **Step 2: Implement the thin argparse script**

Expose `generate`, `write`, `resume`, `evaluate`, and `status`. Default base is `~/.homemaster/benchmarks`, repository root is derived from the script path, timeout defaults to 600 seconds, and each command prints one JSON summary suitable for shell automation. Do not load or serialize provider configuration.

- [ ] **Step 3: Update user-facing documentation and changelog**

Document exact commands, expected five-to-six-hour write duration, token/cost implications, retained records, resume semantics, and the distinction between retrieval failures and natural routing failures. Add a CHANGELOG entry with the same substance as the eventual implementation commit message.

- [ ] **Step 4: Run local verification**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest tests/homemaster/benchmarking/test_memory_recall.py -q
PYTHONPATH=src .venv/bin/ruff check scripts/memory_recall_benchmark.py src/homemaster/benchmarking/memory_recall.py tests/homemaster/benchmarking/test_memory_recall.py
PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py generate --run-id hm100-smoke
PYTHONPATH=src .venv/bin/python scripts/memory_recall_benchmark.py status --run-id hm100-smoke
```

Expected: tests and Ruff PASS; generated dataset reports 100 records with 70/20/10 counts.

- [ ] **Step 5: Run one live external smoke write**

Generate a unique smoke run, execute `write --max-records 1`, and independently run a forced retrieval for that exact subject. Require subprocess return code zero, confirmed `add_memory` terminal receipt, exact returned record, expected ID in search results, and managed Neo4j stopped after the one-shot process exits. The smoke record remains by user request.

- [ ] **Step 6: Run the full 100-record write when smoke passes**

Start `write --run-id <production-run-id>` in a persistent terminal session. Monitor checkpoint growth without treating unchanged progress as failure. Stop on the first ambiguous mutation; otherwise continue until 100 confirmed records exist. Do not begin evaluation until the write checkpoint reaches 100.

- [ ] **Step 7: Commit**

Stage only the benchmark module, script, focused tests, plan/spec, README, memory guide, and CHANGELOG. Commit with a message whose body matches the CHANGELOG entry and explicitly states that records are retained and mutations are serialized through public `homemaster -p` calls.
