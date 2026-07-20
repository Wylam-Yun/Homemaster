#!/usr/bin/env python3
"""Validate V1.9 upstream ports against immutable Git source bytes."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.v19_release._common import (
    read_json_object,
    require_exact_keys,
    require_sha256,
)

SCHEMA_VERSION = "homemaster-v1.9-upstream-port-manifest-v1"
MODES = {"V", "A", "H"}
ROOT_KEYS = {"schema_version", "upstream", "ports"}
UPSTREAM_KEYS = {"repo", "commit"}
PORT_KEYS = {
    "id",
    "mode",
    "source",
    "destination",
    "copied_test_ids",
    "mechanical_deltas",
    "homemaster_deltas",
    "upstream_test_gap",
    "characterization_test_ids",
    "sync_policy",
}
SOURCE_KEYS = {"repo", "commit", "path", "symbol", "sha256"}
TEST_GAP_KEYS = {"reason", "search_evidence"}


def validate_manifest(path: Path, *, repo_root: Path) -> dict[str, Any]:
    payload = read_json_object(path, label="upstream port manifest")
    require_exact_keys(payload, ROOT_KEYS, label="upstream port manifest")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported upstream port manifest schema")
    upstream = payload["upstream"]
    ports = payload["ports"]
    if not isinstance(upstream, dict):
        raise ValueError("upstream must be an object")
    if not isinstance(ports, list):
        raise ValueError("ports must be an array")
    require_exact_keys(upstream, UPSTREAM_KEYS, label="upstream")
    upstream_repo = _resolve_repo(repo_root, upstream["repo"])
    upstream_commit = _full_commit(upstream_repo, upstream["commit"])
    if upstream_commit != upstream["commit"]:
        raise ValueError("upstream commit must be a full immutable SHA")

    seen: set[str] = set()
    for index, port in enumerate(ports):
        if not isinstance(port, dict):
            raise ValueError(f"port {index} must be an object")
        require_exact_keys(port, PORT_KEYS, label=f"port {index}")
        port_id = _nonempty_string(port["id"], label=f"port {index} id")
        if port_id in seen:
            raise ValueError(f"duplicate port id: {port_id}")
        seen.add(port_id)
        if port["mode"] not in MODES:
            raise ValueError(f"port {port_id} has invalid mode")
        _validate_port(
            port,
            port_id=port_id,
            repo_root=repo_root,
            default_repo=upstream_repo,
            default_repo_display=upstream["repo"],
            default_commit=upstream_commit,
        )
    return {
        "status": "PASS",
        "upstream_commit": upstream_commit,
        "port_count": len(ports),
    }


def _validate_port(
    port: dict[str, Any],
    *,
    port_id: str,
    repo_root: Path,
    default_repo: Path,
    default_repo_display: str,
    default_commit: str,
) -> None:
    source = port["source"]
    if not isinstance(source, dict):
        raise ValueError(f"port {port_id} source must be an object")
    require_exact_keys(source, SOURCE_KEYS, label=f"port {port_id} source")
    source_repo = _resolve_repo(repo_root, source["repo"])
    if source_repo != default_repo or source["repo"] != default_repo_display:
        raise ValueError(f"port {port_id} source repo differs from locked upstream")
    if source["commit"] != default_commit:
        raise ValueError(f"port {port_id} source commit differs from locked upstream")
    source_path = _repo_relative_path(source["path"], label=f"port {port_id} source path")
    source_symbol = _nonempty_string(
        source["symbol"], label=f"port {port_id} source symbol"
    )
    expected_hash = require_sha256(source["sha256"], label=f"port {port_id} source hash")
    source_bytes = _git_bytes(source_repo, default_commit, source_path)
    if hashlib.sha256(source_bytes).hexdigest() != expected_hash:
        raise ValueError(f"port {port_id} source hash mismatch")
    _validate_python_symbol(
        source_bytes,
        symbol=source_symbol,
        label=f"port {port_id} source symbol",
    )
    _repo_relative_path(port["destination"], label=f"port {port_id} destination")
    _string_list(port["mechanical_deltas"], label=f"port {port_id} mechanical_deltas")
    _string_list(port["homemaster_deltas"], label=f"port {port_id} homemaster_deltas")
    _nonempty_string(port["sync_policy"], label=f"port {port_id} sync_policy")

    copied = _string_list(port["copied_test_ids"], label=f"port {port_id} copied_test_ids")
    characterization = _string_list(
        port["characterization_test_ids"],
        label=f"port {port_id} characterization_test_ids",
    )
    for node_id in copied:
        test_path = _node_path(node_id, label=f"port {port_id} copied test")
        if not test_path.startswith("tests/"):
            raise ValueError(f"port {port_id} copied test must be under tests/")
        _validate_python_node(
            _git_bytes(source_repo, default_commit, test_path),
            node_id=node_id,
            label=f"port {port_id} copied test",
        )
    for node_id in characterization:
        test_path = repo_root / _node_path(node_id, label=f"port {port_id} characterization test")
        if not test_path.is_file():
            raise ValueError(f"port {port_id} characterization test path does not exist")
        _validate_python_node(
            test_path.read_bytes(),
            node_id=node_id,
            label=f"port {port_id} characterization test",
        )

    gap = port["upstream_test_gap"]
    if copied:
        if gap is not None:
            raise ValueError(f"port {port_id} cannot declare a test gap with copied tests")
    else:
        if not isinstance(gap, dict):
            raise ValueError(f"port {port_id} without copied tests requires upstream_test_gap")
        require_exact_keys(gap, TEST_GAP_KEYS, label=f"port {port_id} upstream_test_gap")
        _nonempty_string(gap["reason"], label=f"port {port_id} test-gap reason")
        search = _string_list(
            gap["search_evidence"], label=f"port {port_id} test-gap search_evidence"
        )
        if not search or not characterization:
            raise ValueError(
                f"port {port_id} test gap requires search evidence and characterization tests"
            )


def _resolve_repo(repo_root: Path, value: Any) -> Path:
    display = _nonempty_string(value, label="source repo")
    candidate = Path(display)
    if candidate.is_absolute():
        raise ValueError("source repo must be repository-relative")
    resolved = (repo_root / candidate).resolve(strict=True)
    if not (resolved / ".git").exists():
        raise ValueError(f"source repo is not a Git worktree: {display}")
    return resolved


def _full_commit(repo: Path, value: Any) -> str:
    commit = _nonempty_string(value, label="upstream commit")
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{commit}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_bytes(repo: Path, commit: str, path: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{path}"],
            check=True,
            capture_output=True,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"upstream path missing at locked commit: {path}") from exc


def _repo_relative_path(value: Any, *, label: str) -> str:
    raw = _nonempty_string(value, label=label)
    if "\\" in raw or "\x00" in raw:
        raise ValueError(f"{label} must be canonical POSIX-relative")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be canonical POSIX-relative")
    return path.as_posix()


def _node_path(value: str, *, label: str) -> str:
    if "::" not in value:
        raise ValueError(f"{label} must be a concrete pytest node id")
    return _repo_relative_path(value.split("::", 1)[0], label=label)


def _validate_python_node(source: bytes, *, node_id: str, label: str) -> None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} source is not parseable Python") from exc
    segments = node_id.split("::")[1:]
    if not segments:
        raise ValueError(f"{label} must name a test symbol")
    current: list[ast.stmt] = tree.body
    selected: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef | None = None
    parameter_id: str | None = None
    selected_path: list[str] = []
    for index, raw_segment in enumerate(segments):
        if "[" in raw_segment:
            if index != len(segments) - 1 or not raw_segment.endswith("]"):
                raise ValueError(f"{label} has an invalid parameterized node id")
            segment, parameter_id = raw_segment[:-1].split("[", 1)
            if not segment or not parameter_id:
                raise ValueError(f"{label} has an invalid parameterized node id")
        else:
            segment = raw_segment
        selected_path.append(segment)
        matches = [
            node
            for node in current
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == segment
        ]
        if len(matches) != 1:
            raise ValueError(f"{label} symbol does not exist: {segment}")
        selected = matches[0]
        selected_position = current.index(selected)
        if any(segment in _defined_names(item) for item in current[selected_position + 1 :]):
            raise ValueError(f"{label} symbol is rebound after definition: {segment}")
        if index < len(segments) - 1:
            if not isinstance(selected, ast.ClassDef):
                raise ValueError(f"{label} has an invalid nested node id")
            if not selected.name.startswith("Test") or _class_is_not_collectable(selected):
                raise ValueError(f"{label} class is not collectable by pytest: {selected.name}")
            current = selected.body
        elif not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef)) or not selected.name.startswith(
            "test"
        ):
            raise ValueError(f"{label} function is not collectable by pytest: {segment}")
    if not isinstance(selected, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError(f"{label} must end at a test function")
    if _sets_pytest_disabled(tree.body, selected_path):
        raise ValueError(f"{label} is disabled from pytest collection")
    _validate_parametrize_shapes(selected, label=label)
    if parameter_id is not None:
        declared_ids = _literal_parametrize_ids(selected)
        if declared_ids is None:
            raise ValueError(
                f"{label} parameter id cannot be proven statically; use the function node id"
            )
        if parameter_id not in declared_ids:
            raise ValueError(f"{label} parameter id does not exist: {parameter_id}")


def _validate_python_symbol(source: bytes, *, symbol: str, label: str) -> None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise ValueError(f"{label} source is not parseable Python") from exc
    segments = symbol.split(".")
    if any(not segment.isidentifier() for segment in segments):
        raise ValueError(f"{label} must be a dotted Python identifier")
    current = tree.body
    for index, segment in enumerate(segments):
        matches = [node for node in current if segment in _defined_names(node)]
        if len(matches) != 1:
            raise ValueError(f"{label} does not exist: {symbol}")
        selected = matches[0]
        if index < len(segments) - 1:
            if not isinstance(selected, ast.ClassDef):
                raise ValueError(f"{label} has an invalid nested symbol: {symbol}")
            current = selected.body


def _defined_names(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return {node.name}
    if isinstance(node, ast.Assign) or isinstance(node, ast.AnnAssign) and node.value is not None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {target.id for target in targets if isinstance(target, ast.Name)}
    return set()


def _class_is_not_collectable(node: ast.ClassDef) -> bool:
    if any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name in {"__init__", "__new__"}
        for item in node.body
    ):
        return True
    return any(
        isinstance(item, (ast.Assign, ast.AnnAssign))
        and "__test__" in _defined_names(item)
        and not (isinstance(item.value, ast.Constant) and item.value.value is True)
        for item in node.body
    )


def _sets_pytest_disabled(body: list[ast.stmt], selected_path: list[str]) -> bool:
    for item in body:
        if not isinstance(item, (ast.Assign, ast.AnnAssign)):
            continue
        targets = item.targets if isinstance(item, ast.Assign) else [item.target]
        for target in targets:
            path = _attribute_path(target)
            if (
                path == ["__test__"] or path == [*selected_path, "__test__"]
            ) and not (isinstance(item.value, ast.Constant) and item.value.value is True):
                return True
    return False


def _attribute_path(node: ast.expr) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        parent = _attribute_path(node.value)
        return [*parent, node.attr] if parent is not None else None
    return None


def _parametrize_calls(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for decorator in function.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "parametrize"
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "mark"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "pytest"
        ):
            calls.append(decorator)
    return calls


def _validate_parametrize_shapes(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    label: str,
) -> None:
    for call in _parametrize_calls(function):
        values_node = call.args[1] if len(call.args) > 1 else next(
            (keyword.value for keyword in call.keywords if keyword.arg == "argvalues"),
            None,
        )
        value_count = _static_sequence_length(values_node)
        ids_node = next(
            (keyword.value for keyword in call.keywords if keyword.arg == "ids"),
            None,
        )
        if ids_node is None:
            continue
        ids_count = _static_sequence_length(ids_node)
        if value_count is not None and ids_count is not None and value_count != ids_count:
            raise ValueError(f"{label} parametrize ids length does not match argvalues")


def _static_sequence_length(node: ast.expr | None) -> int | None:
    if isinstance(node, (ast.List, ast.Tuple)):
        return len(node.elts)
    return None


def _literal_parametrize_ids(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str] | None:
    parametrizations = _parametrize_calls(function)
    if len(parametrizations) != 1:
        return None
    ids_keyword = next(
        (keyword.value for keyword in parametrizations[0].keywords if keyword.arg == "ids"),
        None,
    )
    value_node = parametrizations[0].args[1] if len(parametrizations[0].args) > 1 else next(
        (
            keyword.value
            for keyword in parametrizations[0].keywords
            if keyword.arg == "argvalues"
        ),
        None,
    )
    if not isinstance(value_node, (ast.List, ast.Tuple)):
        return None
    decorator_ids: list[str | None]
    if ids_keyword is None:
        decorator_ids = [None] * len(value_node.elts)
    else:
        try:
            raw_ids = ast.literal_eval(ids_keyword)
        except (ValueError, TypeError):
            return None
        if not isinstance(raw_ids, (list, tuple)) or len(raw_ids) != len(value_node.elts):
            return None
        decorator_ids = list(raw_ids)
    actual_ids: list[str] = []
    for row, decorator_id in zip(value_node.elts, decorator_ids, strict=True):
        row_id = _pytest_param_id(row)
        actual_id = row_id if row_id is not None else decorator_id
        if not isinstance(actual_id, str) or not actual_id or not actual_id.isascii():
            return None
        actual_ids.append(actual_id)
    if len(actual_ids) != len(set(actual_ids)):
        return None
    return set(actual_ids)


def _pytest_param_id(node: ast.expr) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "param"
        and isinstance(func.value, ast.Name)
        and func.value.id == "pytest"
    ):
        return None
    id_node = next((keyword.value for keyword in node.keywords if keyword.arg == "id"), None)
    if isinstance(id_node, ast.Constant) and isinstance(id_node.value, str):
        return id_node.value
    return None


def _nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _string_list(value: Any, *, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{label} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} contains duplicates")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        report = validate_manifest(args.manifest, repo_root=args.repo_root.resolve())
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
