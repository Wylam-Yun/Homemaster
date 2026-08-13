# Experience Message Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace raw JSON `TextMessage` submission with selected, role-aware `DialogueMessage` input while retaining an in-memory `TaskTraceEnvelope`.

**Architecture:** The finalizer collects session events into an in-memory envelope, a pure renderer converts the envelope to dialogue messages, and the finalizer hashes and submits those messages. Runtime trace remains the only persisted full trace; the job store persists only status and operations.

**Tech Stack:** Python 3.11, dataclasses, MindMemOS `DialogueMessage`, pytest.

---

### Task 1: Define renderer behavior with tests

**Files:**
- Modify: `tests/homemaster/experience/test_finalizer.py`

- [ ] Add a trace fixture containing user, thinking, tool start/completion/failure, assistant reply, transport, usage, duplicate final reply, and sentinel IDs.
- [ ] Assert MindMemOS receives ordered `DialogueMessage` roles and semantic content only.
- [ ] Assert sentinel IDs and transport/usage fields are absent, thinking and failures remain, and no `task_trace.json` is created.
- [ ] Run the focused tests and confirm they fail against raw `TextMessage` behavior.

### Task 2: Render and submit selected dialogue

**Files:**
- Modify: `src/homemaster/experience/finalizer.py`
- Modify: `src/homemaster/experience/__init__.py`

- [ ] Add an in-memory `TaskTraceEnvelope` dataclass.
- [ ] Add a pure renderer for the approved event mapping.
- [ ] Hash the stable rendered messages, submit `DialogueMessage` objects, and store `input_hash` under extractor version `experience-v2`.
- [ ] Remove `task_trace.json` writes and envelope paths from results.
- [ ] Run focused tests and confirm they pass.

### Task 3: Update shell output and documentation

**Files:**
- Modify: `src/homemaster/cli/interactive_shell.py`
- Modify: `tests/homemaster/test_cli_interactive.py`
- Modify: `docs/architecture/memory-system.md`
- Modify: `docs/memory-user-guide.md`
- Modify: `plan/V2.4/mindmem_new_plan.md`

- [ ] Replace the Debug envelope path with rendered message count.
- [ ] Describe in-memory envelope and selected dialogue submission; remove claims that `task_trace.json` is saved or submitted.
- [ ] Run experience, CLI, memory, and documentation-adjacent test suites plus `git diff --check`.
