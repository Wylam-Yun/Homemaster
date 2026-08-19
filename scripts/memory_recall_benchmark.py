#!/usr/bin/env python3
"""Run the HomeMaster 100-record flat-memory benchmark."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from homemaster.benchmarking.memory_recall import (
    BenchmarkPaths,
    evaluate_run,
    generate_run,
    status_run,
    write_run,
)


def _base_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base",
        type=Path,
        default=Path("~/.homemaster/benchmarks").expanduser(),
        help="Private benchmark artifact root.",
    )


def _existing_paths(base: Path, run_id: str) -> BenchmarkPaths:
    root = base.expanduser().resolve() / run_id
    if not (root / "dataset.json").is_file() or not (root / "checkpoint.json").is_file():
        raise FileNotFoundError(f"benchmark run does not exist: {root}")
    return BenchmarkPaths.create(base, run_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write and evaluate 100 synthetic facts through public homemaster -p calls."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Create a locked 100-record dataset.")
    generate.add_argument(
        "--run-id",
        default=None,
        help="Stable run identifier; defaults to a timestamped hm100 identifier.",
    )
    _base_argument(generate)

    for name, help_text in (
        ("write", "Write the next unconfirmed records serially."),
        ("resume", "Resume serial writes from the confirmed checkpoint."),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("--run-id", required=True)
        command.add_argument("--timeout-seconds", type=float, default=600.0)
        command.add_argument("--max-records", type=int, default=None)
        _base_argument(command)

    evaluate = subparsers.add_parser("evaluate", help="Run all retrieval and routing suites.")
    evaluate.add_argument("--run-id", required=True)
    evaluate.add_argument("--timeout-seconds", type=float, default=600.0)
    evaluate.add_argument("--max-cases", type=int, default=None)
    _base_argument(evaluate)

    overnight = subparsers.add_parser(
        "overnight",
        help="Finish all writes, then run the first 100 exact-recall cases.",
    )
    overnight.add_argument("--run-id", required=True)
    overnight.add_argument("--timeout-seconds", type=float, default=600.0)
    overnight.add_argument("--recall-cases", type=int, default=100)
    _base_argument(overnight)

    status = subparsers.add_parser("status", help="Show dataset and checkpoint status.")
    status.add_argument("--run-id", required=True)
    _base_argument(status)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    if args.command == "generate":
        run_id = args.run_id or datetime.now().strftime("hm100-%Y%m%d-%H%M%S")
        paths = generate_run(base=args.base, run_id=run_id)
        result = status_run(paths)
    else:
        paths = _existing_paths(args.base, args.run_id)
        if args.command in {"write", "resume"}:
            if args.max_records is not None and args.max_records <= 0:
                raise ValueError("--max-records must be positive")
            result = write_run(
                paths=paths,
                repo_root=repo_root,
                timeout_seconds=args.timeout_seconds,
                max_records=args.max_records,
            )
        elif args.command == "evaluate":
            if args.max_cases is not None and args.max_cases <= 0:
                raise ValueError("--max-cases must be positive")
            result = evaluate_run(
                paths=paths,
                repo_root=repo_root,
                timeout_seconds=args.timeout_seconds,
                max_cases=args.max_cases,
            )
        elif args.command == "overnight":
            if args.recall_cases <= 0:
                raise ValueError("--recall-cases must be positive")
            write_result = write_run(
                paths=paths,
                repo_root=repo_root,
                timeout_seconds=args.timeout_seconds,
            )
            if write_result["state"] == "complete":
                result = {
                    "stage": "evaluation",
                    "write": write_result,
                    "evaluation": evaluate_run(
                        paths=paths,
                        repo_root=repo_root,
                        timeout_seconds=args.timeout_seconds,
                        max_cases=args.recall_cases,
                    ),
                }
            else:
                result = {"stage": "write_stopped", "write": write_result}
        else:
            result = status_run(paths)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if args.command in {"write", "resume"} and result["state"] in {
        "safe_to_retry",
        "outcome_unknown",
    }:
        return 2
    if args.command == "overnight" and result["stage"] == "write_stopped":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
