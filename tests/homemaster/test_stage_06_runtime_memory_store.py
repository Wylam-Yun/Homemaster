from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemaster.contracts import (
    EvidenceRef,
    MemoryCommitPlan,
    MemoryRetrievalQuery,
    ObjectMemoryUpdate,
    TaskCard,
)
from homemaster.memory_rag import run_memory_rag
from homemaster.runtime import ProviderConfig
from homemaster.runtime_memory_store import RuntimeMemoryStore


@dataclass
class StaticQueryProvider:
    query: MemoryRetrievalQuery

    def generate_query(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> tuple[MemoryRetrievalQuery, str, dict[str, Any]]:
        return self.query, self.query.model_dump_json(), {"provider_name": "Mimo"}


@dataclass
class DeterministicEmbeddingProvider:
    provider_name: str = "MemoryEmbedding"
    model: str = "BAAI/bge-m3"

    def public_summary(self) -> dict[str, Any]:
        return {"provider_name": self.provider_name, "model": self.model}

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                1.0 if any(term in text for term in ("水杯", "杯子", "cup")) else 0.0,
                1.0 if "厨房" in text or "kitchen" in text else 0.0,
                1.0 if any(term in text for term in ("药盒", "medicine")) else 0.0,
            ]
            for text in texts
        ]


def _evidence_ref() -> EvidenceRef:
    return EvidenceRef(
        evidence_id="verification:task-1:1",
        evidence_type="verification_result",
        source_id="verify-1",
        created_at="2026-04-26T00:00:00Z",
        summary="观察到水杯",
    )


def test_runtime_memory_store_writes_overlay_readable_by_stage_03(tmp_path: Path) -> None:
    base_memory = Path("data/scenarios/fetch_cup_retry/memory.json")
    store = RuntimeMemoryStore(tmp_path / "memory")
    commit = MemoryCommitPlan(
        commit_id="commit-task-1",
        object_memory_updates=[
            ObjectMemoryUpdate(
                memory_id="mem-cup-1",
                update_type="confirm",
                updated_fields={
                    "last_confirmed_at": "2026-04-26T00:01:00Z",
                    "confidence_level": "high",
                    "belief_state": "confirmed",
                },
                evidence_refs=[_evidence_ref()],
                reason="confirmed in task",
            )
        ],
        index_stale_memory_ids=["mem-cup-1"],
    )

    runtime_path = store.apply_commit_plan(base_memory_path=base_memory, plan=commit)
    payload = json.loads(runtime_path.read_text(encoding="utf-8"))
    cup_memory = next(item for item in payload["object_memory"] if item["memory_id"] == "mem-cup-1")

    assert cup_memory["last_confirmed_at"] == "2026-04-26T00:01:00Z"
    result = run_memory_rag(
        task_card=TaskCard(
            task_type="fetch_object",
            target="水杯",
            delivery_target="user",
            location_hint="厨房",
            success_criteria=["找到水杯"],
            needs_clarification=False,
            confidence=0.9,
        ),
        memory_path=runtime_path,
        llm_provider=ProviderConfig(
            name="Mimo",
            base_url="https://mimo.example",
            model="mimo-v2-pro",
            api_keys=("secret",),
            protocol="anthropic",
        ),
        query_provider=StaticQueryProvider(
            MemoryRetrievalQuery(
                query_text="水杯 厨房",
                target_aliases=["水杯", "杯子"],
                location_terms=["厨房"],
            )
        ),
        embedding_provider=DeterministicEmbeddingProvider(),
        cache_dir=tmp_path / "cache",
        case_name="runtime_memory_store_stage03",
        case_root=tmp_path / "cases",
        results_dir=tmp_path / "results",
    )
    assert result.memory_result.hits[0].memory_id == "mem-cup-1"


def test_runtime_memory_directory_is_gitignored() -> None:
    completed = subprocess.run(
        ["git", "check-ignore", "-q", "var/homemaster/memory/fact_memory.jsonl"],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
    )

    assert completed.returncode == 0
