#!/usr/bin/env python3
"""Verify the V3.5 release boundary from a clean build artifact."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd or ROOT, env=env, text=True, capture_output=True, check=False)


def _artifact_names(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    with tarfile.open(path) as archive:
        return {member.name for member in archive.getmembers()}


def _build_command(output: Path) -> list[str]:
    uv = os.environ.get("HOMEMASTER_UV") or shutil.which("uv")
    if uv:
        return [
            uv,
            "build",
            "--wheel",
            "--sdist",
            "--no-build-isolation",
            "--out-dir",
            str(output),
            str(ROOT),
        ]
    return [sys.executable, "-m", "build", "--wheel", "--sdist", "--no-isolation", "--outdir", str(output)]


def _check(name: str, passed: bool, detail: str, checks: list[dict[str, object]]) -> None:
    checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})


def audit() -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    required = [
        ROOT / "uv.lock",
        ROOT / "config" / "alfworld" / "requirements.lock",
        ROOT / "protocols" / "alfworld-v1.schema.json",
        ROOT / "config" / "homemaster.example.yaml",
    ]
    _check("versioned_release_inputs", all(path.is_file() for path in required), ", ".join(str(path.relative_to(ROOT)) for path in required if not path.is_file()), checks)

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    _check("runtime_and_private_config_ignored", "/.runtime/" in gitignore and "/config/homemaster.yaml" in gitignore, ".gitignore", checks)
    tracked_private = _run(["git", "ls-files", ".runtime", "config/homemaster.yaml", "config/api_config.json", "config/nvidia_api_config.json"])
    _check("private_runtime_not_tracked", not tracked_private.stdout.strip(), tracked_private.stdout.strip(), checks)

    with tempfile.TemporaryDirectory(prefix="homemaster-v35-release-") as temp:
        output = Path(temp) / "dist"
        output.mkdir()
        build = _run(_build_command(output), cwd=Path(temp))
        _check("build_wheel_and_sdist", build.returncode == 0, (build.stderr or build.stdout)[-2000:], checks)
        if build.returncode:
            return checks
        artifacts = sorted(output.iterdir())
        wheel = next((path for path in artifacts if path.suffix == ".whl"), None)
        sdist = next((path for path in artifacts if path.name.endswith(".tar.gz")), None)
        _check("wheel_and_sdist_created", wheel is not None and sdist is not None, ", ".join(path.name for path in artifacts), checks)
        if wheel is None or sdist is None:
            return checks
        wheel_names = _artifact_names(wheel)
        required_wheel = {
            "homemaster/alfworld/harness.py",
            "homemaster/alfworld/worker_client.py",
            "homemaster/alfworld/alfworld_tasksets.yaml",
            "homemaster/alfworld/object_vocabulary.json",
        }
        _check("wheel_package_data", required_wheel <= wheel_names, ", ".join(sorted(required_wheel - wheel_names)), checks)
        sdist_names = _artifact_names(sdist)
        required_sdist = {
            name
            for name in sdist_names
            if name.endswith(("/uv.lock", "/config/alfworld/requirements.lock", "/protocols/alfworld-v1.schema.json"))
        }
        _check("sdist_release_inputs", len(required_sdist) == 3, ", ".join(sorted(required_sdist)), checks)

        probe_env = dict(os.environ)
        probe_env["PYTHONPATH"] = str(wheel)
        probe = _run([sys.executable, "-c", "import homemaster.alfworld; print('ok')"], cwd=Path(temp), env=probe_env)
        _check("wheel_alfworld_import", probe.returncode == 0 and probe.stdout.strip() == "ok", (probe.stderr or probe.stdout).strip(), checks)

        worker_root = ROOT / "workers" / "alfworld_worker"
        worker_probe = _run(
            [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import protocol; assert not any(name.startswith('homemaster') for name in sys.modules); print('ok')", str(worker_root)]
        )
        _check("worker_protocol_import_isolated", worker_probe.returncode == 0 and worker_probe.stdout.strip() == "ok", (worker_probe.stderr or worker_probe.stdout).strip(), checks)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    checks = audit()
    payload = {"status": "PASS" if all(item["status"] == "PASS" for item in checks) else "FAIL", "checks": checks}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for item in checks:
            print(f"{item['status']:<4} {item['name']}: {item['detail']}")
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
