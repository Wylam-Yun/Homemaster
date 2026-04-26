"""Task record and commit-log persistence for Stage 06."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homemaster.contracts import MemoryCommitPlan, TaskRecord
from homemaster.trace import sanitize_for_log


def append_task_record(path: Path, record: TaskRecord | None) -> int:
    if record is None:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                sanitize_for_log(record.model_dump(mode="json")),
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    return 1


def append_commit_log(
    path: Path,
    *,
    plan: MemoryCommitPlan,
    task_id: str,
    object_memory_path: str | None = None,
) -> dict[str, Any]:
    record = {
        "commit_id": plan.commit_id,
        "task_id": task_id,
        "object_memory_update_count": len(plan.object_memory_updates),
        "fact_memory_write_count": len(plan.fact_memory_writes),
        "task_record_written": plan.task_record is not None,
        "skipped_candidates": plan.skipped_candidates,
        "index_stale_memory_ids": plan.index_stale_memory_ids,
        "object_memory_path": object_memory_path,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(sanitize_for_log(record), ensure_ascii=False, sort_keys=True) + "\n"
        )
    return record
