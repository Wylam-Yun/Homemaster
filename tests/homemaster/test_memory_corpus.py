"""Validate the unified memory corpus against HomeWorld."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from homemaster.memory_index import MemoryBM25Index, build_memory_documents
from homemaster.memory_tokenizer import (
    JiebaMemoryTokenizer,
    build_domain_terms_from_object_memory,
)
from homemaster.runtime import REPO_ROOT

CORPUS_PATH = REPO_ROOT / "data" / "memory" / "elder_home_v1" / "object_memory_corpus.json"
WORLD_PATH = REPO_ROOT / "data" / "homes" / "elder_home_v1" / "world.json"

REQUIRED_TARGET_CATEGORIES = {"cup", "medicine_box", "remote_control", "glasses", "keys", "tissue"}
REQUIRED_DISTRACTOR_CATEGORIES = {
    "mug", "water_bottle", "bowl", "plate", "book", "phone", "umbrella", "bag",
}


@pytest.fixture(scope="module")
def corpus() -> dict:
    assert CORPUS_PATH.is_file(), f"Corpus not found: {CORPUS_PATH}"
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def world() -> dict:
    assert WORLD_PATH.is_file(), f"HomeWorld not found: {WORLD_PATH}"
    return json.loads(WORLD_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entries(corpus) -> list[dict]:
    return corpus["object_memory"]


@pytest.fixture(scope="module")
def world_room_ids(world) -> set[str]:
    return {r["room_id"] for r in world.get("rooms", [])}


@pytest.fixture(scope="module")
def world_anchor_map(world) -> dict[str, dict]:
    return {a["anchor_id"]: a for a in world.get("furniture", [])}


@pytest.fixture(scope="module")
def world_viewpoint_ids(world) -> set[str]:
    return set(world.get("viewpoints", {}).keys())


@pytest.fixture(scope="module")
def world_object_categories(world) -> set[str]:
    return {o["category"] for o in world.get("objects", [])}


# --- Basic structure ---


def test_corpus_loads_without_error(corpus: dict):
    assert isinstance(corpus.get("object_memory"), list)
    assert len(corpus["object_memory"]) > 0


def test_corpus_has_exact_entry_count(entries: list[dict]):
    assert len(entries) == 35, f"Expected 35 entries, got {len(entries)}"


def test_corpus_memory_ids_unique(entries: list[dict]):
    ids = [e["memory_id"] for e in entries]
    dupes = [mid for mid in ids if ids.count(mid) > 1]
    assert not dupes, f"Duplicate memory_ids: {set(dupes)}"


# --- Reference integrity ---


def test_corpus_refs_resolve_in_homeworld(
    entries: list[dict],
    world_anchor_map: dict[str, dict],
    world_room_ids: set[str],
    world_viewpoint_ids: set[str],
):
    for entry in entries:
        anchor = entry["anchor"]
        anchor_id = anchor["anchor_id"]
        assert anchor_id in world_anchor_map, (
            f"{entry['memory_id']}: unknown anchor_id {anchor_id!r}"
        )
        hw = world_anchor_map[anchor_id]
        assert anchor["room_id"] == hw["room_id"], (
            f"{entry['memory_id']}: room_id {anchor['room_id']!r} != HomeWorld {hw['room_id']!r}"
        )
        assert anchor["anchor_type"] == hw["anchor_type"], (
            f"{entry['memory_id']}: anchor_type {anchor['anchor_type']!r} != HomeWorld {hw['anchor_type']!r}"
        )
        assert anchor["viewpoint_id"] == hw["viewpoint_id"], (
            f"{entry['memory_id']}: viewpoint_id {anchor['viewpoint_id']!r} != HomeWorld {hw['viewpoint_id']!r}"
        )
        assert anchor["room_id"] in world_room_ids
        assert anchor["viewpoint_id"] in world_viewpoint_ids


# --- Category coverage ---


def test_corpus_has_required_target_categories(entries: list[dict]):
    present = {e["object_category"] for e in entries}
    missing = REQUIRED_TARGET_CATEGORIES - present
    assert not missing, f"Missing required target categories: {missing}"


def test_corpus_has_required_distractor_categories(entries: list[dict]):
    present = {e["object_category"] for e in entries}
    missing = REQUIRED_DISTRACTOR_CATEGORIES - present
    assert not missing, f"Missing required distractor categories: {missing}"


def test_corpus_categories_exist_in_homeworld_objects(
    entries: list[dict], world_object_categories: set[str]
):
    corpus_categories = {e["object_category"] for e in entries}
    unknown = corpus_categories - world_object_categories
    assert not unknown, f"Corpus categories not in HomeWorld objects: {unknown}"


# --- Stale / conflict coverage ---


def test_corpus_has_min_stale(entries: list[dict]):
    stale = [e for e in entries if e.get("belief_state") == "stale"]
    assert len(stale) >= 6, f"Expected >= 6 stale entries, got {len(stale)}"


def test_corpus_has_multi_candidate_conflicts(entries: list[dict]):
    from collections import defaultdict

    cat_anchors: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        cat_anchors[e["object_category"]].add(e["anchor"]["anchor_id"])
    conflicts = {cat: anchors for cat, anchors in cat_anchors.items() if len(anchors) >= 2}
    assert len(conflicts) >= 6, (
        f"Expected >= 6 conflict categories, got {len(conflicts)}: {set(conflicts.keys())}"
    )


# --- Confidence / evidence / belief coverage ---


def test_corpus_confidence_levels_cover_all(entries: list[dict]):
    levels = {e["confidence_level"] for e in entries}
    assert {"high", "medium", "low"}.issubset(levels), f"Missing levels: {levels}"


def test_corpus_belief_states_cover_both(entries: list[dict]):
    states = {e["belief_state"] for e in entries}
    assert {"confirmed", "stale"}.issubset(states), f"Missing states: {states}"


def test_corpus_evidence_sources_cover_both(entries: list[dict]):
    sources = {e["evidence_source"] for e in entries}
    assert {"direct_observation", "inferred_experience"}.issubset(sources), (
        f"Missing evidence sources: {sources}"
    )
    assert sources == {"direct_observation", "inferred_experience"}, (
        f"Unexpected evidence sources: {sources - {'direct_observation', 'inferred_experience'}}"
    )


# --- build_memory_documents ---


def test_corpus_build_memory_documents_all_executable(corpus: dict):
    docs = build_memory_documents(corpus)
    assert len(docs) == 35, f"Expected 35 documents, got {len(docs)}"
    for doc in docs:
        assert doc.executable, f"{doc.document_id} not executable: {doc.invalid_reason}"
        assert doc.invalid_reason is None, f"{doc.document_id} has invalid_reason: {doc.invalid_reason}"


# --- BM25 retrieval ---


def test_corpus_bm25_cup_kitchen_query(corpus: dict):
    domain_terms = build_domain_terms_from_object_memory(corpus)
    tokenizer = JiebaMemoryTokenizer(domain_terms=domain_terms)
    index = MemoryBM25Index.build(
        documents=build_memory_documents(corpus),
        tokenizer=tokenizer,
    )
    hits = index.search("水杯 厨房", top_k=5)
    assert len(hits) > 0
    matching = [
        h
        for h in hits
        if h.document.metadata.get("object_category") == "cup"
        and h.document.metadata.get("room_id") == "kitchen"
    ]
    assert matching, "No cup+kitchen hit in top 5 for '水杯 厨房'"


def test_corpus_bm25_sofa_query(corpus: dict):
    domain_terms = build_domain_terms_from_object_memory(corpus)
    tokenizer = JiebaMemoryTokenizer(domain_terms=domain_terms)
    index = MemoryBM25Index.build(
        documents=build_memory_documents(corpus),
        tokenizer=tokenizer,
    )
    hits = index.search("遥控器 沙发", top_k=5)
    assert len(hits) > 0
    matching = [
        h
        for h in hits
        if h.document.metadata.get("object_category") == "remote_control"
        and h.document.metadata.get("anchor_id") == "anchor_living_sofa_1"
    ]
    assert matching, "No remote_control+sofa hit in top 5 for '遥控器 沙发'"


def test_corpus_bm25_remote_coffee_table_query(corpus: dict):
    domain_terms = build_domain_terms_from_object_memory(corpus)
    tokenizer = JiebaMemoryTokenizer(domain_terms=domain_terms)
    index = MemoryBM25Index.build(
        documents=build_memory_documents(corpus),
        tokenizer=tokenizer,
    )
    hits = index.search("遥控器 茶几", top_k=5)
    assert len(hits) > 0
    matching = [
        h
        for h in hits
        if h.document.metadata.get("object_category") == "remote_control"
        and h.document.metadata.get("anchor_id") == "anchor_living_coffee_table_1"
    ]
    assert matching, "No remote_control+coffee_table hit in top 5 for '遥控器 茶几'"


def test_corpus_bm25_cup_counter_query(corpus: dict):
    domain_terms = build_domain_terms_from_object_memory(corpus)
    tokenizer = JiebaMemoryTokenizer(domain_terms=domain_terms)
    index = MemoryBM25Index.build(
        documents=build_memory_documents(corpus),
        tokenizer=tokenizer,
    )
    hits = index.search("水杯 操作台", top_k=5)
    assert len(hits) > 0
    matching = [
        h
        for h in hits
        if h.document.metadata.get("object_category") == "cup"
        and h.document.metadata.get("anchor_id") == "anchor_kitchen_counter_1"
    ]
    assert matching, "No cup+counter hit in top 5 for '水杯 操作台'"


def test_corpus_bm25_medicine_bookshelf_query(corpus: dict):
    domain_terms = build_domain_terms_from_object_memory(corpus)
    tokenizer = JiebaMemoryTokenizer(domain_terms=domain_terms)
    index = MemoryBM25Index.build(
        documents=build_memory_documents(corpus),
        tokenizer=tokenizer,
    )
    hits = index.search("药箱 书架", top_k=5)
    assert len(hits) > 0
    matching = [
        h
        for h in hits
        if h.document.metadata.get("object_category") == "medicine_box"
        and h.document.metadata.get("anchor_id") == "anchor_study_bookshelf_1"
    ]
    assert matching, "No medicine_box+bookshelf hit in top 5 for '药箱 书架'"
