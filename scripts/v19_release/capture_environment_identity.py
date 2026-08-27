#!/usr/bin/env python3
"""Capture sanitized V1.9 candidate environment identity."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.v19_release._common import (
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)

SCHEMA_VERSION = "homemaster-v1.9-environment-identity-v1"


def capture_identity(
    *,
    repo_root: Path,
    site: str,
    provider: str | None,
    model: str | None,
    alfworld_check: bool,
    alfworld_root: Path | None,
    alfworld_config: Path | None,
    alfworld_trials: Path | None,
    conda_explicit: bytes | None = None,
    expected_conda_env: str | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=True)
    if site not in {"hpc2", "hkust4"}:
        raise ValueError("site must be hpc2 or hkust4")
    if not provider or not model:
        raise ValueError("provider and model identity are required")
    if alfworld_check:
        missing = [
            name
            for name, value in (
                ("alfworld_root", alfworld_root),
                ("alfworld_config", alfworld_config),
                ("alfworld_trials", alfworld_trials),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"ALFWorld identity is missing required inputs: {', '.join(missing)}")

    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    required_conda_env = expected_conda_env
    if site == "hkust4" and alfworld_check:
        required_conda_env = required_conda_env or "hm_alfworld"
        if required_conda_env != "hm_alfworld":
            raise ValueError("hkust4 ALFWorld identity requires expected Conda env hm_alfworld")
    conda_prefix: Path | None = None
    if required_conda_env is not None:
        if not alfworld_check:
            raise ValueError("expected Conda env is only valid for an ALFWorld identity")
        if conda_env != required_conda_env:
            raise ValueError(
                f"ALFWorld identity requires CONDA_DEFAULT_ENV={required_conda_env}"
            )
        raw_prefix = os.environ.get("CONDA_PREFIX")
        if not raw_prefix:
            raise ValueError("ALFWorld Conda identity requires CONDA_PREFIX")
        conda_prefix = Path(raw_prefix).absolute()
        python_executable = Path(sys.executable).absolute()
        if not python_executable.is_relative_to(conda_prefix):
            raise ValueError("Python executable is outside the expected Conda environment")
        if conda_explicit is None:
            conda_explicit = _conda_explicit_bytes()

    imports = {
        package: _import_identity(package, repo_root=repo_root, conda_prefix=conda_prefix)
        for package in ("homemaster", "alfworld", "ai2thor")
    }
    if imports["homemaster"]["status"] != "present":
        raise ValueError("homemaster import is unavailable")
    if imports["homemaster"]["origin_within_repo"] is not True:
        raise ValueError("homemaster import origin is outside the candidate worktree")
    if alfworld_check and any(imports[name]["status"] != "present" for name in ("alfworld", "ai2thor")):
        raise ValueError("ALFWorld identity requires homemaster, alfworld, and ai2thor imports")
    if conda_prefix is not None and any(
        imports[name]["origin_within_conda_prefix"] is not True
        for name in ("alfworld", "ai2thor")
    ):
        raise ValueError("ALFWorld dependency import origin is outside the expected Conda env")

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "site": site,
        "homemaster_commit": _git(repo_root, "rev-parse", "HEAD"),
        "git_status_porcelain_clean": not bool(_git(repo_root, "status", "--porcelain")),
        "python": {
            "executable": str(Path(sys.executable).absolute()),
            "version": platform.python_version(),
            "full_version": sys.version,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "provider": {"name": provider, "model": model},
        "imports": imports,
        "locks": {
            "root_uv_lock_sha256": _optional_hash(repo_root / "uv.lock"),
        },
        "alfworld": {
            "checked": alfworld_check,
            "root_identity_sha256": _directory_identity(alfworld_root),
            "config_sha256": _optional_hash(alfworld_config),
            "trial_manifest_sha256": _optional_hash(alfworld_trials),
            "environment_name": conda_env if alfworld_check else None,
            "expected_environment_name": required_conda_env,
            "conda_explicit_sha256": (
                sha256_bytes(conda_explicit) if conda_explicit is not None else None
            ),
        },
    }
    if required_conda_env is not None:
        required = payload["alfworld"]
        if not required["conda_explicit_sha256"]:
            raise ValueError("ALFWorld Conda identity is missing conda explicit hash")
    return payload


def _import_identity(
    package: str,
    *,
    repo_root: Path,
    conda_prefix: Path | None,
) -> dict[str, Any]:
    spec = importlib.util.find_spec(package)
    if spec is None or spec.origin is None:
        return {
            "status": "absent",
            "version": None,
            "origin": None,
            "origin_within_repo": None,
            "origin_within_conda_prefix": None,
        }
    try:
        origin = Path(spec.origin).resolve(strict=True)
        resolved_conda_prefix = (
            conda_prefix.resolve(strict=True) if conda_prefix is not None else None
        )
    except OSError as exc:
        raise ValueError(f"{package} import origin cannot be resolved") from exc
    origin_within_repo = origin.is_relative_to(repo_root)
    origin_within_conda = (
        origin.is_relative_to(resolved_conda_prefix)
        if resolved_conda_prefix is not None
        else None
    )
    try:
        display = origin.relative_to(repo_root).as_posix()
    except ValueError:
        display = str(origin)
    try:
        version = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {
        "status": "present",
        "version": version,
        "origin": display,
        "origin_within_repo": origin_within_repo,
        "origin_within_conda_prefix": origin_within_conda,
    }


def _directory_identity(path: Path | None) -> str | None:
    if path is None:
        return None
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("ALFWorld root must be a directory")
    records: list[bytes] = []
    for candidate in sorted(root.rglob("*.json")):
        if candidate.is_file():
            relative = candidate.relative_to(root).as_posix().encode("utf-8")
            records.append(relative + b"\x00" + sha256_file(candidate).encode("ascii"))
    if not records:
        raise ValueError("ALFWorld root contains no JSON identity files")
    return sha256_bytes(b"\n".join(records))


def _optional_hash(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"identity path must be a file: {path}")
    return sha256_file(resolved)


def _conda_explicit_bytes() -> bytes:
    try:
        return subprocess.run(
            ["conda", "list", "--explicit"], check=True, capture_output=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("unable to capture conda list --explicit") from exc


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _default_provider_identity(repo_root: Path) -> tuple[str, str]:
    from homemaster.config import load_config

    config_path = repo_root / "config/homemaster.yaml"
    provider = load_config(config_path).get_provider(kind="chat")
    return provider.name, provider.model


def _detected_site() -> str:
    return "hkust4" if "hkust4" in platform.node().casefold() else "hpc2"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--site", choices=("hpc2", "hkust4"))
    parser.add_argument("--profile", choices=("alfworld",))
    parser.add_argument("--expected-conda-env")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--alfworld-check", action="store_true")
    parser.add_argument("--alfworld-root", type=Path)
    parser.add_argument("--alfworld-config", type=Path)
    parser.add_argument("--alfworld-trials", type=Path)
    args = parser.parse_args()
    try:
        repo_root = args.repo_root.resolve(strict=True)
        provider = args.provider
        model = args.model
        if not provider or not model:
            default_provider, default_model = _default_provider_identity(repo_root)
            provider = provider or default_provider
            model = model or default_model
        alfworld_check = args.alfworld_check or args.profile == "alfworld"
        alfworld_root = args.alfworld_root
        if alfworld_root is None and os.environ.get("HOMEMASTER_ALFWORLD_ROOT"):
            alfworld_root = Path(os.environ["HOMEMASTER_ALFWORLD_ROOT"])
        alfworld_config = args.alfworld_config
        if alfworld_config is None and os.environ.get("HOMEMASTER_ALFWORLD_CONFIG"):
            alfworld_config = Path(os.environ["HOMEMASTER_ALFWORLD_CONFIG"])
        alfworld_trials = args.alfworld_trials
        if alfworld_trials is None and alfworld_check:
            alfworld_trials = repo_root / "config/alfworld_v19_release_trials.json"
        payload = capture_identity(
            repo_root=repo_root,
            site=args.site or _detected_site(),
            provider=provider,
            model=model,
            alfworld_check=alfworld_check,
            alfworld_root=alfworld_root,
            alfworld_config=alfworld_config,
            alfworld_trials=alfworld_trials,
            expected_conda_env=args.expected_conda_env,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"identity capture failed: {exc}", file=sys.stderr)
        return 1
    write_canonical_json(args.output, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
