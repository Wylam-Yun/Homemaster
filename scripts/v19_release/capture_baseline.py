#!/usr/bin/env python3
"""Capture the sanitized V1.9 section 24.2 behavior baseline."""

from __future__ import annotations

import argparse
import dataclasses
import os
import platform
import re
import subprocess
import sys
import tempfile
from math import log2
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homemaster.adapters import build_environment_profiles
from homemaster.config import load_config
from homemaster.providers.attempts import (
    AttemptCommitState,
    OutboundImageBinding,
    OutboundObservationBinding,
    ProviderAttemptRecord,
)
from scripts.v19_release._common import (
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    write_canonical_json,
)

NONLIVE_EXPRESSION = "not live_api and not live_alfworld and not live_mcp and not live_coworker"
HOMEMASTER_BASELINE_COMMIT = "5b150a9671bb087b32ed57971a39fa472e8ff1e1"
OPENHARNESS_BASELINE_COMMIT = "9b2efd795c6aa09f88b0c257d269a9e518da6ae7"
BASELINE_FILES = {
    "source-commits.json",
    "dependency-lock-hashes.json",
    "test-inventory.txt",
    "pytest-nonlive.txt",
    "tool-surfaces.json",
    "provider-attempt-contract.json",
    "alfworld-contract-hashes.json",
    "coworker-contract-hashes.json",
}
ALFWORLD_CONTRACT_PATHS = (
    "config/alfworld_v18_regression_trials.json",
    "src/homemaster/benchmarking/alfworld/execution.py",
    "src/homemaster/benchmarking/alfworld/gateway.py",
    "src/homemaster/benchmarking/alfworld/model_view.py",
    "src/homemaster/benchmarking/alfworld/reset_transaction.py",
    "src/homemaster/benchmarking/alfworld/runtime_contract.py",
    "src/homemaster/benchmarking/alfworld/tracing.py",
    "src/homemaster/benchmarking/alfworld/trial_selection.py",
    "src/homemaster/benchmarking/alfworld/types.py",
)
COWORKER_CONTRACT_PATHS = (
    "apps/case02_openenv/src/case02_openenv/artifacts.py",
    "apps/case02_openenv/src/case02_openenv/episode_store.py",
    "apps/case02_openenv/src/case02_openenv/evaluation/scoring.py",
    "apps/case02_openenv/src/case02_openenv/evaluation/trajectory.py",
    "apps/case02_openenv/src/case02_openenv/presentation_models.py",
    "data/coworker_demo/case_02/agent_trajectory_ground_truth.yaml",
    "data/coworker_demo/case_02/dataset_manifest.json",
    "data/coworker_demo/case_02/test_set/item_change_ticket.json",
    "scripts/coworker_demo/verify_dataset_bundle.py",
    "scripts/coworker_demo/verify_run_bundle.py",
    "src/homemaster/benchmarking/coworker_demo/presentation.py",
    "src/homemaster/benchmarking/coworker_demo/registry.py",
    "src/homemaster/benchmarking/coworker_demo/types.py",
)
_SECRET_ENV_KEY_PARTS = (
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "client_id",
    "connection_string",
    "cookie",
    "credential",
    "database_url",
    "dsn",
    "password",
    "private_key",
    "secret",
    "signed_url",
    "token",
)
_BEARER_SECRET = re.compile(r"(?i)(\bBearer\s+)[^\s,;\]}]+")
_LABELED_SECRET = re.compile(
    r"(?ix)"
    r"(?P<label>[\"']?(?:api[_-]?keys?|apikey|authorization|x-api-key|"
    r"auth[_-]?token|access[_-]?(?:key(?:_id)?|token)|aws[_-]?(?:access[_-]?key[_-]?id|"
    r"secret[_-]?access[_-]?key)|token|credential|secret|password|private[_-]?key|cookie|"
    r"database[_-]?url|connection[_-]?string|client[_-]?id|dsn|signed[_-]?url)[\"']?"
    r"\s*[:=]\s*)"
    r"[^\r\n]*"
)
_OPENAI_STYLE_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{4,}\b")
_AWS_ACCESS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_JWT_SECRET = re.compile(
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"
)
_URL_CREDENTIALS = re.compile(
    r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"
)
_SIGNED_QUERY_VALUE = re.compile(
    r"(?i)([?&](?:x-amz-signature|signature|sig|access_token|token)=)[^&#\s]+"
)
_PEM_MATERIAL = re.compile(
    r"-----BEGIN [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----.*?"
    r"(?:-----END [A-Z0-9 ]*(?:PRIVATE KEY|CERTIFICATE)-----|\Z)",
    re.DOTALL,
)
_SSH_KEY = re.compile(
    r"(?i)(\bssh-(?:rsa|ed25519|ecdsa-[^\s]+)\s+)[A-Za-z0-9+/=]{24,}"
)
_TOKEN_CANDIDATE = re.compile(r"\b[A-Za-z0-9_+/=-]{24,}\b")


