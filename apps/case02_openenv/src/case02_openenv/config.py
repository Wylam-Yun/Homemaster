"""Configuration for the case_02 evaluation service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServiceConfig:
    data_root: Path
    artifact_root: Path
    bind_host: str = "127.0.0.1"
    port: int = 8765

    @classmethod
    def from_env(cls) -> ServiceConfig:
        return cls(
            data_root=Path(
                os.environ.get("CASE02_DATA_ROOT", "data/coworker_demo/case_02")
            ).resolve(),
            artifact_root=Path(
                os.environ.get("CASE02_ARTIFACT_ROOT", "var/coworker-demo")
            ).resolve(),
            bind_host=os.environ.get("CASE02_BIND_HOST", "127.0.0.1"),
            port=int(os.environ.get("CASE02_PORT", "8765")),
        )
