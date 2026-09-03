#!/usr/bin/env python3
"""Bind one server's Java, Neo4j, Python and memory roots to this checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml


class RuntimeSetupError(RuntimeError):
    """The local HomeMaster runtime is missing or conflicts with a requested binding."""


def _repo_root(value: Path | None) -> Path:
    return (value or Path(__file__).resolve().parents[1]).expanduser().resolve()


def _config_path(repo_root: Path, value: Path | None) -> Path:
    if value is None:
        return repo_root / "config" / "homemaster.yaml"
    return _repo_path(repo_root, value)


def _repo_path(repo_root: Path, value: Path) -> Path:
    value = value.expanduser()
    return (value if value.is_absolute() else repo_root / value).absolute()


def _require_dir(path: Path, label: str) -> Path:
    path = path.expanduser().resolve(strict=True)
    if not path.is_dir():
        raise RuntimeSetupError(f"{label} is not a directory: {path}")
    return path


def _require_executable(path: Path, label: str) -> Path:
    path = path.expanduser().absolute()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeSetupError(f"{label} is not executable: {path}")
    return path


def _validate_inputs(
    *, neo4j_home: Path, java_home: Path, python_executable: Path, validate_python: bool
) -> tuple[Path, Path, Path]:
    neo4j = _require_dir(neo4j_home, "Neo4j home")
    java = _require_dir(java_home, "Java home")
    python = _require_executable(python_executable, "Python executable")
    _require_executable(neo4j / "bin" / "neo4j", "Neo4j executable")
    _require_executable(neo4j / "bin" / "neo4j-admin", "Neo4j admin executable")
    _require_executable(java / "bin" / "java", "Java executable")
    if validate_python:
        probe = subprocess.run(
            [str(python), "-c", "import structlog, yaml"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout).strip().splitlines()[-1:]
            raise RuntimeSetupError(
                f"Python environment cannot import HomeMaster dependencies: "
                f"{detail[0] if detail else 'unknown error'}"
            )
    return neo4j, java, python


def _validate_alfworld_python(path: Path) -> Path:
    python = _require_executable(path, "ALFWorld Python executable")
    probe = subprocess.run(
        [str(python), "-c", "import alfworld, ai2thor"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        detail = (probe.stderr or probe.stdout).strip().splitlines()[-1:]
        raise RuntimeSetupError(
            "ALFWorld Python cannot import alfworld and ai2thor: "
            f"{detail[0] if detail else 'unknown error'}"
        )
    return python


def _validate_alfworld_root(path: Path) -> Path:
    root = _require_dir(path, "ALFWorld root")
    if not (root / "configs" / "base_config.yaml").is_file():
        raise RuntimeSetupError(f"ALFWorld base config is missing: {root}")
    if not (root / "data" / "json_2.1.1").is_dir():
        raise RuntimeSetupError(f"ALFWorld dataset is missing: {root}")
    return root


def _bind(path: Path, target: Path, *, create_directory: bool = False) -> None:
    target = target.expanduser().resolve(strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        current = path.resolve(strict=False)
        if current == target:
            return
        raise RuntimeSetupError(f"runtime binding conflicts with requested target: {path}")
    if path.exists():
        if create_directory and path.is_dir() and not any(path.iterdir()):
            return
        if path.resolve() == target:
            return
        raise RuntimeSetupError(f"runtime binding conflicts with requested target: {path}")
    path.symlink_to(target, target_is_directory=True)


def _ensure_memory(path: Path, memory_home: Path | None) -> None:
    if memory_home is None:
        if path.is_symlink():
            target = path.resolve(strict=True)
            if not target.is_dir():
                raise RuntimeSetupError(f"memory binding target is not a directory: {target}")
            return
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise RuntimeSetupError(f"memory root is not a directory: {path}")
        return
    target = _require_dir(memory_home, "Memory home")
    _bind(path, target)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeSetupError(f"private config is missing: {path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RuntimeSetupError(f"invalid YAML config: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeSetupError(f"config must be a YAML mapping: {path}")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    content = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        raise RuntimeSetupError(f"private config must be mode 0600: {path}")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
    os.replace(temporary, path)


def _portable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    memory = payload.get("memory")
    if not isinstance(memory, dict):
        raise RuntimeSetupError("config.memory must be a mapping")
    neo4j = memory.get("neo4j")
    if not isinstance(neo4j, dict):
        raise RuntimeSetupError("config.memory.neo4j must be a mapping")
    if not str(neo4j.get("password") or "").strip():
        raise RuntimeSetupError("config.memory.neo4j.password must be set before setup")
    memory["data_root"] = "../.runtime/memory"
    neo4j["mode"] = "managed_local"
    neo4j["home"] = "../.runtime/neo4j"
    neo4j["java_home"] = "../.runtime/java"
    memory["neo4j"] = neo4j
    payload["memory"] = memory
    return payload


def _status(repo_root: Path, config_path: Path) -> dict[str, Any]:
    runtime = repo_root / ".runtime"
    expected = {
        "memory": runtime / "memory",
        "neo4j": runtime / "neo4j",
        "java": runtime / "java",
        "venv": runtime / "venv",
        "alfworld_venv": runtime / "alfworld-venv",
        "alfworld": runtime / "alfworld",
    }
    payload = _load_yaml(config_path)
    memory = payload.get("memory")
    neo4j = memory.get("neo4j") if isinstance(memory, dict) else None
    if not isinstance(memory, dict) or not isinstance(neo4j, dict):
        raise RuntimeSetupError("config.memory and config.memory.neo4j must be mappings")
    if neo4j.get("mode") != "managed_local":
        raise RuntimeSetupError("config.memory.neo4j.mode must be managed_local")
    if not str(neo4j.get("password") or "").strip():
        raise RuntimeSetupError("config.memory.neo4j.password must be set")
    configured = {
        "memory": memory.get("data_root"),
        "neo4j": neo4j.get("home"),
        "java": neo4j.get("java_home"),
    }
    required = {
        "memory": ("memory.data_root", "../.runtime/memory"),
        "neo4j": ("memory.neo4j.home", "../.runtime/neo4j"),
        "java": ("memory.neo4j.java_home", "../.runtime/java"),
    }
    for key, (field, value) in required.items():
        if str(configured[key]) != value:
            raise RuntimeSetupError(f"config.{field} must be {value}")
        if not expected[key].exists():
            raise RuntimeSetupError(f"runtime binding is missing: {expected[key]}")
    python = expected["venv"] / "bin" / "python"
    if not python.is_file():
        raise RuntimeSetupError(f"runtime binding is missing: {python}")
    if not (expected["neo4j"] / "bin" / "neo4j").is_file():
        raise RuntimeSetupError(f"Neo4j executable is missing: {expected['neo4j']}")
    if not (expected["java"] / "bin" / "java").is_file():
        raise RuntimeSetupError(f"Java executable is missing: {expected['java']}")
    alfworld_python = expected["alfworld_venv"] / "bin" / "python"
    alfworld_root = expected["alfworld"]
    alfworld_ready = (
        alfworld_python.is_file()
        and (alfworld_root / "configs" / "base_config.yaml").is_file()
        and (alfworld_root / "data" / "json_2.1.1").is_dir()
    )
    return {
        "status": "ready",
        "repo_root": str(repo_root),
        "config": str(config_path),
        "alfworld_ready": alfworld_ready,
    }


def initialize_runtime(
    *,
    repo_root: Path,
    config_path: Path,
    neo4j_home: Path,
    java_home: Path,
    python_executable: Path,
    alfworld_python: Path | None = None,
    alfworld_root: Path | None = None,
    memory_home: Path | None = None,
    validate_python: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root.expanduser().resolve()
    config_path = config_path.expanduser().resolve()
    if (alfworld_python is None) != (alfworld_root is None):
        raise RuntimeSetupError(
            "ALFWorld setup requires both --alfworld-python and --alfworld-root"
        )
    neo4j, java, python = _validate_inputs(
        neo4j_home=neo4j_home,
        java_home=java_home,
        python_executable=python_executable,
        validate_python=validate_python,
    )
    runtime = repo_root / ".runtime"
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    _ensure_memory(runtime / "memory", memory_home)
    _bind(runtime / "neo4j", neo4j)
    _bind(runtime / "java", java)
    _bind(runtime / "venv", python.parent.parent)
    if alfworld_python is not None:
        _bind(runtime / "alfworld-venv", _validate_alfworld_python(alfworld_python).parent.parent)
    if alfworld_root is not None:
        _bind(runtime / "alfworld", _validate_alfworld_root(alfworld_root))
    _write_yaml(config_path, _portable_payload(_load_yaml(config_path)))
    return _status(repo_root, config_path)


def check_runtime(
    *, repo_root: Path, config_path: Path, validate_python: bool = True
) -> dict[str, Any]:
    result = _status(repo_root.expanduser().resolve(), config_path.expanduser().resolve())
    if validate_python:
        python = Path(result["repo_root"]) / ".runtime" / "venv" / "bin" / "python"
        _validate_inputs(
            neo4j_home=Path(result["repo_root"]) / ".runtime" / "neo4j",
            java_home=Path(result["repo_root"]) / ".runtime" / "java",
            python_executable=python,
            validate_python=True,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    setup = subparsers.add_parser("setup")
    setup.add_argument("--repo-root", type=Path, default=None)
    setup.add_argument("--config", type=Path, default=None)
    setup.add_argument("--neo4j-home", type=Path, required=True)
    setup.add_argument("--java-home", type=Path, required=True)
    setup.add_argument("--python", dest="python_executable", type=Path, required=True)
    setup.add_argument("--memory-home", type=Path, default=None)
    setup.add_argument("--alfworld-python", type=Path, default=None)
    setup.add_argument("--alfworld-root", type=Path, default=None)
    setup.add_argument("--no-python-check", action="store_true")
    check = subparsers.add_parser("check")
    check.add_argument("--repo-root", type=Path, default=None)
    check.add_argument("--config", type=Path, default=None)
    check.add_argument("--no-python-check", action="store_true")
    args = parser.parse_args(argv)
    repo_root = _repo_root(args.repo_root)
    config_path = _config_path(repo_root, args.config)
    try:
        if args.command == "setup":
            result = initialize_runtime(
                repo_root=repo_root,
                config_path=config_path,
                neo4j_home=_repo_path(repo_root, args.neo4j_home),
                java_home=_repo_path(repo_root, args.java_home),
                python_executable=_repo_path(repo_root, args.python_executable),
                memory_home=(
                    _repo_path(repo_root, args.memory_home)
                    if args.memory_home is not None
                    else None
                ),
                alfworld_python=(
                    _repo_path(repo_root, args.alfworld_python)
                    if args.alfworld_python is not None
                    else None
                ),
                alfworld_root=(
                    _repo_path(repo_root, args.alfworld_root)
                    if args.alfworld_root is not None
                    else None
                ),
                validate_python=not args.no_python_check,
            )
        else:
            result = check_runtime(
                repo_root=repo_root,
                config_path=config_path,
                validate_python=not args.no_python_check,
            )
    except RuntimeSetupError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
