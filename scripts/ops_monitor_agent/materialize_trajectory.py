#!/usr/bin/env python3
"""Build the private deterministic evidence bundle for one Ops Monitor Agent run."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from homemaster.browser.trajectory_bundle import materialize_trajectory_bundle


def _commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ticket", required=True, type=Path)
    parser.add_argument("--terminal-verification", required=True, type=Path)
    parser.add_argument("--final-state", required=True, type=Path)
    parser.add_argument("--homemaster-repo", required=True, type=Path)
    parser.add_argument("--ant-repo", required=True, type=Path)
    args = parser.parse_args()
    result = materialize_trajectory_bundle(
        run_dir=args.run_dir,
        output_dir=args.output,
        ticket_path=args.ticket,
        terminal_verification_path=args.terminal_verification,
        final_state_path=args.final_state,
        repository_commits={
            "homemaster": _commit(args.homemaster_repo),
            "ant-design-pro": _commit(args.ant_repo),
        },
    )
    print(result)


if __name__ == "__main__":
    main()