def capture_baseline(
    *,
    repo_root: Path,
    openharness_root: Path,
    output_dir: Path,
    run_tests: bool,
) -> int:
    repo_root = repo_root.resolve(strict=True)
    openharness_root = openharness_root.resolve(strict=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    _verify_locked_sources(repo_root=repo_root, openharness_root=openharness_root)
    sensitive_values = _known_sensitive_values(repo_root)

    write_canonical_json(
        output_dir / "source-commits.json",
        {
            "schema_version": "homemaster-v1.9-baseline-sources-v1",
            "homemaster_commit": HOMEMASTER_BASELINE_COMMIT,
            "openharness_commit": OPENHARNESS_BASELINE_COMMIT,
            "production_roots_match_homemaster_commit": True,
            "python": {
                "implementation": platform.python_implementation(),
                "version": platform.python_version(),
            },
            "uv_version": _command_text(["uv", "--version"]),
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
        },
    )
    write_canonical_json(
        output_dir / "dependency-lock-hashes.json",
        {
            "schema_version": "homemaster-v1.9-baseline-locks-v1",
            "locks": {
                "uv.lock": sha256_file(repo_root / "uv.lock"),
                "apps/case02_openenv/uv.lock": sha256_file(
                    repo_root / "apps/case02_openenv/uv.lock"
                ),
            },
        },
    )
    write_canonical_json(output_dir / "tool-surfaces.json", _tool_surfaces())
    write_canonical_json(
        output_dir / "provider-attempt-contract.json", _provider_attempt_contract()
    )
    write_canonical_json(
        output_dir / "alfworld-contract-hashes.json",
        _contract_hashes(repo_root, "alfworld", ALFWORLD_CONTRACT_PATHS),
    )
    write_canonical_json(
        output_dir / "coworker-contract-hashes.json",
        _contract_hashes(repo_root, "coworker", COWORKER_CONTRACT_PATHS),
    )

    collect = _run_pytest(repo_root, "--collect-only", "-q")
    _assert_no_sensitive_values(
        collect.stdout,
        sensitive_values=sensitive_values,
        label="pytest collection",
    )
    inventory = sorted(
        {
            line.strip()
            for line in collect.stdout.splitlines()
            if line.startswith("tests/") and "::" in line
        }
    )
    if collect.returncode != 0 or not inventory:
        raise RuntimeError("non-live pytest collection failed")
    (output_dir / "test-inventory.txt").write_text("\n".join(inventory) + "\n", encoding="utf-8")

    if run_tests:
        test_run = _run_pytest(repo_root, "-q")
        sanitized = _sanitize_output(
            test_run.stdout,
            repo_root=repo_root,
            sensitive_values=sensitive_values,
        )
        evidence = (
            f"command: <PYTHON> -m pytest -q -m {NONLIVE_EXPRESSION!r}\n"
            f"exit_code: {test_run.returncode}\n"
            "output:\n"
            f"{sanitized.rstrip()}\n"
        )
        (output_dir / "pytest-nonlive.txt").write_text(evidence, encoding="utf-8")
        return test_run.returncode

    (output_dir / "pytest-nonlive.txt").write_text(
        "command: not run\nexit_code: UNVERIFIED\n",
        encoding="utf-8",
    )
    return 0


def _tool_surfaces() -> dict[str, Any]:
    profiles = {}
    for name, profile in build_environment_profiles().items():
        tools = []
        for registered in profile.view.list_tools():
            definition = registered.definition
            snapshot = definition.to_dict()
            tools.append(
                {
                    "name": definition.model_alias,
                    "selectable_by_model": True,
                    "executor_mode": definition.execution_backend.value,
                    "requires_verification": (
                        definition.verification_policy.execution_proof.value != "none"
                    ),
                    "input_schema_sha256": sha256_bytes(
                        canonical_json_bytes(snapshot["input_schema"])
                    ),
                    "output_schema_sha256": sha256_bytes(
                        canonical_json_bytes(snapshot["output_schema"])
                    ),
                    "model_manifest_sha256": sha256_bytes(
                        canonical_json_bytes(definition.to_model_manifest())
                    ),
                }
            )
        profiles[name] = {
            "ordered_tool_names": list(profile.model_tool_names),
            "tools": tools,
        }
    return {"schema_version": "homemaster-v1.9-baseline-tool-surfaces-v1", "profiles": profiles}


def _provider_attempt_contract() -> dict[str, Any]:
    contracts = {}
    for model in (
        OutboundImageBinding,
        OutboundObservationBinding,
        ProviderAttemptRecord,
        AttemptCommitState,
    ):
        contracts[model.__name__] = [
            {"name": field.name, "type": str(field.type)} for field in dataclasses.fields(model)
        ]
    return {
        "schema_version": "homemaster-v1.9-baseline-provider-attempt-v1",
        "contracts": contracts,
        "commit_order": [
            "provider_attempt_recorded",
            "assistant_committed",
            "tool_dispatch_committed",
            "external_action_committed",
        ],
    }


def _contract_hashes(repo_root: Path, domain: str, paths: tuple[str, ...]) -> dict[str, Any]:
    files = {}
    for relative in paths:
        path = repo_root / relative
        if not path.is_file():
            raise RuntimeError(f"missing {domain} baseline owner: {relative}")
        files[relative] = sha256_file(path)
    return {
        "schema_version": f"homemaster-v1.9-baseline-{domain}-contracts-v1",
        "files": files,
        "aggregate_sha256": sha256_bytes(canonical_json_bytes(files)),
    }


def _run_pytest(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args, "-m", NONLIVE_EXPRESSION],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        text=True,
        stderr=subprocess.STDOUT,
    )


