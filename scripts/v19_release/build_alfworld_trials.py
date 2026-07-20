#!/usr/bin/env python3
"""Build the deterministic V1.9 ALFWorld release inventory."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.v19_release._common import write_canonical_json
from scripts.v19_release.alfworld_release_manifest import (
    SOURCE_DISPLAY_PATH,
    build_release_manifest,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trial-root", type=Path)
    args = parser.parse_args()
    payload = build_release_manifest(
        args.source,
        source_display_path=SOURCE_DISPLAY_PATH,
        trial_root=args.trial_root,
    )
    write_canonical_json(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
