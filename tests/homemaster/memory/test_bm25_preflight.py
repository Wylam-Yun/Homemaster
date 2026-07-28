from __future__ import annotations

import os
from pathlib import Path

from homemaster.memory.bm25_preflight import BM25_COMMIT, BM25_HASHES, verify_bm25_offline


def test_locked_bm25_artifact_materializes_into_an_empty_project_cache_offline(
    tmp_path: Path, monkeypatch
) -> None:
    cache_dir = tmp_path / "project" / ".cache" / "homemaster" / "fastembed"
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    path = verify_bm25_offline(cache_dir)

    assert path.name == BM25_COMMIT
    assert path.is_relative_to(cache_dir)
    assert {item.name for item in path.iterdir() if item.is_file()} == set(BM25_HASHES)
    assert os.environ["FASTEMBED_CACHE_PATH"] == str(cache_dir)
