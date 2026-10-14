"""RoboCasa Target50 manifest → evaluation cells."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homemaster.robocasa.types import RoboCasaCell


def load_cells(
    manifest_path: Path,
    *,
    splits: tuple[str, ...] = (),
    tasks: tuple[str, ...] = (),
    seeds: tuple[int, ...] = (),
    limit: int | None = None,
) -> list[RoboCasaCell]:
    """Expand the manifest into (task, split, seed) cells with filters applied."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    environment_split = str(manifest.get("environment_split") or "target")
    splits_doc = manifest.get("splits")
    if not isinstance(splits_doc, dict):
        raise ValueError(f"manifest {manifest_path} has no 'splits' mapping")
    requested = splits or tuple(splits_doc)
    cells: list[RoboCasaCell] = []
    for evaluation_split in requested:
        spec = splits_doc.get(evaluation_split)
        if not isinstance(spec, dict):
            raise ValueError(f"manifest has no split named {evaluation_split!r}")
        timeout = float(spec.get("timeout_seconds") or 1800)
        task_names = spec.get("tasks")
        seed_list = spec.get("seeds")
        if not isinstance(task_names, list) or not isinstance(seed_list, list):
            raise ValueError(f"manifest split {evaluation_split!r} is malformed")
        for task_name in task_names:
            if tasks and task_name not in tasks:
                continue
            for seed in seed_list:
                if seeds and int(seed) not in seeds:
                    continue
                cells.append(
                    RoboCasaCell(
                        task_name=str(task_name),
                        split=environment_split,
                        seed=int(seed),
                        timeout_seconds=timeout,
                        evaluation_split=evaluation_split,
                    )
                )
    if limit is not None:
        cells = cells[:limit]
    if not cells:
        raise ValueError("cell selection is empty after filters")
    return cells


def manifest_planner_reference(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    value = manifest.get("planner_reference")
    return dict(value) if isinstance(value, dict) else {}


def manifest_runtime_protocol(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    value = manifest.get("runtime_protocol")
    return dict(value) if isinstance(value, dict) else {}


__all__ = ["load_cells", "manifest_planner_reference", "manifest_runtime_protocol"]
