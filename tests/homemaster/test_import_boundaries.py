from __future__ import annotations

import ast
from pathlib import Path

HOMEMASTER_ROOT = Path(__file__).resolve().parents[2] / "src" / "homemaster"


def test_homemaster_stage_01_does_not_import_task_brain() -> None:
    offenders: list[str] = []

    for path in sorted(HOMEMASTER_ROOT.glob("**/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "task_brain" or alias.name.startswith("task_brain."):
                        offenders.append(f"{path}:{alias.name}")
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "task_brain" or module.startswith("task_brain."):
                    offenders.append(f"{path}:{module}")

    assert offenders == []
