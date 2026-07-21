from __future__ import annotations

from pathlib import Path

from homemaster.skills.loader import load_skill_registry

_HOME_TOOLS = (
    "task_interpreter",
    "memory_retriever",
    "target_grounder",
    "skill_view",
    "robot_go_to",
    "observe",
    "robot_manipulate",
    "robot_verify",
    "memory_writer",
    "task_summarizer",
)


def _write_skill(root: Path, name: str, description: str, tool: str = "observe") -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\ntool_names: [{tool!r}]\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def test_source_precedence_and_provenance_are_deterministic(tmp_path: Path) -> None:
    user = tmp_path / "user"
    explicit = tmp_path / "explicit"
    repo = tmp_path / "repo"
    cwd = repo / "package"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_skill(user, "deploy", "user")
    _write_skill(repo / ".homemaster" / "skills", "deploy", "project")
    _write_skill(explicit, "deploy", "explicit")

    registry = load_skill_registry(
        cwd=cwd,
        user_dirs=(user,),
        explicit_dirs=(explicit,),
        allowed_tool_names=_HOME_TOOLS,
    )
    skill = registry.get("deploy")

    assert skill is not None
    assert skill.description == "explicit"
    assert [item.source for item in skill.provenance] == ["user", "project", "explicit"]


def test_project_discovery_stops_at_git_root(tmp_path: Path) -> None:
    outside = tmp_path / ".homemaster" / "skills"
    _write_skill(outside, "outside", "outside")
    repo = tmp_path / "repo"
    cwd = repo / "nested"
    cwd.mkdir(parents=True)
    (repo / ".git").mkdir()
    _write_skill(repo / ".homemaster" / "skills", "inside", "inside")

    registry = load_skill_registry(cwd=cwd, allowed_tool_names=_HOME_TOOLS)

    assert registry.get("inside") is not None
    assert registry.get("outside") is None


def test_explicit_source_can_replace_builtin_only_with_recorded_provenance(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit"
    _write_skill(explicit, "fetch_object", "approved replacement")

    registry = load_skill_registry(
        explicit_dirs=(explicit,),
        allowed_tool_names=_HOME_TOOLS,
        allowed_builtin_overrides=("fetch_object",),
    )

    skill = registry.get("fetch_object")
    assert skill is not None
    assert skill.description == "approved replacement"
    assert [item.source for item in skill.provenance] == ["builtin", "explicit"]


def test_explicit_source_cannot_replace_builtin_without_named_authorization(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit"
    _write_skill(explicit, "fetch_object", "unapproved replacement")

    try:
        load_skill_registry(
            explicit_dirs=(explicit,),
            allowed_tool_names=_HOME_TOOLS,
        )
    except ValueError as exc:
        assert "cannot override builtin" in str(exc)
    else:
        raise AssertionError("builtin replacement must require named authorization")


def test_automatic_incompatible_skill_is_reported_without_hiding_builtins(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    _write_skill(user, "unsafe", "unsafe", tool="robot_admin")

    registry = load_skill_registry(
        user_dirs=(user,),
        allowed_tool_names=_HOME_TOOLS,
    )

    assert registry.get("fetch_object") is not None
    assert registry.get("unsafe") is None
    assert len(registry.issues) == 1
    assert registry.issues[0].source == "user"
    assert registry.issues[0].code == "invalid_skill"


def test_frontmatter_invocation_metadata_and_command_alias_are_preserved(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    path = user / "deploy-flow" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: Deploy Flow
description: Release deployment workflow.
tool_names: [observe]
user-invocable: false
disable-model-invocation: true
model: test-model
argument-hint: ENV
---
body
""",
        encoding="utf-8",
    )

    registry = load_skill_registry(
        user_dirs=(user,),
        allowed_tool_names=_HOME_TOOLS,
    )
    skill = registry.get("deploy-flow")

    assert skill is registry.get("Deploy Flow")
    assert skill is not None
    assert skill.command_name == "deploy-flow"
    assert skill.display_name == "Deploy Flow"
    assert skill.user_invocable is False
    assert skill.disable_model_invocation is True
    assert skill.model == "test-model"
    assert skill.argument_hint == "ENV"
    assert registry.get_model_visible("deploy-flow") is None


def test_automatic_alias_conflict_does_not_partially_register_rejected_skill(
    tmp_path: Path,
) -> None:
    user = tmp_path / "user"
    first = _write_skill(user, "first", "first")
    second = _write_skill(user, "second", "second")
    first.write_text(
        first.read_text(encoding="utf-8").replace("---\nbody", "aliases: [shared]\n---\nbody"),
        encoding="utf-8",
    )
    second.write_text(
        second.read_text(encoding="utf-8").replace("---\nbody", "aliases: [shared]\n---\nbody"),
        encoding="utf-8",
    )

    registry = load_skill_registry(
        user_dirs=(user,),
        allowed_tool_names=_HOME_TOOLS,
    )

    assert registry.get("first") is not None
    assert registry.get("shared") is registry.get("first")
    assert registry.get("second") is None
    assert any(issue.code == "invalid_skill" for issue in registry.issues)
