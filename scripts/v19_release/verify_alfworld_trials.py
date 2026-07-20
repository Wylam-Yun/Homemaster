#!/usr/bin/env python3
"""Verify the deterministic V1.9 ALFWorld release inventory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.v19_release.alfworld_release_manifest import verify_release_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("config/alfworld_v18_regression_trials.json"),
    )
    parser.add_argument("--trial-root", type=Path)
    args = parser.parse_args()
    try:
        report = verify_release_manifest(
            args.manifest,
            source_path=args.source,
            trial_root=args.trial_root,
        )
    except ValueError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
