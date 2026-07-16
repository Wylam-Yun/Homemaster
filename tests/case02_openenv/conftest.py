from __future__ import annotations

from pathlib import Path

import pytest
from case02_openenv.episode_store import EpisodeStore


@pytest.fixture
def store(tmp_path: Path) -> EpisodeStore:
    return EpisodeStore(
        data_root=Path("data/coworker_demo/case_02"),
        artifact_root=tmp_path / "runs",
    )
