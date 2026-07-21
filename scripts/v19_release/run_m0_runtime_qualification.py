#!/usr/bin/env python3
"""Run the locked one-trial ALFWorld M0 runtime qualification."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homemaster.benchmarking.alfworld.runner import AlfworldBenchmarkRunner
from homemaster.benchmarking.alfworld.types import AlfworldBenchmarkConfig
from homemaster.config import load_config
from scripts.v19_release._common import (
    canonical_json_bytes,
    read_json_object,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)
from scripts.v19_release.alfworld_release_manifest import (
    SOURCE_ENTRY_KEYS,
    verify_release_manifest,
)
from scripts.v19_release.validate_upstream_port_manifest import validate_manifest
from scripts.v19_release.verify_m0_runtime_qualification import (
    SCHEMA_VERSION,
    verify_qualification,
)

OPENHARNESS_COMMIT = "9b2efd795c6aa09f88b0c257d269a9e518da6ae7"
ALFWORLD_COMMIT = "aaba6870f86c5be6a08a491f32a50b906227bc3e"
UNITY_PLATFORM = "Linux"


def run_qualification(
    *,
    repo_root: Path,
    candidate_sha: str,
    openharness_root: Path,
    alfworld_source_root: Path,
    alfworld_config: Path,
    dataset_root: Path,
    release_manifest: Path,
    source_manifest: Path,
    provider_config: Path,
    provider_name: str,
    output_root: Path,
    run_id: str,
    site: str,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    openharness_root = openharness_root.resolve(strict=True)
    alfworld_source_root = alfworld_source_root.resolve(strict=True)
    alfworld_config = alfworld_config.resolve(strict=True)
    dataset_root = dataset_root.resolve(strict=True)
    release_manifest = release_manifest.resolve(strict=True)
    source_manifest = source_manifest.resolve(strict=True)
    provider_config = provider_config.resolve(strict=True)
    output_root = output_root.absolute()

    _require_locked_git(repo_root, candidate_sha, label="HomeMaster", clean=True)
    _require_locked_git(openharness_root, OPENHARNESS_COMMIT, label="OpenHarness", clean=True)
    _require_locked_git(alfworld_source_root, ALFWORLD_COMMIT, label="ALFWorld", clean=True)
    validate_manifest(repo_root / "plan/V1.9/upstream-port-manifest.json", repo_root=repo_root)
    manifest_report = verify_release_manifest(
        release_manifest,
        source_path=source_manifest,
        trial_root=dataset_root,
    )
    if manifest_report.get("dataset_bytes_verified") is not True:
        raise ValueError("release manifest was not verified against real dataset bytes")

    if output_root.exists():
        raise ValueError("M0 qualification output already exists; a fresh path is required")
    inputs_dir = output_root / "inputs"
    staging_root = output_root / "staging/alfworld-root"
    trace_root = output_root / "run"
    inputs_dir.mkdir(parents=True)
    (staging_root / "data").mkdir(parents=True)

    release_copy = inputs_dir / "alfworld_v19_release_trials.json"
    source_copy = inputs_dir / "alfworld_v18_regression_trials.json"
    config_copy = inputs_dir / "base_config.yaml"
    shutil.copyfile(release_manifest, release_copy)
    shutil.copyfile(source_manifest, source_copy)
    shutil.copyfile(alfworld_config, config_copy)

    release_payload = read_json_object(release_copy, label="release manifest")
    selected = release_payload["entries"][0]
    trial_path = (dataset_root / selected["trial_id"]).resolve(strict=True)
    if not trial_path.is_relative_to(dataset_root):
        raise ValueError("selected trial escapes the dataset root")
    trial_copy = inputs_dir / "selected-trial.json"
    shutil.copyfile(trial_path, trial_copy)

    one_trial_manifest = inputs_dir / "canary-trial-manifest.json"
    write_canonical_json(
        one_trial_manifest,
        {
            "schema_version": "alfworld-trial-selection-v1",
            "entries": [{key: selected[key] for key in SOURCE_ENTRY_KEYS}],
        },
    )
    _link_directory(staging_root / "alfworld", alfworld_source_root / "alfworld")
    _link_directory(staging_root / "configs", alfworld_source_root / "configs")
    _link_directory(staging_root / "data/json_2.1.1", dataset_root)

    provider = _provider_identity(provider_config, provider_name)
    environment = _environment_identity(
        repo_root=repo_root,
        alfworld_source_root=alfworld_source_root,
        provider=provider,
        site=site,
    )
    config = AlfworldBenchmarkConfig(
        alfworld_root=staging_root,
        alfworld_config=config_copy,
        trace_root=trace_root,
        env_type="AlfredThorEnv",
        split="valid_unseen",
        episodes=1,
        memory_mode="disabled",
        max_invalid_actions=1,
        max_env_steps=1,
        max_tool_iterations=2,
        provider_config=provider_config,
        provider_name=provider_name,
        run_id=run_id,
        observation_mode="visual_eval",
        trial_manifest=one_trial_manifest,
    )
    runner = AlfworldBenchmarkRunner(config=config)
    summary = runner.run()
    summary_payload = summary.to_dict()

    run_dir = runner.run_dir
    episode_dir = run_dir / "episode-0001"
    reset_path = episode_dir / "reset-transaction.json"
    attempts_path = episode_dir / "provider_attempts.jsonl"
    summary_path = run_dir / "summary.json"
    frames = sorted((episode_dir / "frames").glob("*.png"))
    if not frames:
        raise ValueError("canary did not produce an explicit captured frame")
    frame_path = frames[0]

    environment["unity"] = _unity_identity()
    reset_payload = read_json_object(reset_path, label="reset transaction")
    event_refs = reset_payload.get("event_files")
    snapshot_ref = reset_payload.get("snapshot_ref")
    if (
        not isinstance(event_refs, list)
        or not event_refs
        or any(not isinstance(ref, str) for ref in event_refs)
        or not isinstance(snapshot_ref, str)
    ):
        raise ValueError("reset transaction has incomplete raw evidence references")
    reset_evidence_paths = [
        (episode_dir / ref).resolve(strict=True)
        for ref in (*event_refs, snapshot_ref)
    ]
    if any(not path.is_relative_to(episode_dir) for path in reset_evidence_paths):
        raise ValueError("reset evidence reference escapes its episode directory")
    artifacts = {
        _relative(output_root, path): sha256_file(path)
        for path in (
            release_copy,
            source_copy,
            config_copy,
            trial_copy,
            one_trial_manifest,
            reset_path,
            attempts_path,
            summary_path,
            frame_path,
            *reset_evidence_paths,
        )
    }
    source_payload = read_json_object(source_copy, label="source manifest")
    dataset_identity = sha256_bytes(
        canonical_json_bytes(
            [
                {"trial_id": entry["trial_id"], "trial_sha256": entry["trial_sha256"]}
                for entry in source_payload["entries"]
            ]
        )
    )
    attempts = _read_nonempty_jsonl(attempts_path)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "candidate": {
            "homemaster_commit": candidate_sha,
            "openharness_commit": OPENHARNESS_COMMIT,
            "alfworld_commit": ALFWORLD_COMMIT,
        },
        "environment": environment,
        "inputs": {
            "alfworld_config_ref": _relative(output_root, config_copy),
            "alfworld_config_sha256": sha256_file(config_copy),
            "dataset_identity_sha256": dataset_identity,
            "release_manifest_ref": _relative(output_root, release_copy),
            "release_manifest_sha256": sha256_file(release_copy),
            "selected_trial": {
                "expected_logical_scene": selected["expected_logical_scene"],
                "goal_fingerprint": selected["goal_fingerprint"],
                "trial_id": selected["trial_id"],
                "trial_sha256": selected["trial_sha256"],
            },
            "source_manifest_ref": _relative(output_root, source_copy),
            "source_manifest_sha256": sha256_file(source_copy),
            "trial_bytes_ref": _relative(output_root, trial_copy),
        },
        "canary": {
            "env_type": "AlfredThorEnv",
            "frame_ref": _relative(output_root, frame_path),
            "fresh": True,
            "observation_mode": "visual_eval",
            "provider_attempt_count": len(attempts),
            "provider_attempts_ref": _relative(output_root, attempts_path),
            "reset_ref": _relative(output_root, reset_path),
            "scorer_available": summary_payload.get("formal_score_available") is True,
            "split": "valid_unseen",
            "summary_ref": _relative(output_root, summary_path),
        },
        "artifacts": artifacts,
    }
    report_path = output_root / "qualification.json"
    write_canonical_json(report_path, report)
    verification = verify_qualification(
        report_path,
        artifact_root=output_root,
        expected_candidate=candidate_sha,
    )
    write_canonical_json(output_root / "verification.json", verification)
    return verification


def _require_locked_git(
    root: Path,
    expected: str,
    *,
    label: str,
    clean: bool,
) -> None:
    actual = _git(root, "rev-parse", "HEAD")
    if actual != expected:
        raise ValueError(f"{label} commit differs from the locked commit")
    if clean and _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError(f"{label} tracked worktree is dirty")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _link_directory(link: Path, target: Path) -> None:
    resolved = target.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"staging target is not a directory: {target}")
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(resolved, target_is_directory=True)


def _provider_identity(config_path: Path, provider_name: str) -> dict[str, Any]:
    config = load_config(config_path)
    profile = config.resolve_provider(provider_name, kind="chat")
    api_keys = tuple(profile.api_keys)
    if not api_keys or any(not key or (key.startswith("<") and key.endswith(">")) for key in api_keys):
        raise ValueError("real provider credentials are unavailable")
    base_url = str(profile.base_url or "")
    normalized = profile.name.casefold()
    non_mock = (
        base_url.startswith("https://")
        and all(token not in normalized for token in ("mock", "scripted", "loopback"))
    )
    if not non_mock:
        raise ValueError("provider is mock, scripted, loopback, or lacks HTTPS transport")
    return {"name": profile.name, "model": profile.model, "non_mock": True}


def _environment_identity(
    *,
    repo_root: Path,
    alfworld_source_root: Path,
    provider: dict[str, Any],
    site: str,
) -> dict[str, Any]:
    if site not in {"hpc2", "hkust4"}:
        raise ValueError("site must be hpc2 or hkust4")
    conda = _conda_identity(site=site)
    display = os.environ.get("DISPLAY")
    if not display:
        raise ValueError("DISPLAY is required on the HPC2 GPU node")
    subprocess.run(
        ["xdpyinfo", "-display", display],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    gpu_line = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=name,driver_version,memory.total",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.splitlines()[0]
    gpu_name, driver_version, memory_mib = [item.strip() for item in gpu_line.split(",")]
    alfworld_containment_root = (
        Path(conda["prefix"]) if site == "hkust4" else alfworld_source_root
    )
    alfworld_containment_key = (
        "origin_within_conda_prefix" if site == "hkust4" else "origin_within_locked_source"
    )
    imports = {
        "homemaster": _import_identity(
            "homemaster", containment_root=repo_root, containment_key="origin_within_candidate"
        ),
        "alfworld": _import_identity(
            "alfworld",
            containment_root=alfworld_containment_root,
            containment_key=alfworld_containment_key,
        ),
        "ai2thor": _import_identity(
            "ai2thor",
            containment_root=(Path(conda["prefix"]) if site == "hkust4" else None),
            containment_key=("origin_within_conda_prefix" if site == "hkust4" else None),
        ),
    }
    return {
        "site": site,
        "hostname": socket.gethostname(),
        "python_executable": str(Path(sys.executable).resolve(strict=True)),
        "python_version": platform.python_version(),
        "imports": imports,
        "conda": conda,
        "provider": provider,
        "gpu": {
            "name": gpu_name,
            "driver_version": driver_version,
            "memory_mib": int(memory_mib),
        },
        "display": {"name": display, "available": True},
        "unity": {},
    }


def _conda_identity(*, site: str) -> dict[str, Any]:
    if site == "hpc2":
        return {"environment_name": None, "explicit_sha256": None, "prefix": None}
    environment_name = os.environ.get("CONDA_DEFAULT_ENV")
    raw_prefix = os.environ.get("CONDA_PREFIX")
    if environment_name != "hm_alfworld" or not raw_prefix:
        raise ValueError("hkust4 qualification requires CONDA_DEFAULT_ENV=hm_alfworld")
    prefix = Path(raw_prefix).resolve(strict=True)
    executable = Path(sys.executable).resolve(strict=True)
    if not executable.is_relative_to(prefix):
        raise ValueError("hkust4 Python executable is outside hm_alfworld")
    explicit = subprocess.run(
        ["conda", "list", "--explicit"],
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    if not explicit:
        raise ValueError("hkust4 conda explicit package list is empty")
    return {
        "environment_name": environment_name,
        "explicit_sha256": sha256_bytes(explicit),
        "prefix": str(prefix),
    }


def _import_identity(
    package: str,
    *,
    containment_root: Path | None = None,
    containment_key: str | None = None,
) -> dict[str, Any]:
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        raise ValueError(f"required import is unavailable: {package}")
    origin = Path(spec.origin).resolve(strict=True)
    identity: dict[str, Any] = {
        "status": "present",
        "version": importlib.metadata.version(package),
        "origin": str(origin),
    }
    if containment_root is not None and containment_key is not None:
        identity[containment_key] = origin.is_relative_to(containment_root)
        if identity[containment_key] is not True:
            raise ValueError(f"{package} import is outside its locked root")
    return identity


def _unity_identity() -> dict[str, Any]:
    import ai2thor._builds

    build = ai2thor._builds.BUILDS.get(UNITY_PLATFORM)
    if not isinstance(build, dict):
        raise ValueError("ai2thor has no pinned Linux Unity build")
    expected = str(build.get("sha256") or "")
    url = str(build.get("url") or "")
    build_name = Path(urlparse(url).path).stem
    executable = (Path.home() / ".ai2thor/releases" / build_name / build_name).resolve(
        strict=True
    )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("pinned Unity executable is unavailable after canary")
    version = build_name.removeprefix("thor-").removesuffix("-Linux64")
    return {
        "platform": UNITY_PLATFORM,
        "version": version,
        "expected_sha256": expected,
        "verified_sha256": expected,
    }


def _read_nonempty_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("provider attempt ledger is empty or malformed")
    return rows


def _relative(root: Path, path: Path) -> str:
    return path.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--openharness-root", type=Path, required=True)
    parser.add_argument("--alfworld-source-root", type=Path, required=True)
    parser.add_argument("--alfworld-config", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--release-manifest", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--provider-config", type=Path, required=True)
    parser.add_argument("--provider-name", default="Mimo")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--site", choices=("hpc2", "hkust4"), required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = run_qualification(
            repo_root=args.repo_root,
            candidate_sha=args.candidate_sha,
            openharness_root=args.openharness_root,
            alfworld_source_root=args.alfworld_source_root,
            alfworld_config=args.alfworld_config,
            dataset_root=args.dataset_root,
            release_manifest=args.release_manifest,
            source_manifest=args.source_manifest,
            provider_config=args.provider_config,
            provider_name=args.provider_name,
            output_root=args.output_root,
            run_id=args.run_id,
            site=args.site,
        )
    except Exception as exc:
        print(f"M0 runtime qualification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
