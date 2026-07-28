from __future__ import annotations

from homemaster.memory.bm25_preflight import BM25_COMMIT, BM25_HASHES, verify_bm25_offline


def test_locked_bm25_artifact_loads_offline_and_encodes_chinese() -> None:
    path = verify_bm25_offline()
    assert path.name == BM25_COMMIT
    assert {item.name for item in path.iterdir() if item.is_file()} == set(BM25_HASHES)
