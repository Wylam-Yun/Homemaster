from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_live_python_sources_do_not_import_openharness() -> None:
    roots = (REPO_ROOT / "src" / "homemaster", REPO_ROOT / "scripts", REPO_ROOT / "tests")
    violations: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                if any(name == "openharness" or name.startswith("openharness.") for name in names):
                    violations.append(path.relative_to(REPO_ROOT).as_posix())
    assert violations == []


def test_openharness_package_and_live_upstream_tests_are_absent() -> None:
    assert not (REPO_ROOT / "src" / "openharness").exists()
    assert not (REPO_ROOT / "tests" / "openharness_upstream").exists()
    assert not (REPO_ROOT / "scripts" / "v20" / "generate_upstream_port_manifest.py").exists()
    assert (REPO_ROOT / "plan" / "V2.0" / "archive" / "upstream-port-manifest.json").is_file()
