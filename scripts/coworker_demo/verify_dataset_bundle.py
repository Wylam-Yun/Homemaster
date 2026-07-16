"""Independent byte/hash/count verifier for a Hawkeye case bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class DatasetVerificationError(RuntimeError):
    """Raised when a dataset bundle violates its manifest contract."""


@dataclass(frozen=True)
class DatasetVerificationReport:
    dataset_id: str
    bundle_root: str
    declared_file_count: int
    verified_file_count: int
    record_counts: dict[str, int]


def verify_bundle(bundle_root: Path) -> DatasetVerificationReport:
    root = bundle_root.expanduser().resolve()
    if not root.is_dir():
        raise DatasetVerificationError(f"bundle root is not a directory: {root}")

    manifest_path = root / "dataset_manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise DatasetVerificationError("dataset manifest must be a JSON object")

    contract = manifest.get("contract")
    if not isinstance(contract, dict):
        raise DatasetVerificationError("dataset manifest contract must be an object")
    declared_hashes = contract.get("file_sha256")
    if not isinstance(declared_hashes, dict) or not declared_hashes:
        raise DatasetVerificationError("dataset manifest file_sha256 must be non-empty")

    resolved_files: dict[str, Path] = {}
    for raw_relative_path, raw_expected_hash in sorted(declared_hashes.items()):
        relative_path = _relative_path(raw_relative_path)
        path = _resolve_contained(root, relative_path)
        if not path.is_file():
            raise DatasetVerificationError(f"missing declared file: {relative_path}")
        expected_hash = str(raw_expected_hash).lower()
        if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
            raise DatasetVerificationError(f"invalid declared sha256: {relative_path}")
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            raise DatasetVerificationError(
                f"sha256 mismatch for {relative_path}: expected {expected_hash}, got {actual_hash}"
            )
        resolved_files[relative_path] = path

    data_files = manifest.get("data_files")
    if not isinstance(data_files, dict):
        raise DatasetVerificationError("dataset manifest data_files must be an object")
    _verify_manifest_references(
        root=root,
        declared_files=resolved_files,
        input_ticket=manifest.get("input_ticket"),
        data_files=data_files,
    )

    expected_counts = manifest.get("record_counts")
    if not isinstance(expected_counts, dict):
        raise DatasetVerificationError("dataset manifest record_counts must be an object")
    observed_counts: dict[str, int] = {}
    for count_name, raw_expected_count in expected_counts.items():
        expected_count = _nonnegative_int(raw_expected_count, field=str(count_name))
        observed_count = _record_count(
            root=root,
            data_files=data_files,
            count_name=str(count_name),
        )
        if observed_count != expected_count:
            raise DatasetVerificationError(
                f"record count mismatch for {count_name}: "
                f"expected {expected_count}, got {observed_count}"
            )
        observed_counts[str(count_name)] = observed_count

    return DatasetVerificationReport(
        dataset_id=str(manifest.get("dataset_id") or ""),
        bundle_root=str(root),
        declared_file_count=len(declared_hashes),
        verified_file_count=len(resolved_files),
        record_counts=observed_counts,
    )


def _verify_manifest_references(
    *,
    root: Path,
    declared_files: dict[str, Path],
    input_ticket: Any,
    data_files: dict[str, Any],
) -> None:
    references = {"input_ticket": input_ticket, **data_files}
    for name, raw_relative_path in references.items():
        relative_path = _relative_path(raw_relative_path)
        path = _resolve_contained(root, relative_path)
        if relative_path not in declared_files:
            raise DatasetVerificationError(
                f"manifest reference is missing from file_sha256: {name}={relative_path}"
            )
        if declared_files[relative_path] != path:
            raise DatasetVerificationError(f"manifest reference resolution mismatch: {name}")


def _record_count(*, root: Path, data_files: dict[str, Any], count_name: str) -> int:
    resolvers: dict[str, Callable[[Any], int]] = {
        "tool_catalog": _list_length,
        "tool_location_mapping": _list_length,
        "monitor_cls_query_request": _list_length,
        "operation_access_log": _list_length,
        "validation_chain_ground_truth": _list_length,
        "mcp_query_config.change_time_window": _change_window_count,
        "black_screen_output": _black_screen_count,
    }
    data_file_keys = {
        "tool_catalog": "tool_catalog",
        "tool_location_mapping": "tool_location_mapping",
        "monitor_cls_query_request": "mcp_request_samples",
        "operation_access_log": "mcp_response_logs",
        "validation_chain_ground_truth": "ground_truth",
        "mcp_query_config.change_time_window": "mcp_query_config",
        "black_screen_output": "black_screen_output",
    }
    resolver = resolvers.get(count_name)
    data_file_key = data_file_keys.get(count_name)
    if resolver is None or data_file_key is None:
        raise DatasetVerificationError(f"unsupported record count: {count_name}")
    if data_file_key not in data_files:
        raise DatasetVerificationError(f"missing data_files entry: {data_file_key}")
    relative_path = _relative_path(data_files[data_file_key])
    payload = _read_json(_resolve_contained(root, relative_path))
    return resolver(payload)


def _list_length(payload: Any) -> int:
    if not isinstance(payload, list):
        raise DatasetVerificationError("record-count payload must be a JSON array")
    return len(payload)


def _change_window_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise DatasetVerificationError("mcp query config must be a JSON object")
    value = payload.get("change_time_window")
    if not isinstance(value, dict) or not value:
        return 0
    return 1


def _black_screen_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        raise DatasetVerificationError("black screen output must be a JSON object")
    records = payload.get("cmd_infos")
    if not isinstance(records, list):
        raise DatasetVerificationError("black screen cmd_infos must be a JSON array")
    return len(records)


def _relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DatasetVerificationError("manifest path must be a non-empty string")
    path = Path(value)
    if path.is_absolute():
        raise DatasetVerificationError(f"manifest path must be relative: {value}")
    return path.as_posix()


def _resolve_contained(root: Path, relative_path: str) -> Path:
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise DatasetVerificationError(
            f"manifest path escapes bundle root: {relative_path}"
        ) from exc
    return path


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise DatasetVerificationError(f"missing JSON file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetVerificationError(f"invalid JSON file: {path}: {exc}") from exc


def _nonnegative_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise DatasetVerificationError(f"record count must be an integer: {field}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise DatasetVerificationError(f"record count must be an integer: {field}") from exc
    if parsed < 0:
        raise DatasetVerificationError(f"record count must be nonnegative: {field}")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_root", type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_bundle(args.bundle_root)
    except DatasetVerificationError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS", **asdict(report)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
