# LoCoMo Full-Pipeline Pilot Plan

## Goal

Replay 50-100 original LoCoMo dialogue turns through the real HomeMaster application and verify that session learning, implicit feedback, dreaming, automatic recall, and model-visible memory tools actually run. The pilot produces inspection artifacts, not benchmark quality scores.

## Fixed Decisions

- Add a sibling `benchmark-locomo` CLI command. Do not reuse the ALFWorld environment adapter.
- Use one long-lived `HomeApplication` with `tool_environment=None`; keep HomeMaster memory tools and omit browser, terminal, and robot environment tools.
- Treat one original LoCoMo session as one HomeMaster run containing the dated transcript. Preserve original speaker names and image captions in the prompt.
- End the HomeMaster session after each original LoCoMo session and enqueue the production `SessionFinalizer` through `MemoryAddQueue`.
- Replay only one sample and one focal user in the pilot. Use the focal speaker as the memory tenant for this first isolated run.
- Override the finalizer trace timestamps with the original LoCoMo session timestamp through a benchmark-owned trace sink. Do not change runtime clock behavior globally.
- Stop after the requested number of source dialogue turns. A partially included final source session is allowed and explicitly reported.
- Do not label corrections or score action quality. Persist every relevant event and finalization operation for manual review.
- `mindmemos_search` uses the complete native MindMemOS memory-type vocabulary:
  `profile`, `fact`, `experience`, `episodic`, `tool_trace`, `skill_candidate`, and
  `file_knowledge`. Search filters pass the selected native value through unchanged, and
  record-free active results return that same native value. Do not maintain a second
  HomeMaster-only `fact|procedure` search ontology. Historical structured procedure
  records retain their complete `record` payload while the top-level search type is the
  native stored `experience`.

## Data Flow

1. Load `locomo10.json` and select one `sample_id` and focal speaker.
2. Read source sessions chronologically until `max_source_turns` is reached.
3. Render each selected session as a dated, named-speaker transcript.
4. Submit it with `ApplicationRuntime.run(RunRequest(...))` using the ordinary `home` profile and memory permissions.
5. Persist a benchmark trace whose message events carry the source session timestamp.
6. Enqueue `SessionFinalizer.finalize()` on the application memory FIFO and wait for terminal completion.
7. Fail the run if application execution, finalization, implicit feedback, dreaming, or application shutdown fails.
8. Write `summary.json`, `sessions.jsonl`, and `memory_events.jsonl` under the run directory.

## Required Report Fields

- selected sample, focal speaker, source sessions, and source turn count;
- per-session runtime status, finalization status, Vanilla Add operations, and duration;
- counts and full payloads for automatic recall, explicit/implicit feedback, dreaming, and every `mindmemos_*` tool call;
- source timestamp used for every replayed session;
- artifact paths and terminal failure details.

## Verification

- Unit: loading/truncation is chronological and exact; timestamps reach finalizer messages; report event filtering is exact.
- Integration: fake application proves one RunRequest and one finalization per selected source session.
- Real pilot gate: start the configured chat/embedding/Qdrant/Neo4j stack, replay a small sample, require successful return statuses, and read the persisted finalization jobs and raw active memories. Only then expand to 50-100 source turns.

## Non-Goals

- Official LoCoMo QA/F1 scoring;
- correction/dreaming action quality labels;
- browser, shell, robot, or other environment tools;
- multi-sample parallelism or general benchmark framework refactoring;
- production-wide user identity migration.
