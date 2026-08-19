from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from homemaster.memory.enrichment_queue import MemoryEnrichmentQueue


@pytest.mark.asyncio
async def test_memory_enrichment_queue_caps_concurrency_at_two(tmp_path: Path) -> None:
    active = 0
    maximum = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    class Store:
        async def enrich_flat_memory(self, *, memory_id, content, context):
            nonlocal active, maximum
            del content, context
            active += 1
            maximum = max(maximum, active)
            if maximum == 2:
                entered.set()
            await release.wait()
            active -= 1
            return {"memory_id": memory_id, "entity_ids": []}

    queue = MemoryEnrichmentQueue(
        Store(),
        audit_path=tmp_path / "enrichment.jsonl",
        concurrency=2,
    )
    await queue.start()
    context = SimpleNamespace(
        account_id="account",
        project_id="project",
        session_id="session",
        request_id="request",
    )
    for index in range(4):
        queue.enqueue(
            memory_id=f"memory-{index}",
            content=f"content-{index}",
            context=context,
        )
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert active == 2
    assert maximum == 2
    release.set()
    await queue.aclose()
    assert active == 0
    assert maximum == 2
