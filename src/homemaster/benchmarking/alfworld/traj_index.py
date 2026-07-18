"""Resolve each subtask to an existing ALFWorld traj_data.json.

ALFWorld's env.set_task(traj_data) needs a full traj_data dict (task_type,
pddl_params, scene.object_poses, plan.high_pddl, ...). Rather than synthesizing
one, we reuse an existing ALFRED trial whose task_type+object+parent match the
subtask. This keeps scene object_poses legal and goal_conditions_met correct.

Folder-name convention (valid_seen / valid_unseen):
    <goal_type>-<object>-<mrecep|None>-<parent|toggle>-<floorplan>
    e.g. pick_heat_then_place_in_recep-Apple-None-Fridge-10
         look_at_obj_in_light-RemoteControl-None-FloorLamp-219

For look_at_obj_in_light the fourth field is the toggle (DeskLamp/FloorLamp),
which we accept as the "parent" slot for indexing purposes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from homemaster.benchmarking.alfworld.types import Subtask, Taskset


def _split_root(alfworld_root: Path, split: str) -> Path:
    return alfworld_root / "data" / "json_2.1.1" / split


def _parse_folder(folder: str) -> tuple[str, str, str, str, str] | None:
    """Return (goal_type, object, mrecep, parent_or_toggle, floorplan) or None."""
    parts = folder.split("-")
    if len(parts) < 5:
        return None
    floorplan = parts[-1]
    parent = parts[-2]
    mrecep = parts[-3]
    obj = parts[-4]
    goal_type = "-".join(parts[:-4])
    return goal_type, obj, mrecep, parent, floorplan


def _index_split(split_root: Path) -> dict[tuple[str, str, str, str, str], list[Path]]:
    """Index every trial's traj_data.json by (goal_type, obj, mrecep, parent, floorplan)."""
    index: dict[tuple[str, str, str, str, str], list[Path]] = {}
    if not split_root.is_dir():
        return index
    for folder in os.listdir(split_root):
        folder_path = split_root / folder
        if not folder_path.is_dir():
            continue
        parsed = _parse_folder(folder)
        if parsed is None:
            continue
        goal_type, obj, mrecep, parent, floorplan = parsed
        for trial in os.listdir(folder_path):
            if not trial.startswith("trial_"):
                continue
            traj = folder_path / trial / "traj_data.json"
            if traj.is_file():
                index.setdefault((goal_type, obj, mrecep, parent, floorplan), []).append(traj)
    return index


def _subtask_key(subtask: Subtask, floorplan: int) -> tuple[str, str, str, str, str]:
    """Build the index key for a subtask.

    look_at_obj_in_light stores the toggle in the parent slot of the folder name.
    """
    parent_or_toggle = (
        subtask.toggle
        if subtask.goal_type == "look_at_obj_in_light"
        else subtask.parent
    )
    mrecep = subtask.mrecep or "None"
    return (
        subtask.goal_type,
        subtask.object,
        mrecep,
        parent_or_toggle or "",
        str(floorplan),
    )


def resolve_subtask_trajs(
    tasksets: list[Taskset],
    *,
    alfworld_root: Path,
    split: str,
) -> None:
    """Resolve and assign traj_path on every subtask in place.

    Raises ValueError with the first subtask that has no matching trial, listing
    the missing (floorplan, goal_type, object, parent) so the operator can fix
    the yaml or pick a different split.
    """
    split_root = _split_root(alfworld_root, split)
    index = _index_split(split_root)
    for taskset in tasksets:
        for subtask in taskset.subtasks:
            key = _subtask_key(subtask, taskset.floorplan)
            hits = index.get(key)
            if not hits:
                raise ValueError(
                    f"no ALFRED trial found for subtask "
                    f"{subtask.goal_type}({subtask.object}, "
                    f"{subtask.toggle or subtask.parent}) in FloorPlan{taskset.floorplan} "
                    f"under split {split}; check alfworld_reference.md and the taskset yaml"
                )
            # Deterministic pick: smallest trial path string (no Date/random).
            chosen = sorted(hits)[0]
            object.__setattr__(subtask, "traj_path", chosen)


def load_traj_data(traj_path: Path) -> dict:
    """Load a traj_data.json. Validates the task_type matches the folder name."""
    with traj_path.open("r", encoding="utf-8") as reader:
        payload = json.load(reader)
    if not isinstance(payload, dict):
        raise ValueError(f"traj_data.json is not a mapping: {traj_path}")
    return payload
