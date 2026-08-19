# Native MindMemOS Search Types Implementation Plan

## Goal

Make the model-visible `mindmemos_search` contract use MindMemOS's native memory types instead of HomeMaster's reduced
`fact|procedure` search vocabulary. A valid native memory must never be reported as corrupt only because HomeMaster did
not project its type.

## Protocol Decision

- Search input accepts `profile`, `fact`, `experience`, `episodic`, `tool_trace`, `skill_candidate`, and
  `file_knowledge`, or omits the filter to search all types.
- The selected type is passed to MindMemOS as the exact `mem_type` filter.
- Active record-free results return exact content and their native stored type.
- Historical structured records keep their full `record`; a stored procedure has top-level native type `experience` and
  retains `record.memory_type=procedure` for structured update compatibility.
- `mindmemos_add` remains `fact|procedure`; this change does not broaden model-authored writes.

## Verification

- Lock the seven-value model tool schema.
- Regress a Finalizer-style `tool_trace` through the search executor and provider-facing result.
- Open the existing LoCoMo Qdrant store and require both known `tool_trace` IDs to return with exact content and no
  `memory_record_corrupt` diagnostic.
- Run focused memory, feedback-context, and application tests plus Ruff and `git diff --check`.
