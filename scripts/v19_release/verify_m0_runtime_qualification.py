#!/usr/bin/env python3
"""Independent, stdlib-only verifier for an M0 ALFWorld qualification bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "homemaster-v1.9-m0-runtime-qualification-v1"
RESET_SCHEMA_VERSION = "alfworld-reset-transaction-v1"
ATTEMPT_KEYS = {
    "cause_code",
    "error_type",
    "model_attempt_id",
    "outbound_images",
    "outbound_observations",
    "request_sha256",
    "response_completed",
    "stripped_images",
}
OUTBOUND_IMAGE_KEYS = {
    "block_index",
    "content_sha256",
    "frame_binding_id",
    "message_index",
    "observation_backend_id",
    "observation_capture_event_sequence",
    "observation_content_sha256",
    "observation_generation",
    "observation_id",
    "observation_pixel_sha256",
    "observation_run_id",
    "observation_state_sequence",
}
OUTBOUND_OBSERVATION_KEYS = {
    "block_index",
    "content_sha256",
    "media_type",
    "message_index",
    "observation_backend_id",
    "observation_capture_event_sequence",
    "observation_content_sha256",
    "observation_generation",
    "observation_id",
    "observation_pixel_sha256",
    "observation_run_id",
    "observation_state_sequence",
}
ROOT_KEYS = {
    "artifacts",
    "candidate",
    "canary",
    "environment",
    "inputs",
    "schema_version",
    "status",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def verify_qualification(
    report_path: Path,
    *,
    artifact_root: Path,
    expected_candidate: str | None = None,
) -> dict[str, Any]:
    root = artifact_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("artifact root must be a directory")
    report = _read_object(report_path, label="qualification report")
    _exact_keys(report, ROOT_KEYS, label="qualification report")
    if report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported qualification schema")
    if report["status"] != "PASS":
        raise ValueError("qualification status is not PASS")

    candidate = _object(report["candidate"], label="candidate")
    _exact_keys(
        candidate,
        {"alfworld_commit", "homemaster_commit", "openharness_commit"},
        label="candidate",
    )
    for name, value in candidate.items():
        if not isinstance(value, str) or COMMIT_RE.fullmatch(value) is None:
            raise ValueError(f"candidate {name} must be a full Git commit")
    if expected_candidate is not None and candidate["homemaster_commit"] != expected_candidate:
        raise ValueError("qualification candidate does not match expected commit")

    artifact_hashes = _verify_artifacts(report["artifacts"], root=root)
    inputs = _verify_inputs(report["inputs"], root=root, hashes=artifact_hashes)
    _verify_environment(report["environment"])
    attempt_count = _verify_canary(
        report["canary"],
        root=root,
        hashes=artifact_hashes,
        selected_trial=inputs["selected_trial"],
    )
    return {
        "status": "PASS",
        "candidate": candidate["homemaster_commit"],
        "artifact_count": len(artifact_hashes),
        "provider_attempt_count": attempt_count,
        "selected_trial": inputs["selected_trial"]["trial_id"],
    }


def _verify_artifacts(value: Any, *, root: Path) -> dict[str, str]:
    artifacts = _object(value, label="artifacts")
    if not artifacts:
        raise ValueError("artifacts must not be empty")
    verified: dict[str, str] = {}
    for ref, expected in sorted(artifacts.items()):
        path = _artifact_path(root, ref)
        digest = _sha256(expected, label=f"artifact hash for {ref}")
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"artifact is missing or is a symlink: {ref}")
        if _sha256_file(path) != digest:
            raise ValueError(f"artifact hash mismatch: {ref}")
        verified[ref] = digest
    return verified


def _verify_inputs(value: Any, *, root: Path, hashes: dict[str, str]) -> dict[str, Any]:
    inputs = _object(value, label="inputs")
    _exact_keys(
        inputs,
        {
            "alfworld_config_sha256",
            "alfworld_config_ref",
            "dataset_identity_sha256",
            "release_manifest_ref",
            "release_manifest_sha256",
            "selected_trial",
            "source_manifest_ref",
            "source_manifest_sha256",
            "trial_bytes_ref",
        },
        label="inputs",
    )
    release_ref = _required_artifact_ref(inputs["release_manifest_ref"], hashes)
    source_ref = _required_artifact_ref(inputs["source_manifest_ref"], hashes)
    trial_ref = _required_artifact_ref(inputs["trial_bytes_ref"], hashes)
    config_ref = _required_artifact_ref(inputs["alfworld_config_ref"], hashes)
    if _sha256(inputs["release_manifest_sha256"], label="release manifest hash") != hashes[
        release_ref
    ]:
        raise ValueError("release manifest hash is not bound to its artifact")
    if _sha256(inputs["source_manifest_sha256"], label="source manifest hash") != hashes[
        source_ref
    ]:
        raise ValueError("source manifest hash is not bound to its artifact")
    if _sha256(inputs["alfworld_config_sha256"], label="ALFWorld config hash") != hashes[
        config_ref
    ]:
        raise ValueError("ALFWorld config hash is not bound to its artifact")
    _sha256(inputs["dataset_identity_sha256"], label="dataset identity hash")

    release = _read_object(_artifact_path(root, release_ref), label="release manifest")
    source = _read_object(_artifact_path(root, source_ref), label="source manifest")
    entries = release.get("entries")
    if release.get("schema_version") != "alfworld-v19-release-trials-v1":
        raise ValueError("release manifest schema drifted")
    if not isinstance(entries, list) or len(entries) != 4:
        raise ValueError("release manifest must contain exactly four entries")
    if release.get("source", {}).get("sha256") != hashes[source_ref]:
        raise ValueError("release manifest does not bind the source artifact")
    selection = release.get("selection")
    if selection != {
        "algorithm": "sha256-rank-v1",
        "count": 4,
        "seed": "homemaster-v1.9-release",
    }:
        raise ValueError("release selection policy drifted")
    if source.get("schema_version") != "alfworld-trial-selection-v1":
        raise ValueError("source manifest schema drifted")

    selected = _object(inputs["selected_trial"], label="selected trial")
    _exact_keys(
        selected,
        {"expected_logical_scene", "goal_fingerprint", "trial_id", "trial_sha256"},
        label="selected trial",
    )
    first = _object(entries[0], label="first release entry")
    for key in selected:
        if selected[key] != first.get(key):
            raise ValueError("canary trial is not the first locked release entry")
    if _sha256(selected["trial_sha256"], label="selected trial hash") != hashes[trial_ref]:
        raise ValueError("selected trial bytes are not bound to the selected identity")
    _sha256(selected["goal_fingerprint"], label="selected goal fingerprint")
    if not isinstance(selected["trial_id"], str) or not selected["trial_id"]:
        raise ValueError("selected trial id is missing")
    return inputs


def _verify_environment(value: Any) -> None:
    environment = _object(value, label="environment")
    _exact_keys(
        environment,
        {
            "display",
            "conda",
            "gpu",
            "hostname",
            "imports",
            "provider",
            "python_executable",
            "python_version",
            "site",
            "unity",
        },
        label="environment",
    )
    site = environment["site"]
    if site not in {"hpc2", "hkust4"}:
        raise ValueError("qualification site is invalid")
    for key in ("hostname", "python_executable", "python_version"):
        if not isinstance(environment[key], str) or not environment[key]:
            raise ValueError(f"environment {key} is missing")
    display = _object(environment["display"], label="display")
    if display.get("available") is not True or not isinstance(display.get("name"), str):
        raise ValueError("display preflight did not pass")
    gpu = _object(environment["gpu"], label="GPU")
    if not isinstance(gpu.get("name"), str) or not gpu["name"]:
        raise ValueError("GPU identity is missing")
    if not isinstance(gpu.get("driver_version"), str) or not gpu["driver_version"]:
        raise ValueError("GPU driver identity is missing")
    provider = _object(environment["provider"], label="provider")
    if provider.get("non_mock") is not True:
        raise ValueError("provider is not qualified as non-mock")
    for key in ("name", "model"):
        if not isinstance(provider.get(key), str) or not provider[key]:
            raise ValueError(f"provider {key} is missing")

    imports = _object(environment["imports"], label="imports")
    if set(imports) != {"ai2thor", "alfworld", "homemaster"}:
        raise ValueError("required import identities differ")
    for package, raw_identity in imports.items():
        identity = _object(raw_identity, label=f"{package} import")
        if identity.get("status") != "present":
            raise ValueError(f"{package} import is unavailable")
        if not isinstance(identity.get("origin"), str) or not identity["origin"]:
            raise ValueError(f"{package} import origin is missing")
        if not isinstance(identity.get("version"), str) or not identity["version"]:
            raise ValueError(f"{package} version is missing")
    if imports["homemaster"].get("origin_within_candidate") is not True:
        raise ValueError("homemaster import is outside the candidate")
    conda = _object(environment["conda"], label="Conda identity")
    _exact_keys(
        conda,
        {"environment_name", "explicit_sha256", "prefix"},
        label="Conda identity",
    )
    if site == "hkust4":
        if conda["environment_name"] != "hm_alfworld":
            raise ValueError("hkust4 Conda environment is not hm_alfworld")
        if not isinstance(conda["prefix"], str) or not conda["prefix"]:
            raise ValueError("hkust4 Conda prefix is missing")
        _sha256(conda["explicit_sha256"], label="Conda explicit package hash")
        for package in ("alfworld", "ai2thor"):
            if imports[package].get("origin_within_conda_prefix") is not True:
                raise ValueError(f"{package} import is outside hm_alfworld")
    else:
        if any(conda[key] is not None for key in conda):
            raise ValueError("HPC2 qualification has unexpected Conda identity")
        if imports["alfworld"].get("origin_within_locked_source") is not True:
            raise ValueError("alfworld import is outside the locked source")

    unity = _object(environment["unity"], label="Unity build")
    if unity.get("platform") != "Linux":
        raise ValueError("Unity build platform is not Linux")
    if not isinstance(unity.get("version"), str) or not unity["version"]:
        raise ValueError("Unity build version is missing")
    expected = _sha256(unity.get("expected_sha256"), label="expected Unity hash")
    if _sha256(unity.get("verified_sha256"), label="verified Unity hash") != expected:
        raise ValueError("Unity build hash was not verified")


def _verify_canary(
    value: Any,
    *,
    root: Path,
    hashes: dict[str, str],
    selected_trial: dict[str, Any],
) -> int:
    canary = _object(value, label="canary")
    _exact_keys(
        canary,
        {
            "env_type",
            "frame_ref",
            "fresh",
            "observation_mode",
            "provider_attempt_count",
            "provider_attempts_ref",
            "reset_ref",
            "scorer_available",
            "split",
            "summary_ref",
        },
        label="canary",
    )
    if (
        canary["env_type"] != "AlfredThorEnv"
        or canary["split"] != "valid_unseen"
        or canary["observation_mode"] != "visual_eval"
        or canary["fresh"] is not True
    ):
        raise ValueError("canary runtime profile is not the locked fresh visual profile")
    reset_ref = _required_artifact_ref(canary["reset_ref"], hashes)
    frame_ref = _required_artifact_ref(canary["frame_ref"], hashes)
    attempts_ref = _required_artifact_ref(canary["provider_attempts_ref"], hashes)
    summary_ref = _required_artifact_ref(canary["summary_ref"], hashes)

    reset = _read_object(_artifact_path(root, reset_ref), label="reset transaction")
    if reset.get("schema_version") != RESET_SCHEMA_VERSION or reset.get("ready") is not True:
        raise ValueError("reset transaction did not pass")
    if reset.get("goal_trial_fingerprint") != selected_trial["goal_fingerprint"]:
        raise ValueError("reset goal fingerprint does not match the selected trial")
    for key in ("scene_reset_fingerprint", "snapshot_sha256"):
        _sha256(reset.get(key), label=f"reset {key}")
    actions = reset.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("reset transaction has no scan/restore actions")
    action_names = [row.get("request", {}).get("payload", {}).get("action") for row in actions]
    if "GetReachablePositions" not in action_names or "TeleportFull" not in action_names:
        raise ValueError("reset transaction does not prove scan and restore")
    if action_names[-1] != "ChangeTimeScale":
        raise ValueError("reset transaction did not restore normal time")
    initial = _object(reset.get("initial_event"), label="reset initial event")
    _sha256(initial.get("frame_sha256"), label="reset frame hash")
    reset_parent = PurePosixPath(reset_ref).parent
    event_files = reset.get("event_files")
    if not isinstance(event_files, list) or not event_files:
        raise ValueError("reset transaction has no raw event evidence")
    for event_ref in event_files:
        if not isinstance(event_ref, str):
            raise ValueError("reset event reference is invalid")
        _required_artifact_ref((reset_parent / event_ref).as_posix(), hashes)
    initial_ref = initial.get("event_ref")
    if not isinstance(initial_ref, str) or initial_ref not in event_files:
        raise ValueError("reset initial event is not bound to raw evidence")
    initial_evidence = _read_object(
        _artifact_path(root, (reset_parent / initial_ref).as_posix()),
        label="raw reset event",
    )
    encoded_frame = initial_evidence.get("raw_frame_base64")
    if not isinstance(encoded_frame, str) or not encoded_frame:
        raise ValueError("raw reset event does not contain frame bytes")
    try:
        raw_frame = base64.b64decode(encoded_frame, validate=True)
    except ValueError as exc:
        raise ValueError("raw reset frame is not valid base64") from exc
    if hashlib.sha256(raw_frame).hexdigest() != initial["frame_sha256"]:
        raise ValueError("raw reset frame hash mismatch")
    snapshot_ref = reset.get("snapshot_ref")
    if not isinstance(snapshot_ref, str):
        raise ValueError("reset snapshot reference is missing")
    snapshot_artifact = (reset_parent / snapshot_ref).as_posix()
    _required_artifact_ref(snapshot_artifact, hashes)
    snapshot = _read_object(_artifact_path(root, snapshot_artifact), label="pose snapshot")
    if snapshot.get("snapshot_sha256") != reset["snapshot_sha256"]:
        raise ValueError("reset snapshot identity is inconsistent")

    frame = _artifact_path(root, frame_ref).read_bytes()
    if len(frame) < 16 or not frame.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("explicit captured frame is not a non-empty PNG")

    attempts = _read_jsonl(_artifact_path(root, attempts_ref), label="provider attempts")
    if not attempts:
        raise ValueError("provider attempt ledger is empty")
    outbound_observation_count = 0
    for index, attempt in enumerate(attempts):
        _exact_keys(attempt, ATTEMPT_KEYS, label=f"provider attempt {index}")
        _sha256(attempt["request_sha256"], label=f"provider attempt {index} request hash")
        if not isinstance(attempt["model_attempt_id"], str) or not attempt["model_attempt_id"]:
            raise ValueError("provider attempt id is missing")
        if not isinstance(attempt["response_completed"], bool):
            raise ValueError("provider response completion flag is invalid")
        if not isinstance(attempt["outbound_images"], list):
            raise ValueError("provider outbound image bindings are invalid")
        for image in attempt["outbound_images"]:
            binding = _object(image, label="provider outbound image")
            _exact_keys(binding, OUTBOUND_IMAGE_KEYS, label="provider outbound image")
            _sha256(binding["content_sha256"], label="provider outbound image hash")
        observations = attempt["outbound_observations"]
        if not isinstance(observations, list):
            raise ValueError("provider outbound observation bindings are invalid")
        for observation in observations:
            binding = _object(observation, label="provider outbound observation")
            _exact_keys(
                binding,
                OUTBOUND_OBSERVATION_KEYS,
                label="provider outbound observation",
            )
            content_hash = _sha256(
                binding["content_sha256"],
                label="provider outbound observation hash",
            )
            if binding["observation_content_sha256"] != content_hash:
                raise ValueError("provider observation content hash drifted")
            if binding["media_type"] != "image/png":
                raise ValueError("canary outbound observation is not PNG")
            outbound_observation_count += 1
    if outbound_observation_count <= 0:
        raise ValueError("provider attempts contain no explicit outbound observation binding")
    declared_count = canary["provider_attempt_count"]
    if not isinstance(declared_count, int) or isinstance(declared_count, bool):
        raise ValueError("provider attempt count must be an integer")
    if declared_count != len(attempts) or declared_count <= 0:
        raise ValueError("provider attempt count is inconsistent or zero")

    summary = _read_object(_artifact_path(root, summary_ref), label="canary summary")
    if summary.get("episode_count") != 1:
        raise ValueError("canary summary must contain exactly one episode")
    if summary.get("formal_score_available") is not True or canary["scorer_available"] is not True:
        raise ValueError("formal scorer is unavailable")
    config = _object(summary.get("config"), label="canary summary config")
    if config.get("env_type") != "AlfredThorEnv" or config.get("observation_mode") != "visual_eval":
        raise ValueError("summary runtime profile drifted")
    return len(attempts)


def _required_artifact_ref(value: Any, hashes: dict[str, str]) -> str:
    if not isinstance(value, str) or value not in hashes:
        raise ValueError(f"required artifact reference is missing: {value!r}")
    return value


def _artifact_path(root: Path, ref: Any) -> Path:
    if not isinstance(ref, str) or not ref or "\\" in ref or "\x00" in ref:
        raise ValueError("artifact reference must be a POSIX relative path")
    path = PurePosixPath(ref)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe artifact reference: {ref}")
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"artifact reference escapes the bundle: {ref}")
    return resolved


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    return _object(value, label=label)


def _read_jsonl(path: Path, *, label: str) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        values = [json.loads(line) for line in lines if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {path}") from exc
    return [_object(value, label=f"{label} row") for value in values]


def _object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} keys differ")


def _sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--expected-candidate")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = verify_qualification(
            args.report,
            artifact_root=args.artifact_root,
            expected_candidate=args.expected_candidate,
        )
    except ValueError as exc:
        print(f"M0 qualification verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
