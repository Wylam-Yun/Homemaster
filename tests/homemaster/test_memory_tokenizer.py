from __future__ import annotations

import json
from pathlib import Path

from homemaster.memory_tokenizer import (
    JiebaMemoryTokenizer,
    build_domain_terms_from_object_memory,
)


def _load_memory(case_name: str) -> dict[str, object]:
    path = Path("data/scenarios") / case_name / "memory.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_domain_terms_are_derived_from_object_memory() -> None:
    terms = build_domain_terms_from_object_memory(_load_memory("fetch_cup_retry"))

    assert "水杯" in terms
    assert "杯子" in terms
    assert "厨房餐桌" in terms
    assert "kitchen_table_viewpoint" in terms
    assert "厨房" in terms


def test_jieba_tokenizer_keeps_domain_terms_stable() -> None:
    memory_payload = _load_memory("check_medicine_success")
    terms = build_domain_terms_from_object_memory(memory_payload)
    tokenizer = JiebaMemoryTokenizer(domain_terms=terms)

    assert "药盒" in tokenizer.tokenize("去桌子那边看看药盒是不是还在")
    assert "客厅边桌" in tokenizer.tokenize("去客厅边桌看看")


def test_jieba_tokenizer_hits_cup_and_kitchen_query_terms() -> None:
    terms = build_domain_terms_from_object_memory(_load_memory("fetch_cup_retry"))
    tokenizer = JiebaMemoryTokenizer(domain_terms=terms)

    tokens = tokenizer.tokenize("去厨房找水杯")

    assert "厨房" in tokens
    assert "水杯" in tokens