def _sanitize_output(
    value: str,
    *,
    repo_root: Path,
    sensitive_values: tuple[str, ...] = (),
) -> str:
    sanitized = value.replace(str(repo_root), "<REPO>")
    sanitized = sanitized.replace(str(Path.home()), "<HOME>")
    sanitized = sanitized.replace(tempfile.gettempdir(), "<TMP>")
    for secret in sorted(set(sensitive_values), key=len, reverse=True):
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = _PEM_MATERIAL.sub("[REDACTED PEM MATERIAL]", sanitized)
    sanitized = _SSH_KEY.sub(r"\1[REDACTED]", sanitized)
    sanitized = _BEARER_SECRET.sub(r"\1[REDACTED]", sanitized)
    sanitized = _LABELED_SECRET.sub(r"\g<label>[REDACTED]", sanitized)
    sanitized = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", sanitized)
    sanitized = _SIGNED_QUERY_VALUE.sub(r"\1[REDACTED]", sanitized)
    sanitized = _JWT_SECRET.sub("[REDACTED]", sanitized)
    sanitized = _AWS_ACCESS_KEY.sub("[REDACTED]", sanitized)
    sanitized = _OPENAI_STYLE_SECRET.sub("[REDACTED]", sanitized)
    sanitized = _TOKEN_CANDIDATE.sub(_redact_high_entropy_token, sanitized)
    return re.sub(r"\bin \d+(?:\.\d+)?s\b", "in <DURATION>", sanitized)


def _redact_high_entropy_token(match: re.Match[str]) -> str:
    token = match.group(0)
    classes = sum(
        any(predicate(character) for character in token)
        for predicate in (str.islower, str.isupper, str.isdigit)
    )
    counts = {character: token.count(character) for character in set(token)}
    entropy = -sum(
        (count / len(token)) * log2(count / len(token)) for count in counts.values()
    )
    return "[REDACTED]" if classes >= 3 and entropy >= 4.0 else token


def _sensitive_environment_values() -> tuple[str, ...]:
    return tuple(
        value
        for key, value in os.environ.items()
        if value and any(part in key.casefold() for part in _SECRET_ENV_KEY_PARTS)
    )


def _known_sensitive_values(repo_root: Path) -> tuple[str, ...]:
    config_path = repo_root / "config/homemaster.yaml"
    configured = (
        api_key
        for provider in load_config(config_path).providers.items
        for api_key in provider.api_keys
    )
    return tuple(sorted(set((*_sensitive_environment_values(), *configured))))


def _assert_no_sensitive_values(
    value: str,
    *,
    sensitive_values: tuple[str, ...],
    label: str,
) -> None:
    if any(secret and secret in value for secret in sensitive_values):
        raise RuntimeError(f"{label} contains a configured secret")


def _verify_locked_sources(*, repo_root: Path, openharness_root: Path) -> None:
    resolved_home = _git(
        repo_root,
        "rev-parse",
        "--verify",
        f"{HOMEMASTER_BASELINE_COMMIT}^{{commit}}",
    )
    if resolved_home != HOMEMASTER_BASELINE_COMMIT:
        raise RuntimeError("HomeMaster baseline commit is unavailable or abbreviated")
    openharness_head = _git(openharness_root, "rev-parse", "HEAD")
    if openharness_head != OPENHARNESS_BASELINE_COMMIT:
        raise RuntimeError("OpenHarness HEAD drifted from the locked baseline commit")
    untracked = _git(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "src/homemaster",
        "apps/case02_openenv/src",
    )
    if untracked:
        raise RuntimeError("production roots contain untracked source")
    ignored = _git(
        repo_root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--",
        "src/homemaster",
        "apps/case02_openenv/src",
    )
    unexpected_ignored = [
        path
        for path in ignored.splitlines()
        if "/__pycache__/" not in path and ".egg-info/" not in path
    ]
    if unexpected_ignored:
        raise RuntimeError("production roots contain ignored non-generated files")
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "diff",
            "--exit-code",
            HOMEMASTER_BASELINE_COMMIT,
            "--",
            "src/homemaster",
            "apps/case02_openenv/src",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError("production roots drifted from the locked HomeMaster baseline commit")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _command_text(command: list[str]) -> str:
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--openharness-root", type=Path, default=Path("../OpenHarness"))
    parser.add_argument("--output-dir", type=Path, default=Path("plan/V1.9/baseline"))
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()
    try:
        return capture_baseline(
            repo_root=args.repo_root,
            openharness_root=args.openharness_root,
            output_dir=args.output_dir,
            run_tests=not args.skip_tests,
        )
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"baseline capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
