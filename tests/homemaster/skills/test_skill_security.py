from __future__ import annotations

import os
from pathlib import Path

import pytest

from homemaster.skills.loader import SkillLoader, load_skill_registry


def _skill(root: Path, *, tool: str = "observe") -> Path:
    path = root / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"---\nname: demo\ndescription: demo\ntool_names: [{tool!r}]\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def test_skill_text_cannot_change_the_runtime_tool_view(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _skill(root, tool="robot_admin")

    registry = load_skill_registry(explicit_dirs=(root,))

    skill = registry.get("demo")
    assert skill is not None
    assert "robot_admin" in skill.content


def test_skill_resource_rejects_absolute_parent_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    path = _skill(root)
    resource = path.parent / "reference.txt"
    resource.write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = path.parent / "escape.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    loader = SkillLoader()
    spec = loader.load_from_file(path, root=root)
    assert loader.resolve_resource(spec, "reference.txt") == resource.resolve()
    for unsafe in ("../outside.txt", str(outside), "escape.txt"):
        with pytest.raises(ValueError, match="contained|escapes"):
            loader.resolve_resource(spec, unsafe)


def test_loader_rejects_skill_file_symlink_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    outside_root = tmp_path / "outside"
    outside = _skill(outside_root)
    link_dir = root / "linked"
    link_dir.mkdir(parents=True)
    try:
        os.symlink(outside, link_dir / "SKILL.md")
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(ValueError, match="escapes authorized root"):
        SkillLoader().load_from_file(link_dir / "SKILL.md", root=root)


def test_project_skill_root_symlink_cannot_escape_git_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    cwd = repo / "nested"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    outside = tmp_path / "outside"
    _skill(outside)
    project_parent = repo / ".homemaster"
    project_parent.mkdir()
    try:
        (project_parent / "skills").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    registry = load_skill_registry(cwd=cwd)

    assert registry.get("demo") is None
    assert registry.issues
    assert any(issue.code == "unsafe_root" for issue in registry.issues)
