"""Append-only fact/event memory persistence for Stage 06."""

from __future__ import annotations

import json
from pathlib import Path

from homemaster.contracts import FactMemoryWrite
from homemaster.trace import sanitize_for_log


def append_fact_memory_writes(
    path: Path,
    writes: list[FactMemoryWrite],
) -> int:
    """Append structured fact/event memory records to JSONL."""

    if not writes:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for write in writes:
            handle.write(
                json.dumps(
                    sanitize_for_log(write.model_dump(mode="json")),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return len(writes)
