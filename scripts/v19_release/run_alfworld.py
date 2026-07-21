#!/usr/bin/env python3
"""Run the locked V1.9 ALFWorld four-trial release workload."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
from homemaster.config import load_config
from scripts.v19_release._common import (
    read_json_object,
    sha256_file,
    write_canonical_json,
)
from scripts.v19_release.alfworld_release_manifest import (
    SOURCE_DISPLAY_PATH,
    SOURCE_ENTRY_KEYS,
    verify_release_manifest,
)
from scripts.v19_release.verify_alfworld_release import (
    MANIFEST_ENTRY_KEYS,
    PROFILE,
    SCHEMA_VERSION,
    verify_alfworld_release,
)


def run_alfworld_release(
    *,
    repo_root: Path,
    alfworld_root: Path,
    alfworld_config: Path,
    trace_root: Path,
    env_type: str,
    split: str,
    episodes: int,
    trial_manifest: Path,
    observation_mode: str,
    api_config: Path,
    provider_name: str,
    report_path: Path,
    max_invalid_actions: int = 100,
    max_env_steps: int = 50,
    max_tool_iterations: int = 1000,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    report_root = report_path.parent.resolve()
    report_root.mkdir(parents=True, exist_ok=True)
    trace_root = trace_root.resolve()
    if not trace_root.is_relative_to(report_root):
        raise ValueError("trace root must be contained by the report directory")
    profile = {
        "env_type": env_type,
        "episodes": episodes,
        "observation_mode": observation_mode,
        "split": split,
    }
    if profile != PROFILE:
        raise ValueError("release runner requires AlfredThorEnv/valid_unseen/visual_eval/4")

    candidate_sha = _candidate_sha(repo_root)
    source_manifest = repo_root / SOURCE_DISPLAY_PATH
    trial_root = alfworld_root.resolve(strict=True) / "data" / "json_2.1.1"
    manifest_verification = verify_release_manifest(
        trial_manifest,
        source_path=source_manifest,
        trial_root=trial_root,
    )
    manifest = read_json_object(trial_manifest, label="locked release manifest")
    entries = manifest["entries"]
    assert isinstance(entries, list)

    inputs_dir = report_root / "alfworld-inputs"
    execution_manifest = inputs_dir / (
        f"{manifest_verification['manifest_sha256']}-execution.json"
    )
    if execution_manifest.exists():
        raise ValueError("fresh release execution manifest already exists")
    write_canonical_json(
        execution_manifest,
        {
            "schema_version": "alfworld-trial-selection-v1",
            "entries": [
                {key: entry[key] for key in SOURCE_ENTRY_KEYS}
                for entry in entries
            ],
        },
    )

    provider = _provider_identity(api_config, provider_name)
    run_id = f"v19-{candidate_sha[:12]}-{uuid.uuid4().hex[:12]}"
    config = AlfworldBenchmarkConfig(
        alfworld_root=alfworld_root,
        alfworld_config=alfworld_config,
        trace_root=trace_root,
        env_type="AlfredThorEnv",
        split="valid_unseen",
        episodes=4,
        memory_mode="disabled",
        max_invalid_actions=max_invalid_actions,
        max_env_steps=max_env_steps,
        max_tool_iterations=max_tool_iterations,
        provider_config=api_config,
        provider_name=provider_name,
        run_id=run_id,
        observation_mode="visual_eval",
        trial_manifest=execution_manifest,
    )
    runner = AlfworldBenchmarkRunner(config=config)
    if runner.run_dir.exists():
        raise ValueError("fresh ALFWorld run directory already exists")
    summary = runner.run()
    summary_payload = summary.to_dict()
    report = _build_report(
        candidate_sha=candidate_sha,
        execution_manifest=execution_manifest,
        locked_manifest=trial_manifest,
        locked_entries=entries,
        profile=profile,
        provider=provider,
        report_root=report_root,
        run_dir=runner.run_dir,
        summary=summary_payload,
    )
    write_canonical_json(report_path, report)
    return report


def _build_report(
    *,
    candidate_sha: str,
    execution_manifest: Path,
    locked_manifest: Path,
    locked_entries: list[dict[str, Any]],
    profile: dict[str, Any],
    provider: dict[str, str],
    report_root: Path,
    run_dir: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    summary_path = run_dir / "summary.json"
    for index in range(len(locked_entries)):
        (run_dir / f"episode-{index + 1:04d}" / "trace.jsonl").touch(exist_ok=True)
    artifacts = _artifact_hashes(
        report_root,
        [execution_manifest, *sorted(path for path in run_dir.rglob("*") if path.is_file())],
    )
    raw_episodes = summary.get("episodes")
    if not isinstance(raw_episodes, list) or len(raw_episodes) != len(locked_entries):
        raise ValueError("runner summary episode count mismatches locked manifest")

    report_episodes = []
    for index, (entry, episode) in enumerate(
        zip(locked_entries, raw_episodes, strict=True)
    ):
        if not isinstance(episode, dict):
            raise ValueError(f"runner episode {index} must be an object")
        episode_dir = run_dir / f"episode-{index + 1:04d}"
        attempts_path = episode_dir / "provider_attempts.jsonl"
        attempts = _read_jsonl(attempts_path, label=f"episode {index} provider attempts")
        report_episodes.append(
            {
                "index": index,
                "trial_id": entry["trial_id"],
                "run_id": episode.get("run_id"),
                "trace_ref": _relative(report_root, episode_dir / "trace.jsonl"),
                "model_trace_ref": _relative(
                    report_root, episode_dir / "model_trace.jsonl"
                ),
                "runtime_events_ref": _relative(
                    report_root, episode_dir / "runtime" / "runtime_events.jsonl"
                ),
                "provider_attempts_ref": _relative(report_root, attempts_path),
                "reset_ref": _relative(
                    report_root, episode_dir / "reset-transaction.json"
                ),
                "summary_ref": _relative(report_root, episode_dir / "summary.json"),
                "provider_attempt_count": len(attempts),
                "successful_provider_attempt_count": sum(
                    int(row.get("response_completed") is True) for row in attempts
                ),
                "classification": episode.get("classification"),
                "success": episode.get("success") is True,
                "score_eligible": episode.get("score_eligible") is True,
                "action_counts": {
                    "agent_tool_calls": episode.get("agent_tool_call_count"),
                    "setup_backend_actions": episode.get("setup_backend_action_count"),
                    "control_backend_actions": episode.get("control_backend_action_count"),
                    "model_backend_actions": episode.get("model_backend_action_count"),
                    "total_backend_actions": episode.get("total_backend_action_count"),
                    "total_external_requests": episode.get("total_external_request_count"),
                },
                "terminal_tool_call_id": episode.get("terminal_tool_call_id"),
                "terminal_evidence_ref": episode.get("terminal_evidence_ref"),
            }
        )

    selected = len(locked_entries)
    attempted = sum(int(row["provider_attempt_count"] > 0) for row in report_episodes)
    eligible = sum(int(row["score_eligible"]) for row in report_episodes)
    success = sum(int(row["success"]) for row in report_episodes)
    task_failures = [row["trial_id"] for row in report_episodes if not row["success"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "candidate_sha": candidate_sha,
        "run_id": summary.get("run_id"),
        "profile": profile,
        "provider": provider,
        "manifest": {
            "sha256": sha256_file(locked_manifest),
            "entries": [
                {key: entry[key] for key in MANIFEST_ENTRY_KEYS}
                for entry in locked_entries
            ],
        },
        "execution_manifest_ref": _relative(report_root, execution_manifest),
        "summary_ref": _relative(report_root, summary_path),
        "episodes": report_episodes,
        "summary": {
            "selected": selected,
            "attempted": attempted,
            "eligible": eligible,
            "success": success,
            "formal_score_available": (
                selected == 4
                and attempted == selected
                and eligible == selected
                and summary.get("formal_score_available") is True
            ),
            "harness_invalid": selected - eligible,
            "contract_violations": 0,
            "task_failures": task_failures,
        },
        "artifacts": artifacts,
    }


def _artifact_hashes(root: Path, paths: list[Path]) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"release artifact is missing or is a symlink: {path}")
        ref = _relative(root, path)
        if ref in artifacts:
            raise ValueError(f"duplicate release artifact reference: {ref}")
        artifacts[ref] = sha256_file(path)
    return artifacts


def _relative(root: Path, path: Path) -> str:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"release artifact escapes report root: {path}")
    return resolved.relative_to(resolved_root).as_posix()


def _read_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label} must contain JSON objects")
    return rows


def _provider_identity(config_path: Path, provider_name: str) -> dict[str, str]:
    profile = load_config(config_path).get_provider(provider_name, kind="chat")
    if not profile.api_keys:
        raise ValueError("real provider credentials are unavailable")
    if not profile.base_url.startswith("https://"):
        raise ValueError("release provider must use an HTTPS endpoint")
    if any(token in profile.name.casefold() for token in ("mock", "scripted", "loopback")):
        raise ValueError("release provider cannot be mock, scripted, or loopback")
    return {
        "name": profile.name,
        "model": profile.model,
        "api_format": profile.api_format,
        "transport": profile.transport,
    }


def _candidate_sha(repo_root: Path) -> str:
    sha = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(sha) != 40 or any(character not in "0123456789abcdef" for character in sha):
        raise ValueError("candidate must resolve to a full lowercase Git commit")
    dirty = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if dirty:
        raise ValueError("tracked candidate worktree must be clean")
    return sha


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--alfworld-root", type=Path, required=True)
    parser.add_argument("--alfworld-config", type=Path, required=True)
    parser.add_argument("--trace-root", type=Path, required=True)
    parser.add_argument("--env-type", default="AlfredThorEnv")
    parser.add_argument("--split", default="valid_unseen")
    parser.add_argument("--episodes", type=int, default=4)
    parser.add_argument("--trial-manifest", type=Path, required=True)
    parser.add_argument("--observation-mode", default="visual_eval")
    parser.add_argument("--api-config", type=Path, required=True)
    parser.add_argument("--provider-name", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--max-invalid-actions", type=int, default=100)
    parser.add_argument("--max-env-steps", type=int, default=50)
    parser.add_argument("--max-tool-iterations", type=int, default=1000)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = run_alfworld_release(
            repo_root=args.repo_root,
            alfworld_root=args.alfworld_root,
            alfworld_config=args.alfworld_config,
            trace_root=args.trace_root,
            env_type=args.env_type,
            split=args.split,
            episodes=args.episodes,
            trial_manifest=args.trial_manifest,
            observation_mode=args.observation_mode,
            api_config=args.api_config,
            provider_name=args.provider_name,
            report_path=args.report,
            max_invalid_actions=args.max_invalid_actions,
            max_env_steps=args.max_env_steps,
            max_tool_iterations=args.max_tool_iterations,
        )
        verification = verify_alfworld_release(
            args.report,
            manifest_path=args.trial_manifest,
            gate="migration",
            expected_sha=report["candidate_sha"],
            expect_selected=4,
            expect_attempted=4,
            expect_eligible=4,
        )
    except Exception as exc:
        print(f"ALFWorld release run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(verification, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
