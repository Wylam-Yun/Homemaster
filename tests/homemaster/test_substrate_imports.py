"""Import-boundary regression: core-only environment must not pull extras.

Only subprocesses give a clean ``sys.modules`` view, so each case spawns
one interpreter. The boundary package ``homemaster.substrate`` must not
import ``agentscope.app``, ``.tui``, ``.rag``, or other extras-gated
subpackages as a side effect.
"""

from __future__ import annotations

import subprocess
import sys

FORBIDDEN = (
    "agentscope.app",
    "agentscope.tui",
    "agentscope.rag",
    "agentscope.sop",
    "agentscope.workspace",
    "agentscope.realtime",
    "agentscope.classifier",
    "agentscope.a2a",
    "agentscope.tts",
)


def _loaded_modules_after(import_target: str) -> list[str]:
    code = (
        "import sys, importlib\n"
        f"importlib.import_module({import_target!r})\n"
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('agentscope'))))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return [line for line in proc.stdout.splitlines() if line]


def test_substrate_import_pulls_no_extras_subpackages() -> None:
    loaded = _loaded_modules_after("homemaster.substrate")
    pulled = [
        m
        for m in loaded
        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)
    ]
    assert not pulled, f"extras-gated subpackages imported: {pulled}"


def test_plain_agentscope_import_pulls_no_extras_subpackages() -> None:
    loaded = _loaded_modules_after("agentscope")
    pulled = [
        m
        for m in loaded
        if any(m == f or m.startswith(f + ".") for f in FORBIDDEN)
    ]
    assert not pulled, f"extras-gated subpackages imported: {pulled}"


import ast
import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _iter_imports(root: Path):
    """Yield (file, imported_top_level_module) for every .py under root."""
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield path, alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.level:  # relative import — stays inside the tree
                    continue
                yield path, node.module.split(".")[0]


def test_vendored_agentscope_never_imports_homemaster() -> None:
    """The vendored tree must stay upstream-compatible: no HomeMaster (or
    sibling-repo) imports may creep into src/agentscope."""
    offenders = [
        f"{path.relative_to(SRC_ROOT)} imports {mod}"
        for path, mod in _iter_imports(SRC_ROOT / "agentscope")
        if mod in {"homemaster", "mindmemos"}
    ]
    assert not offenders, "vendored agentscope imports project code:\n" + "\n".join(
        offenders
    )


def test_homemaster_only_imports_agentscope_via_substrate() -> None:
    """Design red line: outside substrate/, HomeMaster must not know
    agentscope exists (domain/permissions/devices stay engine-agnostic)."""
    offenders = []
    for path, mod in _iter_imports(SRC_ROOT / "homemaster"):
        if mod != "agentscope":
            continue
        rel = path.relative_to(SRC_ROOT / "homemaster")
        if rel.parts and rel.parts[0] == "substrate":
            continue
        offenders.append(str(path.relative_to(SRC_ROOT)))
    assert not offenders, (
        "agentscope imported outside substrate/:\n" + "\n".join(offenders)
    )


def test_no_machine_specific_absolute_paths_in_src() -> None:
    """Portability invariant: no /Users//home//data1 literals in OUR source.
    The vendored agentscope tree is excluded: it is verbatim upstream, and
    its literals (e.g. an E2B sandbox home, docstring examples) are
    upstream's defaults, not this host's paths."""
    pattern = re.compile(r'["\'](/Users/|/home/|/data1/|/opt/|C:\\\\)')
    offenders = []
    for root in (SRC_ROOT / "homemaster",):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if pattern.search(line):
                    offenders.append(
                        f"{path.relative_to(SRC_ROOT)}:{lineno}"
                    )
    assert not offenders, (
        "machine-specific absolute paths in source:\n" + "\n".join(offenders)
    )
