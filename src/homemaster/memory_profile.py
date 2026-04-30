"""Memory profile materializer: corpus + profile -> object_memory payload."""

from __future__ import annotations

from typing import Any

from homemaster.contracts import MemoryProfile


def materialize_memory(
    corpus: dict[str, Any],
    profile: MemoryProfile,
) -> dict[str, Any]:
    """Apply memory profile rules to produce an object_memory payload.

    Priority:
    1. full_corpus=True -> take all corpus entries; otherwise take include_memory_ids
    2. exclude_memory_ids -> remove matching entries
    3. Returns {"object_memory": [...]} for Stage03 consumption

    Raises ValueError if include_memory_ids or exclude_memory_ids reference
    IDs not in corpus.
    """
    corpus_entries = corpus.get("object_memory", [])
    corpus_by_id = {
        e["memory_id"]: e
        for e in corpus_entries
        if isinstance(e, dict) and "memory_id" in e
    }

    if profile.full_corpus:
        selected = list(corpus_entries)
    else:
        unknown_include = [
            mid for mid in profile.include_memory_ids if mid not in corpus_by_id
        ]
        if unknown_include:
            raise ValueError(
                f"Unknown memory_ids in include_memory_ids: {unknown_include}"
            )
        selected = [corpus_by_id[mid] for mid in profile.include_memory_ids]

    if profile.exclude_memory_ids:
        unknown_exclude = [
            mid
            for mid in profile.exclude_memory_ids
            if mid not in corpus_by_id
        ]
        if unknown_exclude:
            raise ValueError(
                f"Unknown memory_ids in exclude_memory_ids: {unknown_exclude}"
            )
        exclude_set = set(profile.exclude_memory_ids)
        selected = [
            e for e in selected if e.get("memory_id") not in exclude_set
        ]

    return {"object_memory": selected}
