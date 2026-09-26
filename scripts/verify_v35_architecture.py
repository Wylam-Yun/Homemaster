#!/usr/bin/env python3
"""Deterministic V3.5 architecture and packaging audit.

This is intentionally independent of pytest so a clean checkout can use it as
the final static gate before any live ALFWorld run.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "homemaster"
WORKER = ROOT / "workers" / "alfworld_worker"


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _text_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def audit() -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        findings.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    composition = SRC / "application" / "composition" / "__init__.py"
    check("composition_entry", composition.is_file(), str(composition))
    old_composition = SRC / "cli" / "composition.py"
    check("cli_composition_removed", not old_composition.exists(), str(old_composition))

    offenders: list[str] = []
    for path in _python_files(SRC):
        if path == old_composition:
            continue
        if any(value == "homemaster.cli.composition" for value in _imports(path)):
            offenders.append(str(path.relative_to(ROOT)))
    check("composition_import_direction", not offenders, ", ".join(offenders))

    domain_runtime = [SRC / "application" / "runtime.py", SRC / "agent" / "generic_runtime.py"]
    forbidden_runtime = ("homemaster.alfworld", "AlfredThorEnv", "objectId", "scorer")
    leaks = [
        f"{path.relative_to(ROOT)}:{term}"
        for path in domain_runtime
        for term in forbidden_runtime
        if term in path.read_text(encoding="utf-8")
    ]
    check("generic_runtime_domain_free", not leaks, ", ".join(leaks))

    worker_offenders = []
    for path in _python_files(WORKER):
        if _imports(path) & {"homemaster", "mindmemos"}:
            worker_offenders.append(str(path.relative_to(ROOT)))
    check("worker_import_isolation", not worker_offenders, ", ".join(worker_offenders))

    old_package = SRC / "benchmarking" / "alfworld"
    check("old_alfworld_package_removed", not old_package.exists(), str(old_package))

    forbidden = tuple(
        value
        for value in (
            "Alfred" + "TWEnv",
            "Text" + "World",
            "require_v18_" + "reset",
            "_ENABLE_V18_" + "RESET_TRANSACTION",
            "virtual_" + "navigate",
            "_execute_thor_" + "manipulation",
            "_legacy_execution_" + "feedback",
            "Manipulation" + "Router",
            "LegacyManipulation" + "Executor",
            "from_" + "tool_spec",
            "robot_" + "navigate",
            "robot_" + "find_object",
            "http_worker" + ".py",
            "http_client" + ".py",
        )
    )
    legacy_roots = (SRC, ROOT / "config", ROOT / "scripts")
    legacy_hits = {
        term: [
            str(path.relative_to(ROOT))
            for root in legacy_roots
            for path in _text_files(root)
            if path.name != "verify_v35_architecture.py"
            and term in path.read_text(encoding="utf-8", errors="replace")
        ]
        for term in forbidden
    }
    legacy_hits = {term: paths for term, paths in legacy_hits.items() if paths}
    check("legacy_runtime_surface_removed", not legacy_hits, json.dumps(legacy_hits, ensure_ascii=False))

    required = (
        ROOT / "config" / "alfworld" / "requirements.lock",
        ROOT / "scripts" / "setup-alfworld.sh",
        ROOT / "protocols" / "alfworld-v1.schema.json",
        ROOT / "src" / "homemaster" / "alfworld" / "harness.py",
        ROOT / "src" / "homemaster" / "alfworld" / "backend.py",
        ROOT / "src" / "homemaster" / "alfworld" / "lifecycle.py",
        ROOT / "src" / "homemaster" / "alfworld" / "scene.py",
        ROOT / "src" / "homemaster" / "alfworld" / "actions.py",
        ROOT / "src" / "homemaster" / "alfworld" / "outcomes.py",
        ROOT / "src" / "homemaster" / "alfworld" / "recording.py",
    )
    check("required_v35_artifacts", all(path.is_file() for path in required), ", ".join(str(p) for p in required if not p.is_file()))

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    check("mindmemos_locked_dependency", '"mindmemos"' in pyproject and 'name = "mindmemos"' in lock, "pyproject.toml/uv.lock")

    schema = ROOT / "protocols" / "alfworld-v1.schema.json"
    try:
        payload = json.loads(schema.read_text(encoding="utf-8"))
        response = payload["$defs"]["response"]
        required_response = set(response["required"])
        check(
            "worker_schema_typed_receipt",
            {"protocol", "request_id", "status", "external_return_code", "backend_attempted", "result"}
            <= required_response,
            "response required fields",
        )
    except Exception as exc:
        check("worker_schema_typed_receipt", False, str(exc))

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    findings = audit()
    if args.json:
        print(json.dumps({"status": "PASS" if all(item["status"] == "PASS" for item in findings) else "FAIL", "checks": findings}, indent=2))
    else:
        for item in findings:
            print(f"{item['status']:<4} {item['name']}: {item['detail']}")
    return 0 if all(item["status"] == "PASS" for item in findings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
