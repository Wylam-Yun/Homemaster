"""Repo templates under config/ must stay byte-identical to packaged resources.

config/ holds the user-facing copies (currently symlinks) of files that are
also shipped inside the wheel under homemaster.resources. If either side is
replaced by a real file and edited independently, the installed package and
the checkout would silently diverge — these tests catch that drift.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_example_yaml_matches_packaged_resource() -> None:
    repo_template = (REPO_ROOT / "config" / "homemaster.example.yaml").read_bytes()
    packaged = (files("homemaster.resources") / "homemaster.example.yaml").read_bytes()

    assert repo_template == packaged


def test_runtime_assets_lock_matches_packaged_resource() -> None:
    repo_lock = (REPO_ROOT / "config" / "runtime-assets.lock.json").read_bytes()
    packaged = (
        REPO_ROOT / "src" / "homemaster" / "resources" / "runtime-assets.lock.json"
    ).read_bytes()

    assert repo_lock == packaged
