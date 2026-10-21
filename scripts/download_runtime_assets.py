#!/usr/bin/env python3
"""Fetch and install locked HomeMaster runtime assets.

Thin wrapper: the implementation lives in ``homemaster.setup_runtime`` so the
``homemaster`` wheel can provision assets without a checkout. This script stays
the repo-side entry point (``python -m scripts.download_runtime_assets`` and
direct invocation) and resolves the sibling ``src/`` tree first so it works
even when the installed ``homemaster`` in ``.runtime/venv`` is stale.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if (_SRC / "homemaster").is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import os  # noqa: E402  (kept: tests monkeypatch assets.os.uname)

from homemaster.setup_runtime import (  # noqa: E402
    _current_platform,
    _sha256,
    ensure_java,
    ensure_neo4j,
)

__all__ = ["_current_platform", "_sha256", "ensure_java", "ensure_neo4j", "os"]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--java-destination", type=Path)
    parser.add_argument("--offline-bundle", type=Path)
    args = parser.parse_args()
    if args.java_destination:
        ensure_java(args.repo_root.resolve(), args.java_destination.resolve(), offline_bundle=args.offline_bundle)
    ensure_neo4j(args.repo_root.resolve(), args.destination.resolve(), offline_bundle=args.offline_bundle)
